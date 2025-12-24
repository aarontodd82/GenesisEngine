#!/usr/bin/env python3
"""
stream_vgm_visual.py - VGM streaming with real-time waveform visualization

Based on stream_vgm.py, adds per-channel oscilloscope visualization using
imgui-bundle. The visualization runs in the main thread while streaming
runs in a background thread.

Features:
  - 6 FM channel waveform displays (YM2612)
  - 4 PSG channel waveform displays (SN76489)
  - Real-time register tracking and waveform generation
  - Non-blocking visualization (doesn't affect playback)

Usage:
    python stream_vgm_visual.py song.vgm --port COM3
    python stream_vgm_visual.py song.vgz --port /dev/ttyUSB0
"""

import argparse
import glob
import gzip
import os
import sys
import struct
import time
import threading
import queue

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

# Add local modules to path
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)

# Visualization imports (optional - falls back to CLI mode if unavailable)
_HAS_VISUALIZATION = False
_USE_PYGAME = True  # Set to False to use imgui version

try:
    if _USE_PYGAME:
        from visualizer.app_pygame import VisualizerApp
    else:
        from visualizer.app import VisualizerApp
    from streaming.command_interceptor import CommandInterceptor
    _HAS_VISUALIZATION = True
except ImportError as e:
    print(f"Note: Visualization unavailable ({e}). Running in CLI mode.")
    print("Install imgui-bundle for visualization: pip install imgui-bundle")

# =============================================================================
# Protocol Constants (must match StreamingProtocol.h)
# =============================================================================

# Control commands
CMD_PING = 0x00
CMD_ACK = 0x0F

# Chip write commands
CMD_PSG_WRITE = 0x50
CMD_YM2612_WRITE_A0 = 0x52
CMD_YM2612_WRITE_A1 = 0x53

# Wait commands
CMD_WAIT_FRAMES = 0x61
CMD_WAIT_NTSC = 0x62
CMD_WAIT_PAL = 0x63

# Compression commands
CMD_RLE_WAIT_FRAME_1 = 0xC0

# Stream control
CMD_END_OF_STREAM = 0x66
CMD_PCM_SEEK = 0xE0

# Flow control - MUST match StreamingProtocol.h
# NOTE: These use ASCII control codes to avoid conflicts with VGM commands
# Previously 'R' (0x52) conflicted with CMD_YM2612_WRITE_A0 (0x52)
FLOW_READY = 0x06  # ASCII ACK - ready for more data
FLOW_NAK = 0x15    # ASCII NAK - bad checksum, retry

# Timing
FRAME_SAMPLES_NTSC = 735
FRAME_SAMPLES_PAL = 882

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_BAUD = 1000000

# Board types
BOARD_TYPE_UNO = 1
BOARD_TYPE_MEGA = 2
BOARD_TYPE_OTHER = 3
BOARD_TYPE_TEENSY4 = 4
BOARD_TYPE_ESP32 = 5

# Board-specific settings: (chunk_size, chunks_in_flight, default_dac_rate)
BOARD_SETTINGS = {
    BOARD_TYPE_UNO: (64, 1, 4),      # Uno: 1 chunk at a time, DAC rate 1/4
    BOARD_TYPE_MEGA: (128, 1, 4),    # Mega: 1 chunk at a time, DAC rate 1/4
    BOARD_TYPE_OTHER: (128, 1, 1),   # Other boards: 1 chunk, full DAC
    BOARD_TYPE_TEENSY4: (128, 1, 1), # Teensy 4.x: 1 chunk, full DAC
    BOARD_TYPE_ESP32: (128, 1, 1),   # ESP32: larger chunks now that serial overhead is fixed
}

CHUNK_HEADER = 0x01
CHUNK_END = 0x02

# =============================================================================
# Utility Functions
# =============================================================================

def find_arduino_port():
    """Auto-detect Arduino port."""
    ports = serial.tools.list_ports.comports()
    for port in ports:
        desc = (port.description or "").lower()
        if any(x in desc for x in ['arduino', 'mega', 'uno', 'ch340', 'ch341', 'ftdi']):
            return port.device
    if len(ports) == 1:
        return ports[0].device
    return None


def list_ports():
    """List available serial ports."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports found!")
        return
    print("\nAvailable ports:")
    for port in ports:
        print(f"  {port.device} - {port.description}")


def decompress_vgz(data):
    """Decompress VGZ if needed."""
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    return data


def parse_vgm_header(data):
    """Parse VGM header."""
    if data[:4] != b'Vgm ':
        return None

    version = struct.unpack('<I', data[0x08:0x0C])[0]
    total_samples = struct.unpack('<I', data[0x18:0x1C])[0]

    # Loop offset is relative to 0x1C
    loop_offset_rel = struct.unpack('<I', data[0x1C:0x20])[0]
    loop_offset = (0x1C + loop_offset_rel) if loop_offset_rel else 0

    # Loop samples (how long the loop section is)
    loop_samples = struct.unpack('<I', data[0x20:0x24])[0]

    if version >= 0x150:
        data_offset_rel = struct.unpack('<I', data[0x34:0x38])[0]
        data_offset = 0x34 + data_offset_rel if data_offset_rel else 0x40
    else:
        data_offset = 0x40

    return {
        'version': version,
        'data_offset': data_offset,
        'total_samples': total_samples,
        'duration': total_samples / 44100.0,
        'loop_offset': loop_offset,
        'loop_samples': loop_samples,
    }


# =============================================================================
# VGM Processing
# =============================================================================

def preprocess_vgm(data, data_offset, loop_offset=0):
    """
    Preprocess VGM data:
    1. Extract PCM data block
    2. Inline DAC bytes for 0x80-0x8F commands
    3. Convert to stream of (command_byte, args) tuples
    4. Track loop point index if loop_offset is provided

    Returns: (commands, loop_command_index)
        loop_command_index is the index into commands where the loop starts,
        or None if no loop point.
    """
    # First pass: extract PCM data block
    pcm_data = None
    pos = data_offset

    while pos < len(data):
        cmd = data[pos]
        if cmd == 0x67:  # Data block
            if pos + 7 <= len(data):
                block_type = data[pos + 2]
                block_size = struct.unpack('<I', data[pos + 3:pos + 7])[0]
                if block_type == 0x00:
                    pcm_data = data[pos + 7:pos + 7 + block_size]
                pos += 7 + block_size
            else:
                break
        elif cmd == 0x66:
            break
        elif cmd == 0x50:
            pos += 2
        elif cmd in (0x52, 0x53):
            pos += 3
        elif cmd == 0x61:
            pos += 3
        elif cmd in (0x62, 0x63):
            pos += 1
        elif 0x70 <= cmd <= 0x7F:
            pos += 1
        elif 0x80 <= cmd <= 0x8F:
            pos += 1
        elif cmd == 0xE0:
            pos += 5
        else:
            pos += 1

    # Second pass: generate commands with inlined PCM
    commands = []
    pos = data_offset
    pcm_pos = 0
    loop_command_index = None

    while pos < len(data):
        # Check if this position is the loop point
        if loop_offset and pos == loop_offset and loop_command_index is None:
            loop_command_index = len(commands)

        cmd = data[pos]

        if cmd == 0x67:  # Skip data block
            block_size = struct.unpack('<I', data[pos + 3:pos + 7])[0]
            pos += 7 + block_size

        elif cmd == 0x66:  # End
            commands.append((CMD_END_OF_STREAM, b''))
            break

        elif cmd == 0x50:  # PSG write
            commands.append((CMD_PSG_WRITE, bytes([data[pos + 1]])))
            pos += 2

        elif cmd == 0x52:  # YM2612 Port 0
            commands.append((CMD_YM2612_WRITE_A0, bytes([data[pos + 1], data[pos + 2]])))
            pos += 3

        elif cmd == 0x53:  # YM2612 Port 1
            commands.append((CMD_YM2612_WRITE_A1, bytes([data[pos + 1], data[pos + 2]])))
            pos += 3

        elif cmd == 0x61:  # Wait N samples
            samples = struct.unpack('<H', data[pos + 1:pos + 3])[0]
            commands.append((CMD_WAIT_FRAMES, struct.pack('<H', samples)))
            pos += 3

        elif cmd == 0x62:  # Wait NTSC frame
            commands.append((CMD_WAIT_NTSC, b''))
            pos += 1

        elif cmd == 0x63:  # Wait PAL frame
            commands.append((CMD_WAIT_PAL, b''))
            pos += 1

        elif 0x70 <= cmd <= 0x7F:  # Short wait
            commands.append((cmd, b''))
            pos += 1

        elif 0x80 <= cmd <= 0x8F:  # DAC + wait (inline PCM byte)
            if pcm_data and pcm_pos < len(pcm_data):
                dac_byte = pcm_data[pcm_pos]
                pcm_pos += 1
            else:
                dac_byte = 0x80
            commands.append((cmd, bytes([dac_byte])))
            pos += 1

        elif cmd == 0xE0:  # PCM seek
            if pos + 5 <= len(data):
                pcm_pos = struct.unpack('<I', data[pos + 1:pos + 5])[0]
            pos += 5

        else:
            pos += 1

    return commands, loop_command_index


def apply_wait_optimization(commands, loop_index=None):
    """
    Optimize wait commands:
    1. Merge consecutive waits into single CMD_WAIT_FRAMES
    2. Convert small waits to short wait commands (0x70-0x7F)
    3. Use RLE for runs of frame waits

    Returns: (optimized_commands, new_loop_index)
    """
    optimized = []
    i = 0
    new_loop_index = None

    def get_wait_samples(cmd, args):
        """Extract wait samples from a command."""
        if cmd == CMD_WAIT_NTSC:
            return FRAME_SAMPLES_NTSC
        elif cmd == CMD_WAIT_PAL:
            return FRAME_SAMPLES_PAL
        elif cmd == CMD_WAIT_FRAMES:
            return struct.unpack('<H', args)[0]
        elif 0x70 <= cmd <= 0x7F:
            return (cmd & 0x0F) + 1
        return 0

    def is_wait_cmd(cmd):
        return cmd in (CMD_WAIT_NTSC, CMD_WAIT_PAL, CMD_WAIT_FRAMES) or (0x70 <= cmd <= 0x7F)

    while i < len(commands):
        # Track loop index mapping
        if loop_index is not None and i == loop_index:
            new_loop_index = len(optimized)

        cmd, args = commands[i]

        # Accumulate consecutive waits
        if is_wait_cmd(cmd):
            total_samples = get_wait_samples(cmd, args)
            j = i + 1

            # Merge consecutive waits, but stop if we hit the loop point
            while j < len(commands) and is_wait_cmd(commands[j][0]):
                # Don't merge past the loop point
                if loop_index is not None and j == loop_index:
                    break
                total_samples += get_wait_samples(commands[j][0], commands[j][1])
                j += 1

            # Output optimized wait(s)
            while total_samples > 0:
                if total_samples >= FRAME_SAMPLES_NTSC * 2 and total_samples % FRAME_SAMPLES_NTSC == 0:
                    # Multiple NTSC frames - use RLE
                    frames = total_samples // FRAME_SAMPLES_NTSC
                    if frames <= 255:
                        optimized.append((CMD_RLE_WAIT_FRAME_1, bytes([frames])))
                        total_samples = 0
                    else:
                        optimized.append((CMD_RLE_WAIT_FRAME_1, bytes([255])))
                        total_samples -= 255 * FRAME_SAMPLES_NTSC
                elif total_samples == FRAME_SAMPLES_NTSC:
                    optimized.append((CMD_WAIT_NTSC, b''))
                    total_samples = 0
                elif total_samples == FRAME_SAMPLES_PAL:
                    optimized.append((CMD_WAIT_PAL, b''))
                    total_samples = 0
                elif total_samples <= 16:
                    # Short wait 0x70-0x7F (1-16 samples)
                    optimized.append((0x70 + (total_samples - 1), b''))
                    total_samples = 0
                elif total_samples <= 65535:
                    # General wait
                    optimized.append((CMD_WAIT_FRAMES, struct.pack('<H', total_samples)))
                    total_samples = 0
                else:
                    # Too large - split
                    optimized.append((CMD_WAIT_FRAMES, struct.pack('<H', 65535)))
                    total_samples -= 65535

            i = j
            continue

        optimized.append((cmd, args))
        i += 1

    return optimized, new_loop_index


def strip_dac(commands, loop_index=None):
    """
    Remove all DAC commands, converting them to waits.
    Preserves timing by keeping the wait portion of 0x80-0x8F commands.

    Returns: (stripped_commands, new_loop_index)
    """
    stripped = []
    new_loop_index = None

    for i, (cmd, args) in enumerate(commands):
        if loop_index is not None and i == loop_index:
            new_loop_index = len(stripped)

        # DAC + wait commands (0x80-0x8F)
        if 0x80 <= cmd <= 0x8F:
            # Extract wait portion and convert to short wait
            wait = cmd & 0x0F
            if wait > 0:
                stripped.append((0x70 + wait - 1, b''))  # Short wait (0x70-0x7F)
            # If wait is 0, just skip the command entirely
        else:
            stripped.append((cmd, args))

    return stripped, new_loop_index


def apply_dac_rate_reduction(commands, dac_rate=1, loop_index=None):
    """
    Reduce DAC sample rate by skipping samples.
    - dac_rate: 1 = full rate, 2 = half rate, 4 = quarter rate

    This directly reduces command count, which is the main throughput bottleneck.

    Returns: (compressed_commands, new_loop_index)
    """
    if dac_rate == 1:
        return commands, loop_index  # No reduction

    compressed = []
    dac_count = 0
    new_loop_index = None

    for i, (cmd, args) in enumerate(commands):
        if loop_index is not None and i == loop_index:
            new_loop_index = len(compressed)

        # DAC + wait commands (0x80-0x8F)
        if 0x80 <= cmd <= 0x8F and args:
            dac_count += 1

            # Rate reduction: skip samples
            if (dac_count % dac_rate) != 1:
                # Skip this sample, but keep the wait
                wait = cmd & 0x0F
                if wait > 0:
                    compressed.append((0x70 + wait - 1, b''))  # Short wait
                continue

            compressed.append((cmd, args))
        else:
            compressed.append((cmd, args))

    return compressed, new_loop_index


def detect_chips(commands):
    """Detect which chips are used in the command stream.

    Returns: (has_psg, has_ym2612)
    """
    has_psg = False
    has_ym2612 = False

    for cmd, args in commands:
        if cmd == CMD_PSG_WRITE:
            has_psg = True
        elif cmd in (CMD_YM2612_WRITE_A0, CMD_YM2612_WRITE_A1):
            has_ym2612 = True
        # Also check DAC commands (0x80-0x8F) as YM2612
        elif 0x80 <= cmd <= 0x8F:
            has_ym2612 = True

        if has_psg and has_ym2612:
            break  # Found both, no need to continue

    return has_psg, has_ym2612


def attenuate_psg(commands, attenuation_increase=1, loop_index=None):
    """Increase PSG attenuation (reduce volume) by specified amount.

    PSG attenuation commands have format: 1cc1aaaa
      - cc = channel (0-3)
      - aaaa = attenuation (0=loudest, 15=silent)

    Args:
        commands: List of (cmd, args) tuples
        attenuation_increase: How much to increase attenuation (1-15)
        loop_index: Index of loop point in commands

    Returns: (modified_commands, new_loop_index)
    """
    modified = []
    new_loop_index = None

    for i, (cmd, args) in enumerate(commands):
        if loop_index is not None and i == loop_index:
            new_loop_index = len(modified)

        if cmd == CMD_PSG_WRITE and args:
            psg_byte = args[0]
            # Check if this is an attenuation command (bit 4 set, bit 7 set)
            if (psg_byte & 0x90) == 0x90:
                # Extract current attenuation (bits 0-3)
                current_atten = psg_byte & 0x0F
                # If already silent (15), leave it silent
                # Otherwise increase attenuation but cap at 14 to preserve audibility
                if current_atten == 15:
                    new_atten = 15
                else:
                    new_atten = min(14, current_atten + attenuation_increase)
                # Rebuild the byte
                new_byte = (psg_byte & 0xF0) | new_atten
                modified.append((cmd, bytes([new_byte])))
            else:
                modified.append((cmd, args))
        else:
            modified.append((cmd, args))

    return modified, new_loop_index


def commands_to_bytes(commands, loop_index=None):
    """Convert command list to raw bytes.

    Returns: (bytes, loop_byte_offset, byte_to_samples)
        loop_byte_offset is the byte offset where the loop starts, or None.
        byte_to_samples is a list mapping byte offset to cumulative sample count.
    """
    output = bytearray()
    loop_byte_offset = None
    byte_to_samples = []  # byte_to_samples[i] = samples elapsed at byte i
    cumulative_samples = 0

    for i, (cmd, args) in enumerate(commands):
        if loop_index is not None and i == loop_index:
            loop_byte_offset = len(output)

        # Record sample count at start of this command
        start_pos = len(output)
        output.append(cmd)
        output.extend(args)

        # Fill byte_to_samples for all bytes of this command
        cmd_len = len(output) - start_pos
        for _ in range(cmd_len):
            byte_to_samples.append(cumulative_samples)

        # Calculate samples for wait commands (after the command)
        if cmd == CMD_WAIT_NTSC:
            cumulative_samples += FRAME_SAMPLES_NTSC
        elif cmd == CMD_WAIT_PAL:
            cumulative_samples += FRAME_SAMPLES_PAL
        elif cmd == CMD_WAIT_FRAMES and len(args) >= 2:
            cumulative_samples += args[0] | (args[1] << 8)
        elif 0x70 <= cmd <= 0x7F:
            cumulative_samples += (cmd & 0x0F) + 1
        elif 0x80 <= cmd <= 0x8F:
            cumulative_samples += cmd & 0x0F
        elif cmd == CMD_RLE_WAIT_FRAME_1 and args:
            cumulative_samples += args[0] * FRAME_SAMPLES_NTSC

    return bytes(output), loop_byte_offset, byte_to_samples


# =============================================================================
# Streaming
# =============================================================================

def stream_vgm(port, baud, vgm_path, dac_rate=None, no_dac=False, loop_count=None, verbose=False):
    """Stream VGM file using binary protocol.

    Args:
        dac_rate: None = use board default, 1-4 = override with specific rate
        loop_count: None = no looping, 0 = infinite, N = play N times total
    """

    # Load file
    print(f"\nLoading: {os.path.basename(vgm_path)}")
    with open(vgm_path, 'rb') as f:
        data = f.read()

    original_size = len(data)
    data = decompress_vgz(data)
    if len(data) != original_size:
        print(f"  Decompressed: {original_size:,} -> {len(data):,} bytes")

    header = parse_vgm_header(data)
    if not header:
        print("ERROR: Not a valid VGM file!")
        return False

    print(f"  Duration: {int(header['duration']//60)}:{int(header['duration']%60):02d}")

    # Show loop info
    has_vgm_loop = header['loop_offset'] > 0
    if has_vgm_loop:
        loop_duration = header['loop_samples'] / 44100.0
        print(f"  VGM loop point: {int(loop_duration//60)}:{int(loop_duration%60):02d} loop section")
    elif loop_count is not None:
        print(f"  No VGM loop point (will restart from beginning)")

    # Preprocess VGM (DAC processing deferred until we know board type)
    print("  Preprocessing VGM...")
    commands, loop_index = preprocess_vgm(data, header['data_offset'], header['loop_offset'])
    original_cmd_count = len(commands)
    original_bytes = sum(1 + len(args) for cmd, args in commands)

    # Connect
    print(f"\nConnecting to {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=5.0, write_timeout=5.0)
    except serial.SerialException as e:
        print(f"ERROR: {e}")
        return False

    time.sleep(2)  # Wait for board reset

    # Drain any garbage from reset
    ser.reset_input_buffer()

    # Send PING and wait for ACK+BOARD_TYPE+READY handshake
    print("Waiting for device...")
    got_ready = False
    board_type = None

    for attempt in range(5):
        if attempt > 0:
            print(f"  Retry {attempt}...")

        ser.reset_input_buffer()
        ser.write(bytes([CMD_PING]))

        # Wait for ACK, BOARD_TYPE, then READY
        got_ack = False
        timeout = time.time()
        while time.time() - timeout < 1.0:
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == CMD_ACK:
                    got_ack = True
                elif got_ack and board_type is None and b in BOARD_SETTINGS:
                    board_type = b
                elif b == FLOW_READY and got_ack and board_type is not None:
                    got_ready = True
                    board_name = {1: "Uno", 2: "Mega", 3: "Other", 4: "Teensy 4.x", 5: "ESP32"}.get(board_type, "Unknown")
                    print(f"  Connected! (Board: {board_name})")
                    break
            time.sleep(0.01)

        if got_ready:
            break

    if not got_ready:
        print("\nERROR: No response from device.")
        print("  - Make sure the new firmware is uploaded")
        print(f"  - Check that baud rate matches (using {baud})")
        ser.close()
        return False

    # Get board-specific settings
    chunk_size, chunks_in_flight, default_dac_rate = BOARD_SETTINGS.get(board_type, (64, 2, 1))

    # Detect chips and apply PSG attenuation if both FM and PSG are present
    has_psg, has_ym2612 = detect_chips(commands)
    if has_psg and has_ym2612:
        commands, loop_index = attenuate_psg(commands, attenuation_increase=2, loop_index=loop_index)
        print(f"  PSG attenuated for FM+PSG mix")

    # Apply DAC processing (now that we know board type)
    effective_dac_rate = dac_rate if dac_rate is not None else default_dac_rate
    if no_dac:
        commands, loop_index = strip_dac(commands, loop_index)
        print(f"  DAC stripped (FM/PSG only)")
    elif effective_dac_rate > 1:
        commands, loop_index = apply_dac_rate_reduction(commands, effective_dac_rate, loop_index)
        print(f"  DAC rate reduction: 1/{effective_dac_rate} (keeping every {effective_dac_rate}th sample)")

    # Apply wait optimization (merges and RLE)
    commands, loop_index = apply_wait_optimization(commands, loop_index)
    print(f"  Wait optimization: {original_cmd_count} -> {len(commands)} commands")

    # Convert to bytes
    stream_data, loop_byte_offset, _ = commands_to_bytes(commands, loop_index)
    compression_ratio = len(stream_data) / original_bytes * 100 if original_bytes > 0 else 100
    print(f"  Stream size: {len(stream_data):,} bytes ({compression_ratio:.1f}% of original)")

    if loop_byte_offset is not None:
        print(f"  Loop byte offset: {loop_byte_offset:,}")

    ser.reset_input_buffer()

    # Stream data
    print("\nStreaming...")
    pos = 0
    total = len(stream_data)
    start_time = time.time()
    last_progress = -1
    retransmits = 0
    pending_chunks = []
    chunks_sent = 0  # Debug counter

    def send_chunk(data):
        """Send a chunk with header, length, data, and checksum."""
        nonlocal chunks_sent
        chunks_sent += 1
        length = len(data)
        checksum = length
        for b in data:
            checksum ^= b
        packet = bytes([CHUNK_HEADER, length]) + data + bytes([checksum & 0xFF])
        ser.write(packet)

    all_bytes_received = {}  # Debug: track ALL bytes
    def check_responses():
        """Check for READY/NAK signals. Returns (acks, naks) count."""
        acks = 0
        naks = 0
        while ser.in_waiting:
            b = ser.read(1)[0]
            all_bytes_received[b] = all_bytes_received.get(b, 0) + 1
            if b == FLOW_READY:
                acks += 1
            elif b == FLOW_NAK:
                naks += 1
        return acks, naks

    def wait_for_response(timeout=0.5):
        """Wait for READY or NAK. Returns (acks, naks) count."""
        start = time.time()
        total_acks = 0
        total_naks = 0
        while time.time() - start < timeout:
            acks, naks = check_responses()
            total_acks += acks
            total_naks += naks
            if total_acks > 0 or total_naks > 0:
                return total_acks, total_naks
            time.sleep(0.001)
        return total_acks, total_naks

    try:
        # Determine loop behavior
        # loop_count: None = no looping, 0 = infinite, N = play N times total
        is_looping = loop_count is not None
        plays_remaining = None
        if loop_count is not None:
            if loop_count == 0:
                plays_remaining = -1  # Infinite
            else:
                plays_remaining = loop_count

        # For looping: we need to strip the END_OF_STREAM command from the data
        # and only send it when we're truly done
        if is_looping:
            # Remove trailing END_OF_STREAM (0x66) if present
            if stream_data and stream_data[-1] == CMD_END_OF_STREAM:
                stream_data_main = stream_data[:-1]
            else:
                stream_data_main = stream_data

            # Loop section is from loop point to end (without END_OF_STREAM)
            loop_start = loop_byte_offset if loop_byte_offset else 0
            stream_data_loop = stream_data_main[loop_start:]
        else:
            stream_data_main = stream_data

        # Streaming state
        pos = 0
        total_bytes_streamed = 0
        loop_number = 1
        pending_chunks = []
        last_progress = -1

        # Which data are we currently streaming?
        current_data = stream_data_main
        current_label = ""

        while True:
            # Update label for display
            if is_looping:
                if plays_remaining == -1:
                    current_label = f"[Loop {loop_number}] "
                else:
                    current_label = f"[{loop_number}/{loop_count}] "

            # Send chunks up to pipeline limit
            while len(pending_chunks) < chunks_in_flight and pos < len(current_data):
                chunk_end = min(pos + chunk_size, len(current_data))
                chunk_data = current_data[pos:chunk_end]
                send_chunk(chunk_data)
                pending_chunks.append((pos, chunk_end))
                pos = chunk_end

            # Check for responses
            acks, naks = check_responses()

            # Handle NAKs - retransmit
            if naks > 0:
                retransmits += naks
                chunks_to_resend = pending_chunks[:naks]
                pending_chunks = pending_chunks[naks:]
                for s_pos, e_pos in chunks_to_resend:
                    send_chunk(current_data[s_pos:e_pos])
                    pending_chunks.append((s_pos, e_pos))

            # Handle ACKs
            if acks > 0:
                pending_chunks = pending_chunks[acks:]

            # If pipeline full or done sending or can't send, wait for responses
            if pending_chunks and len(pending_chunks) >= chunks_in_flight:
                acks, naks = wait_for_response(0.1)
                if naks > 0:
                    retransmits += naks
                    chunks_to_resend = pending_chunks[:naks]
                    pending_chunks = pending_chunks[naks:]
                    for s_pos, e_pos in chunks_to_resend:
                        send_chunk(current_data[s_pos:e_pos])
                        pending_chunks.append((s_pos, e_pos))
                if acks > 0:
                    pending_chunks = pending_chunks[acks:]

            # Progress display
            confirmed_pos = pos - sum(end - start for start, end in pending_chunks)
            progress = confirmed_pos * 100 // len(current_data) if len(current_data) > 0 else 100
            if progress != last_progress:
                last_progress = progress
                elapsed = time.time() - start_time
                total_confirmed = total_bytes_streamed + confirmed_pos
                rate = total_confirmed / elapsed / 1024 if elapsed > 0 else 0
                print(f"\r  {current_label}{progress}% {rate:.1f}KB/s q:{len(pending_chunks)} rtx:{retransmits}   ", end="", flush=True)

            # Check if we've finished current data section
            if pos >= len(current_data):
                # Are we looping?
                if is_looping:
                    # Check if we should continue looping
                    if plays_remaining == -1:
                        # Infinite loop - continue
                        pass
                    elif plays_remaining > 1:
                        plays_remaining -= 1
                    else:
                        # Done looping - wait for pending chunks then exit
                        if not pending_chunks:
                            ser.write(bytes([CMD_END_OF_STREAM]))
                            total_bytes_streamed += len(current_data)
                            break
                        continue  # Keep waiting for ACKs

                    # Wait for pending chunks to drain before switching
                    if pending_chunks:
                        continue  # Keep waiting for ACKs

                    # Start next loop iteration
                    total_bytes_streamed += len(current_data)
                    loop_number += 1
                    pos = 0
                    last_progress = -1
                    current_data = stream_data_loop
                    print(f"\n  Starting loop {loop_number}...")
                else:
                    # Not looping - send end marker, device will ACK pending chunks
                    total_bytes_streamed += len(current_data)
                    break

        # Send end marker and wait for final ACK
        ser.write(bytes([CHUNK_END]))
        wait_for_response(1.0)

        print(f"\n\nStream complete! Waiting for playback...")

        # Wait for playback to finish
        end_wait_start = time.time()
        while time.time() - end_wait_start < 600:
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == FLOW_READY:
                    print("  Playback finished!")
                    break
            time.sleep(0.05)

        elapsed = time.time() - start_time
        print(f"\nStats:")
        print(f"  Total bytes streamed: {total_bytes_streamed:,}")
        print(f"  Chunks sent: {chunks_sent}, NAKs: {retransmits} ({retransmits*100//max(chunks_sent,1)}%)")
        print(f"  All bytes received: {dict(sorted([(hex(k), v) for k, v in all_bytes_received.items()]))}")
        if is_looping:
            print(f"  Loops: {loop_number}")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Average rate: {total_bytes_streamed / elapsed / 1024:.1f} KB/s")

        ser.close()
        return True

    except KeyboardInterrupt:
        print("\n\nInterrupted")
        ser.close()
        return False


# =============================================================================
# Interactive Wizard
# =============================================================================

def interactive_wizard():
    """Interactive mode for users who run without arguments."""
    print("=" * 60)
    print("  Genesis Engine VGM Streamer")
    print("=" * 60)
    print()

    # Step 1: Find VGM files in current directory AND script directory
    vgm_files = []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs = ['.']  # Current directory
    if script_dir != os.path.abspath('.'):
        search_dirs.append(script_dir)  # Script directory (if different)

    for search_dir in search_dirs:
        for ext in ['*.vgm', '*.vgz', '*.VGM', '*.VGZ']:
            pattern = os.path.join(search_dir, ext)
            vgm_files.extend(glob.glob(pattern))

    # Remove duplicates and sort
    vgm_files = sorted(set(os.path.normpath(f) for f in vgm_files))

    if not vgm_files:
        print("No VGM/VGZ files found.")
        print()
        print("Searched:")
        print(f"  - Current directory: {os.path.abspath('.')}")
        if len(search_dirs) > 1:
            print(f"  - Script directory:  {script_dir}")
        print()
        print("Please either:")
        print("  1. Copy some .vgm or .vgz files to one of these folders")
        print("  2. Run with a file path: python stream_vgm.py path/to/song.vgm")
        print()
        input("Press Enter to exit...")
        return 1

    # Step 2: Select file
    print(f"Found {len(vgm_files)} VGM file(s):")
    print()
    for i, f in enumerate(vgm_files, 1):
        # Get file size
        size = os.path.getsize(f)
        if size > 1024 * 1024:
            size_str = f"{size / 1024 / 1024:.1f} MB"
        elif size > 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size} bytes"
        print(f"  {i}. {f} ({size_str})")
    print()

    if len(vgm_files) == 1:
        selected_file = vgm_files[0]
        print(f"Selected: {selected_file}")
    else:
        while True:
            try:
                choice = input(f"Select a file (1-{len(vgm_files)}): ").strip()
                if not choice:
                    continue
                idx = int(choice) - 1
                if 0 <= idx < len(vgm_files):
                    selected_file = vgm_files[idx]
                    break
                print(f"Please enter a number between 1 and {len(vgm_files)}")
            except ValueError:
                print("Please enter a number")
            except KeyboardInterrupt:
                print("\nCancelled.")
                return 1

    print()

    # Step 3: Find serial port
    print("Looking for Genesis Engine board...")
    ports = serial.tools.list_ports.comports()

    if not ports:
        print()
        print("ERROR: No serial ports found!")
        print()
        print("Please check:")
        print("  1. Is the board plugged in via USB?")
        print("  2. Is the correct driver installed?")
        print()
        input("Press Enter to exit...")
        return 1

    # Try to auto-detect
    auto_port = find_arduino_port()

    if auto_port:
        print(f"  Found: {auto_port}")
        selected_port = auto_port
    elif len(ports) == 1:
        selected_port = ports[0].device
        print(f"  Found: {selected_port} ({ports[0].description})")
    else:
        print()
        print("Multiple serial ports found:")
        for i, port in enumerate(ports, 1):
            print(f"  {i}. {port.device} - {port.description}")
        print()
        while True:
            try:
                choice = input(f"Select port (1-{len(ports)}): ").strip()
                if not choice:
                    continue
                idx = int(choice) - 1
                if 0 <= idx < len(ports):
                    selected_port = ports[idx].device
                    break
                print(f"Please enter a number between 1 and {len(ports)}")
            except ValueError:
                print("Please enter a number")
            except KeyboardInterrupt:
                print("\nCancelled.")
                return 1

    print()

    # Step 4: Ask about looping
    print()
    print("Playback options:")
    print()
    print("  1. Play once")
    print("  2. Loop forever (Ctrl+C to stop)")
    print()

    loop_count = None
    while True:
        try:
            choice = input("Select option (1-2) [1]: ").strip()
            if not choice or choice == '1':
                loop_count = None
                break
            elif choice == '2':
                loop_count = 0  # 0 = infinite
                break
            print("Please enter 1 or 2")
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 1

    print()
    print("-" * 60)
    print(f"  File: {selected_file}")
    print(f"  Port: {selected_port}")
    print(f"  Loop: {'Forever (Ctrl+C to stop)' if loop_count == 0 else 'Once'}")
    print("-" * 60)
    print()

    try:
        input("Press Enter to start streaming (Ctrl+C to cancel)...")
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1

    # Stream!
    success = stream_vgm(
        selected_port,
        DEFAULT_BAUD,
        selected_file,
        dac_rate=None,  # Use board default
        no_dac=False,
        loop_count=loop_count,
        verbose=False
    )

    if success:
        print()
        # Ask if they want to play another
        try:
            again = input("Play another file? (y/n) [n]: ").strip().lower()
            if again in ('y', 'yes'):
                print()
                return interactive_wizard()  # Restart wizard
        except KeyboardInterrupt:
            pass

    return 0 if success else 1


# =============================================================================
# Visual Streaming
# =============================================================================

class VisualStreamer:
    """
    Manages streaming with visualization.

    SYNCHRONIZED DESIGN:
    - Streaming thread: sends chunks to hardware
    - Visualization thread: processes SAME preprocessed commands at real-time rate
    - Both use the same command data, started at the same time
    - If viz falls behind wall-clock, it skips waits to catch up
    """

    def __init__(self):
        self.app: VisualizerApp = None
        self.interceptor: CommandInterceptor = None
        self.streaming_thread: threading.Thread = None
        self.viz_thread: threading.Thread = None
        self.stop_event = threading.Event()
        self.stream_result = None
        self.commands = None  # Preprocessed commands for visualization
        self.loop_index = None  # Loop point in commands
        self.loop_count = None  # Looping: None=no loop, 0=infinite, N=N times
        self.start_time = None  # When playback started (shared between threads)

    def stream_with_visualization(self, port, baud, vgm_path, dac_rate=None,
                                   no_dac=False, loop_count=None, crt_enabled=True):
        """Stream VGM with visualization."""

        # Store loop setting for viz thread
        self.loop_count = loop_count

        # Create visualizer app
        self.app = VisualizerApp(crt_enabled=crt_enabled)

        # Create command interceptor
        self.interceptor = CommandInterceptor()

        # Connect interceptor callbacks to visualizer
        self.interceptor.on_waveform_update = self.app.update_waveform
        self.interceptor.on_key_change = self.app.set_key_on
        self.interceptor.on_dac_mode_change = self.app.set_dac_mode
        self.interceptor.on_pitch_change = self.app.set_channel_pitch

        # Set up file info
        filename = os.path.basename(vgm_path)
        self.app.set_playback_info(filename, 0)
        self.app.set_status("Loading...")

        # Preprocess VGM for visualization (same processing as streaming)
        self._preprocess_for_viz(vgm_path, dac_rate, no_dac)

        # Start interceptor
        self.interceptor.start()

        # Start streaming in background thread
        self.streaming_thread = threading.Thread(
            target=self._stream_thread,
            args=(port, baud, vgm_path, dac_rate, no_dac, loop_count),
            daemon=True
        )
        self.streaming_thread.start()

        # Run GUI in main thread (blocking)
        try:
            self.app.run(title=f"Genesis Visualizer - {filename}")
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_event.set()
            self.interceptor.stop()
            if self.streaming_thread and self.streaming_thread.is_alive():
                self.streaming_thread.join(timeout=2.0)
            if self.viz_thread and self.viz_thread.is_alive():
                self.viz_thread.join(timeout=2.0)

        return self.stream_result if self.stream_result is not None else False

    def _preprocess_for_viz(self, vgm_path, dac_rate, no_dac):
        """Preprocess VGM for visualization - same processing as streaming."""
        with open(vgm_path, 'rb') as f:
            data = f.read()
        data = decompress_vgz(data)
        header = parse_vgm_header(data)
        if not header:
            return

        self.total_duration = header['duration']

        # Preprocess same as streaming
        commands, loop_index = preprocess_vgm(data, header['data_offset'], header['loop_offset'])

        # Detect chips and apply PSG attenuation (same as streaming)
        has_psg, has_ym2612 = detect_chips(commands)
        if has_psg and has_ym2612:
            commands, loop_index = attenuate_psg(commands, attenuation_increase=2, loop_index=loop_index)

        # Apply same DAC processing as streaming would
        # Note: We don't know board type here, so use provided dac_rate or assume 1
        if no_dac:
            commands, loop_index = strip_dac(commands, loop_index)
        elif dac_rate and dac_rate > 1:
            commands, loop_index = apply_dac_rate_reduction(commands, dac_rate, loop_index)

        # Apply same wait optimization
        commands, loop_index = apply_wait_optimization(commands, loop_index)

        self.commands = commands
        self.loop_index = loop_index

    def _stream_thread(self, port, baud, vgm_path, dac_rate, no_dac, loop_count):
        """Background streaming thread."""
        try:
            self.stream_result = stream_vgm_visual_internal(
                port, baud, vgm_path, dac_rate, no_dac, loop_count,
                chunk_callback=None,
                progress_callback=self._on_progress,
                status_callback=self._on_status,
                stop_event=self.stop_event,
                start_callback=self._on_stream_start
            )
        except Exception as e:
            print(f"Streaming error: {e}")
            self.stream_result = False

    def _on_stream_start(self):
        """Called when hardware streaming actually starts - launch visualization."""
        # Start time for wall-clock sync. The 25ms catch-up mechanism handles
        # any drift from hardware buffering or CPU overhead.
        self.start_time = time.time()
        # Then start viz thread which will use this start_time
        self.viz_thread = threading.Thread(target=self._viz_thread_run, daemon=True)
        self.viz_thread.start()

    def _viz_thread_run(self):
        """
        Visualization thread - processes commands at real-time rate.

        Uses the SAME preprocessed commands as streaming.
        Timing is based on wall-clock from shared start_time.
        If behind, skips commands (not just waits) to truly catch up.
        """
        if not self.commands:
            return

        cmd_idx = 0
        samples_processed = 0

        # Looping state
        is_looping = self.loop_count is not None
        plays_remaining = -1 if self.loop_count == 0 else (self.loop_count or 1)
        loop_start_idx = self.loop_index if self.loop_index is not None else 0

        # Pre-calculate cumulative sample times for each command for fast seeking
        cmd_sample_times = []
        cumulative = 0
        for cmd, args in self.commands:
            cmd_sample_times.append(cumulative)
            if cmd == CMD_WAIT_NTSC:
                cumulative += FRAME_SAMPLES_NTSC
            elif cmd == CMD_WAIT_PAL:
                cumulative += FRAME_SAMPLES_PAL
            elif cmd == CMD_WAIT_FRAMES and len(args) >= 2:
                cumulative += args[0] | (args[1] << 8)
            elif 0x70 <= cmd <= 0x7F:
                cumulative += (cmd & 0x0F) + 1
            elif 0x80 <= cmd <= 0x8F:
                cumulative += cmd & 0x0F
            elif cmd == CMD_RLE_WAIT_FRAME_1 and args:
                cumulative += args[0] * FRAME_SAMPLES_NTSC
        total_samples = cumulative
        loop_start_samples = cmd_sample_times[loop_start_idx] if loop_start_idx < len(cmd_sample_times) else 0

        def find_cmd_for_time(target_samples):
            """Binary search to find command index for a given sample time."""
            lo, hi = 0, len(cmd_sample_times) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if cmd_sample_times[mid] <= target_samples:
                    lo = mid
                else:
                    hi = mid - 1
            return lo

        # Use a local time reference that resets on loop
        loop_time_offset = 0.0  # Added to start_time for current loop iteration

        def do_loop():
            """Handle loop - reset to loop point and adjust time reference."""
            nonlocal cmd_idx, samples_processed, loop_time_offset
            # Calculate how far into the song we should be at loop point
            loop_point_time = loop_start_samples / 44100.0
            # Current wall time
            now = time.time()
            # Adjust offset so (now - start_time - loop_time_offset) = loop_point_time
            loop_time_offset = (now - self.start_time) - loop_point_time
            cmd_idx = loop_start_idx
            samples_processed = loop_start_samples

        while not self.stop_event.is_set():
            # Check if we've reached the end of commands
            if cmd_idx >= len(self.commands):
                if is_looping:
                    if plays_remaining == -1:
                        do_loop()
                        continue
                    elif plays_remaining > 1:
                        plays_remaining -= 1
                        do_loop()
                        continue
                break

            # Bidirectional sync: skip ahead if behind, wait if ahead
            now = time.time()
            elapsed = now - self.start_time - loop_time_offset
            target_samples = int(elapsed * 44100.0)
            drift_samples = target_samples - samples_processed

            # If behind, fast-forward by processing commands without sleeping
            # (don't skip - we need to process through interceptor for emulator state)
            catching_up = drift_samples > 441  # More than 10ms behind

            cmd, args = self.commands[cmd_idx]
            cmd_idx += 1

            # Process command through interceptor
            self.interceptor.process_command(cmd, args)

            # Calculate wait samples for this command
            wait_samples = 0
            if cmd == CMD_WAIT_NTSC:
                wait_samples = FRAME_SAMPLES_NTSC
            elif cmd == CMD_WAIT_PAL:
                wait_samples = FRAME_SAMPLES_PAL
            elif cmd == CMD_WAIT_FRAMES and len(args) >= 2:
                wait_samples = args[0] | (args[1] << 8)
            elif 0x70 <= cmd <= 0x7F:
                wait_samples = (cmd & 0x0F) + 1
            elif 0x80 <= cmd <= 0x8F:
                wait_samples = cmd & 0x0F
            elif cmd == CMD_RLE_WAIT_FRAME_1 and args:
                wait_samples = args[0] * FRAME_SAMPLES_NTSC
            elif cmd == CMD_END_OF_STREAM:
                if is_looping:
                    if plays_remaining == -1:
                        do_loop()
                        continue
                    elif plays_remaining > 1:
                        plays_remaining -= 1
                        do_loop()
                        continue
                break

            if wait_samples > 0:
                samples_processed += wait_samples
                # Skip sleep if catching up, otherwise sync to wall clock
                if not catching_up:
                    target_time = self.start_time + loop_time_offset + (samples_processed / 44100.0)
                    now = time.time()
                    if now < target_time:
                        time.sleep(target_time - now)

    def _on_progress(self, progress: float, elapsed: float, total: float):
        """Called to update progress."""
        if self.app:
            self.app.set_progress(progress, elapsed)
            self.app.total_duration = total

    def _on_status(self, message: str):
        """Called to update status."""
        if self.app:
            self.app.set_status(message)


def stream_vgm_visual_internal(port, baud, vgm_path, dac_rate=None, no_dac=False,
                               loop_count=None, chunk_callback=None,
                               progress_callback=None, status_callback=None,
                               stop_event=None, start_callback=None):
    """
    Stream VGM file with callbacks for visualization.

    This is the same as stream_vgm but with hooks for the visualizer.
    chunk_callback receives raw chunk data for async processing (doesn't block streaming).
    """

    def update_status(msg):
        if status_callback:
            status_callback(msg)
        print(msg)

    # Load file
    update_status(f"Loading: {os.path.basename(vgm_path)}")
    with open(vgm_path, 'rb') as f:
        data = f.read()

    original_size = len(data)
    data = decompress_vgz(data)
    if len(data) != original_size:
        update_status(f"Decompressed: {original_size:,} -> {len(data):,} bytes")

    header = parse_vgm_header(data)
    if not header:
        update_status("ERROR: Not a valid VGM file!")
        return False

    total_duration = header['duration']
    update_status(f"Duration: {int(total_duration//60)}:{int(total_duration%60):02d}")

    # Show loop info
    has_vgm_loop = header['loop_offset'] > 0

    # Preprocess VGM
    update_status("Preprocessing VGM...")
    commands, loop_index = preprocess_vgm(data, header['data_offset'], header['loop_offset'])
    original_cmd_count = len(commands)
    original_bytes = sum(1 + len(args) for cmd, args in commands)

    # Connect
    update_status(f"Connecting to {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=5.0, write_timeout=5.0)
    except serial.SerialException as e:
        update_status(f"ERROR: {e}")
        return False

    time.sleep(2)
    ser.reset_input_buffer()

    # Send PING and wait for ACK+BOARD_TYPE+READY
    update_status("Waiting for device...")
    got_ready = False
    board_type = None

    for attempt in range(5):
        if stop_event and stop_event.is_set():
            ser.close()
            return False

        if attempt > 0:
            update_status(f"Retry {attempt}...")

        ser.reset_input_buffer()
        ser.write(bytes([CMD_PING]))

        got_ack = False
        timeout = time.time()
        while time.time() - timeout < 1.0:
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == CMD_ACK:
                    got_ack = True
                elif got_ack and board_type is None and b in BOARD_SETTINGS:
                    board_type = b
                elif b == FLOW_READY and got_ack and board_type is not None:
                    got_ready = True
                    board_name = {1: "Uno", 2: "Mega", 3: "Other", 4: "Teensy 4.x", 5: "ESP32"}.get(board_type, "Unknown")
                    update_status(f"Connected! (Board: {board_name})")
                    break
            time.sleep(0.01)

        if got_ready:
            break

    if not got_ready:
        update_status("ERROR: No response from device.")
        ser.close()
        return False

    # Get board-specific settings
    chunk_size, chunks_in_flight, default_dac_rate = BOARD_SETTINGS.get(board_type, (64, 2, 1))

    # Detect chips and apply PSG attenuation if both FM and PSG are present
    has_psg, has_ym2612 = detect_chips(commands)
    if has_psg and has_ym2612:
        commands, loop_index = attenuate_psg(commands, attenuation_increase=2, loop_index=loop_index)

    # Apply DAC processing
    effective_dac_rate = dac_rate if dac_rate is not None else default_dac_rate
    if no_dac:
        commands, loop_index = strip_dac(commands, loop_index)
    elif effective_dac_rate > 1:
        commands, loop_index = apply_dac_rate_reduction(commands, effective_dac_rate, loop_index)

    # Apply wait optimization
    commands, loop_index = apply_wait_optimization(commands, loop_index)

    # Convert to bytes
    stream_data, loop_byte_offset, byte_to_samples = commands_to_bytes(commands, loop_index)

    ser.reset_input_buffer()

    # Stream data
    update_status("Streaming...")

    # Signal that streaming is starting (for synchronized visualization)
    if start_callback:
        start_callback()

    pos = 0
    start_time = time.time()
    last_progress = -1
    retransmits = 0
    pending_chunks = []
    chunks_sent = 0

    def send_chunk(data):
        """Send chunk to hardware and queue for visualization (non-blocking)."""
        nonlocal chunks_sent
        chunks_sent += 1
        length = len(data)
        checksum = length
        for b in data:
            checksum ^= b
        packet = bytes([CHUNK_HEADER, length]) + data + bytes([checksum & 0xFF])
        ser.write(packet)

        # Queue chunk for async visualization processing (doesn't block streaming)
        if chunk_callback:
            chunk_callback(bytes(data))

    def check_responses():
        acks = 0
        naks = 0
        while ser.in_waiting:
            b = ser.read(1)[0]
            if b == FLOW_READY:
                acks += 1
            elif b == FLOW_NAK:
                naks += 1
        return acks, naks

    def wait_for_response(timeout_val=0.5):
        start = time.time()
        total_acks = 0
        total_naks = 0
        while time.time() - start < timeout_val:
            acks, naks = check_responses()
            total_acks += acks
            total_naks += naks
            if total_acks > 0 or total_naks > 0:
                return total_acks, total_naks
            time.sleep(0.001)
        return total_acks, total_naks

    try:
        is_looping = loop_count is not None
        plays_remaining = None
        if loop_count is not None:
            plays_remaining = -1 if loop_count == 0 else loop_count

        if is_looping:
            if stream_data and stream_data[-1] == CMD_END_OF_STREAM:
                stream_data_main = stream_data[:-1]
            else:
                stream_data_main = stream_data
            loop_start = loop_byte_offset if loop_byte_offset else 0
            stream_data_loop = stream_data_main[loop_start:]
        else:
            stream_data_main = stream_data

        pos = 0
        total_bytes_streamed = 0
        loop_number = 1
        pending_chunks = []
        current_data = stream_data_main

        while True:
            # Check for stop signal
            if stop_event and stop_event.is_set():
                update_status("Stopped by user")
                break

            # Send chunks
            while len(pending_chunks) < chunks_in_flight and pos < len(current_data):
                chunk_end = min(pos + chunk_size, len(current_data))
                chunk_data = current_data[pos:chunk_end]
                send_chunk(chunk_data)
                pending_chunks.append((pos, chunk_end))
                pos = chunk_end

            # Check for responses
            acks, naks = check_responses()

            if naks > 0:
                retransmits += naks
                chunks_to_resend = pending_chunks[:naks]
                pending_chunks = pending_chunks[naks:]
                for s_pos, e_pos in chunks_to_resend:
                    send_chunk(current_data[s_pos:e_pos])
                    pending_chunks.append((s_pos, e_pos))

            if acks > 0:
                pending_chunks = pending_chunks[acks:]

            if pending_chunks and len(pending_chunks) >= chunks_in_flight:
                acks, naks = wait_for_response(0.1)
                if naks > 0:
                    retransmits += naks
                    chunks_to_resend = pending_chunks[:naks]
                    pending_chunks = pending_chunks[naks:]
                    for s_pos, e_pos in chunks_to_resend:
                        send_chunk(current_data[s_pos:e_pos])
                        pending_chunks.append((s_pos, e_pos))
                if acks > 0:
                    pending_chunks = pending_chunks[acks:]

            # Progress update
            confirmed_pos = pos - sum(end - start for start, end in pending_chunks)
            progress = confirmed_pos * 100 // len(current_data) if len(current_data) > 0 else 100
            if progress != last_progress:
                last_progress = progress
                elapsed = time.time() - start_time
                if progress_callback:
                    progress_callback(progress, elapsed, total_duration)

            # Check if finished
            if pos >= len(current_data):
                if is_looping:
                    if plays_remaining == -1:
                        pass
                    elif plays_remaining > 1:
                        plays_remaining -= 1
                    else:
                        if not pending_chunks:
                            ser.write(bytes([CMD_END_OF_STREAM]))
                            total_bytes_streamed += len(current_data)
                            break
                        continue

                    if pending_chunks:
                        continue

                    total_bytes_streamed += len(current_data)
                    loop_number += 1
                    pos = 0
                    last_progress = -1
                    current_data = stream_data_loop
                    update_status(f"Loop {loop_number}...")
                else:
                    total_bytes_streamed += len(current_data)
                    break

        ser.write(bytes([CHUNK_END]))
        wait_for_response(1.0)

        update_status("Playback complete!")

        # Wait for playback to finish
        end_wait_start = time.time()
        while time.time() - end_wait_start < 600:
            if stop_event and stop_event.is_set():
                break
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == FLOW_READY:
                    break
            time.sleep(0.05)

        ser.close()
        return True

    except KeyboardInterrupt:
        update_status("Interrupted")
        ser.close()
        return False


def run_visual_streamer(port, baud, vgm_path, dac_rate=None, no_dac=False, loop_count=None, crt_enabled=True):
    """Run the visual streamer."""
    if not _HAS_VISUALIZATION:
        print("Visualization not available. Running CLI mode.")
        return stream_vgm(port, baud, vgm_path, dac_rate, no_dac, loop_count)

    streamer = VisualStreamer()
    return streamer.stream_with_visualization(port, baud, vgm_path, dac_rate, no_dac, loop_count, crt_enabled)


def run_offline_visualizer(vgm_path, loop_count=None, crt_enabled=True, audio_enabled=False):
    """Run visualization without hardware - emulator only, optionally with audio."""
    if not _HAS_VISUALIZATION:
        print("ERROR: Visualization not available. Install imgui-bundle.")
        return False

    # Set up audio if requested
    audio_stream = None
    audio_buffer = None
    audio_lock = None
    audio_output_latency = 0  # Will be set from stream.latency if audio enabled
    if audio_enabled:
        try:
            import sounddevice as sd
            import numpy as np

            # Ring buffer for audio samples
            audio_buffer = {'data': np.zeros((44100, 2), dtype=np.float32), 'write_pos': 0, 'read_pos': 0}
            audio_lock = threading.Lock()

            def audio_callback(outdata, frames, time_info, status):
                """Sounddevice callback - pulls audio from ring buffer."""
                with audio_lock:
                    buf = audio_buffer['data']
                    read_pos = audio_buffer['read_pos']
                    write_pos = audio_buffer['write_pos']
                    buf_len = len(buf)

                    # Calculate available samples
                    available = (write_pos - read_pos) % buf_len

                    if available >= frames:
                        # Read from ring buffer (fast path - no wrap)
                        end_pos = read_pos + frames
                        if end_pos <= buf_len:
                            outdata[:] = buf[read_pos:end_pos]
                        else:
                            # Wrap around
                            first_chunk = buf_len - read_pos
                            outdata[:first_chunk] = buf[read_pos:]
                            outdata[first_chunk:] = buf[:frames - first_chunk]
                        audio_buffer['read_pos'] = end_pos % buf_len
                    else:
                        # Not enough data - output silence
                        outdata.fill(0)

            audio_stream = sd.OutputStream(
                samplerate=44100,
                channels=2,
                dtype='float32',
                blocksize=1024,
                callback=audio_callback
            )
            audio_stream.start()
            # Get actual output latency reported by the audio system
            audio_output_latency = audio_stream.latency  # in seconds
            print(f"Audio output enabled (latency: {audio_output_latency*1000:.0f}ms)")
        except ImportError:
            print("WARNING: sounddevice not installed. Run: pip install sounddevice")
            print("Continuing without audio...")
            audio_enabled = False
        except Exception as e:
            print(f"WARNING: Could not initialize audio: {e}")
            print("Continuing without audio...")
            audio_enabled = False

    # Load and parse VGM
    print(f"Loading: {os.path.basename(vgm_path)}")
    with open(vgm_path, 'rb') as f:
        data = f.read()

    data = decompress_vgz(data)
    header = parse_vgm_header(data)
    if not header:
        print("ERROR: Not a valid VGM file!")
        return False

    total_duration = header['duration']
    print(f"Duration: {int(total_duration//60)}:{int(total_duration%60):02d}")

    # Preprocess
    commands, loop_index = preprocess_vgm(data, header['data_offset'], header['loop_offset'])
    print(f"Commands: {len(commands)}")

    # Create visualizer and interceptor
    app = VisualizerApp(crt_enabled=crt_enabled)
    interceptor = CommandInterceptor()

    # Audio buffer latency compensation
    # The audio ring buffer introduces latency, so we delay visualization to match
    if audio_enabled:
        # Total latency = our ring buffer fill time + audio system reported latency
        AUDIO_SAMPLERATE = 44100
        AUDIO_BLOCKSIZE = 1024  # Samples per audio callback
        RING_BUFFER_BLOCKS = 2  # Blocks we buffer before audio callback has data
        ring_buffer_latency = (AUDIO_BLOCKSIZE * RING_BUFFER_BLOCKS) / AUDIO_SAMPLERATE
        # audio_output_latency is set when stream starts (includes driver + OS + hardware)
        AUDIO_LATENCY_SECONDS = ring_buffer_latency + audio_output_latency

        viz_delay_queue = []  # Queue of (timestamp, callback, args)
        viz_delay_lock = threading.Lock()

        def delayed_waveform_update(channel, data):
            """Queue waveform update to be delivered after audio latency delay."""
            deliver_time = time.time() + AUDIO_LATENCY_SECONDS
            with viz_delay_lock:
                viz_delay_queue.append((deliver_time, 'waveform', (channel, data.copy())))

        def delayed_key_change(channel, on):
            deliver_time = time.time() + AUDIO_LATENCY_SECONDS
            with viz_delay_lock:
                viz_delay_queue.append((deliver_time, 'key', (channel, on)))

        def delayed_dac_mode(enabled):
            deliver_time = time.time() + AUDIO_LATENCY_SECONDS
            with viz_delay_lock:
                viz_delay_queue.append((deliver_time, 'dac', (enabled,)))

        def delayed_pitch_change(channel, pitch):
            deliver_time = time.time() + AUDIO_LATENCY_SECONDS
            with viz_delay_lock:
                viz_delay_queue.append((deliver_time, 'pitch', (channel, pitch)))

        def process_delayed_updates():
            """Process any delayed updates that are ready to be delivered."""
            now = time.time()
            with viz_delay_lock:
                while viz_delay_queue and viz_delay_queue[0][0] <= now:
                    _, update_type, args = viz_delay_queue.pop(0)
                    if update_type == 'waveform':
                        app.update_waveform(*args)
                    elif update_type == 'key':
                        app.set_key_on(*args)
                    elif update_type == 'dac':
                        app.set_dac_mode(*args)
                    elif update_type == 'pitch':
                        app.set_channel_pitch(*args)

        interceptor.on_waveform_update = delayed_waveform_update
        interceptor.on_key_change = delayed_key_change
        interceptor.on_dac_mode_change = delayed_dac_mode
        interceptor.on_pitch_change = delayed_pitch_change
    else:
        process_delayed_updates = None  # No delay needed without audio
        interceptor.on_waveform_update = app.update_waveform
        interceptor.on_key_change = app.set_key_on
        interceptor.on_dac_mode_change = app.set_dac_mode
        interceptor.on_pitch_change = app.set_channel_pitch

    # Set up audio callback if enabled
    if audio_enabled and audio_buffer is not None:
        def on_audio(stereo_samples):
            """Write stereo samples to ring buffer."""
            import numpy as np
            with audio_lock:
                buf = audio_buffer['data']
                write_pos = audio_buffer['write_pos']
                buf_len = len(buf)
                n = len(stereo_samples)

                # Write samples to ring buffer (fast numpy slicing)
                end_pos = write_pos + n
                if end_pos <= buf_len:
                    buf[write_pos:end_pos] = stereo_samples
                else:
                    # Wrap around
                    first_chunk = buf_len - write_pos
                    buf[write_pos:] = stereo_samples[:first_chunk]
                    buf[:n - first_chunk] = stereo_samples[first_chunk:]
                audio_buffer['write_pos'] = end_pos % buf_len
        interceptor.on_audio_output = on_audio

    filename = os.path.basename(vgm_path)
    app.set_playback_info(filename, total_duration)
    app.set_status("Offline playback" + (" with audio" if audio_enabled else ""))

    interceptor.start()

    # Playback thread
    stop_event = threading.Event()

    def playback_thread():
        start_time = time.time()
        cmd_idx = 0
        samples_played = 0

        while not stop_event.is_set() and cmd_idx < len(commands):
            # Process any delayed visualization updates (audio latency compensation)
            if process_delayed_updates:
                process_delayed_updates()

            cmd, args = commands[cmd_idx]
            cmd_idx += 1

            # Process command synchronously
            interceptor.process_command(cmd, args)

            # Calculate wait time for timing commands
            wait_samples = 0
            if cmd == CMD_WAIT_NTSC:
                wait_samples = FRAME_SAMPLES_NTSC
            elif cmd == CMD_WAIT_PAL:
                wait_samples = FRAME_SAMPLES_PAL
            elif cmd == CMD_WAIT_FRAMES and len(args) >= 2:
                wait_samples = args[0] | (args[1] << 8)
            elif 0x70 <= cmd <= 0x7F:
                wait_samples = (cmd & 0x0F) + 1
            elif 0x80 <= cmd <= 0x8F:
                wait_samples = cmd & 0x0F
            elif cmd == CMD_RLE_WAIT_FRAME_1 and args:
                wait_samples = args[0] * FRAME_SAMPLES_NTSC
            elif cmd == CMD_END_OF_STREAM:
                # Handle looping
                if loop_count is not None and loop_index is not None:
                    cmd_idx = loop_index
                    continue
                break

            if wait_samples > 0:
                samples_played += wait_samples
                # Update progress
                elapsed = samples_played / 44100.0
                progress = min(100, elapsed / total_duration * 100) if total_duration > 0 else 0
                app.set_progress(progress, elapsed)

                # Real-time delay (audio playback provides its own timing)
                target_time = start_time + elapsed
                sleep_time = target_time - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)

        app.set_status("Playback complete")

    thread = threading.Thread(target=playback_thread, daemon=True)
    thread.start()

    # Run GUI
    try:
        app.run(title=f"Genesis Visualizer (Offline) - {filename}")
    finally:
        stop_event.set()
        interceptor.stop()
        if audio_stream is not None:
            audio_stream.stop()
            audio_stream.close()

    return True


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Stream VGM files to Genesis Engine with waveform visualization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python stream_vgm_visual.py song.vgm --port COM3
    python stream_vgm_visual.py song.vgz --port /dev/ttyUSB0
    python stream_vgm_visual.py song.vgm --no-visual   # CLI mode only
    python stream_vgm_visual.py song.vgm --loop        # Loop forever

Visualization:
    By default, opens a window with 10 oscilloscope displays showing
    real-time waveforms for all FM and PSG channels. Use --no-visual
    for headless/CLI mode.

DAC Options (for songs with PCM/DAC audio):
    --dac-rate N   DAC sample rate divisor (1=full, 2=half, 3=third, 4=quarter)
                   Default: 4 for Uno/Mega, 1 for faster boards
    --no-dac       Strip all DAC data (FM/PSG only)

Looping:
    --loop         Loop forever (Ctrl+C to stop)
    --loop N       Play N times total (e.g., --loop 3 plays 3 times)
        """
    )

    parser.add_argument('file', nargs='?', help='VGM/VGZ file to stream')
    parser.add_argument('--port', '-p', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--baud', '-b', type=int, default=DEFAULT_BAUD,
                        help=f'Baud rate (default: {DEFAULT_BAUD})')
    parser.add_argument('--list-ports', '-l', action='store_true',
                        help='List available serial ports')
    parser.add_argument('--no-visual', action='store_true',
                        help='Disable visualization (CLI mode only)')
    parser.add_argument('--dac-rate', type=int, default=None, choices=[1, 2, 3, 4],
                        help='DAC sample rate divisor (1=full, 2=half, 4=quarter). Default: auto per board')
    parser.add_argument('--no-dac', action='store_true',
                        help='Strip all DAC/PCM data (FM/PSG only, smallest size)')
    parser.add_argument('--loop', nargs='?', const=0, type=int, default=None, metavar='N',
                        help='Loop playback: --loop for infinite, --loop N to play N times')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')
    parser.add_argument('--offline', action='store_true',
                        help='Run visualization without hardware (emulator only)')
    parser.add_argument('--audio', action='store_true',
                        help='Enable audio output in offline mode (requires sounddevice)')
    parser.add_argument('--no-crt', action='store_true',
                        help='Disable CRT shader effects (scanlines, phosphor, etc.)')

    args = parser.parse_args()

    if args.list_ports:
        list_ports()
        return 0

    if not args.file:
        # No file specified - run interactive wizard
        return interactive_wizard()

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        return 1

    # Offline mode - no hardware needed
    if args.offline:
        success = run_offline_visualizer(
            args.file,
            loop_count=args.loop,
            crt_enabled=not args.no_crt,
            audio_enabled=args.audio
        )
        return 0 if success else 1

    port = args.port or find_arduino_port()
    if not port:
        print("ERROR: Could not find device. Use --port to specify.")
        list_ports()
        return 1

    # Choose between visual and CLI mode
    if args.no_visual or not _HAS_VISUALIZATION:
        # CLI mode
        success = stream_vgm(
            port,
            args.baud,
            args.file,
            dac_rate=args.dac_rate,
            no_dac=args.no_dac,
            loop_count=args.loop,
            verbose=args.verbose
        )
    else:
        # Visual mode
        success = run_visual_streamer(
            port,
            args.baud,
            args.file,
            dac_rate=args.dac_rate,
            no_dac=args.no_dac,
            loop_count=args.loop,
            crt_enabled=not args.no_crt
        )

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
