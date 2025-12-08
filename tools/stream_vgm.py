#!/usr/bin/env python3
"""
stream_vgm.py - High-performance VGM streaming to Arduino

Uses a compact binary protocol for efficient, real-time playback:
  - Direct binary commands (no text parsing overhead)
  - PING/ACK handshaking for device readiness
  - Output buffering for maximum throughput
  - RLE compression for wait commands
  - DPCM compression for DAC audio (optional)

Protocol: See StreamingProtocol.h for command definitions.

Usage:
    python stream_vgm.py song.vgm --port COM3
    python stream_vgm.py song.vgz --port /dev/ttyUSB0 --baud 250000
"""

import argparse
import gzip
import os
import sys
import struct
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

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

DEFAULT_BAUD = 500000

# Chunk sizes per board type (sent during handshake)
CHUNK_SIZE_UNO = 64
CHUNK_SIZE_MEGA = 128
CHUNK_SIZE_DEFAULT = 64  # Conservative default

CHUNK_HEADER = 0x01
CHUNK_END = 0x02

# Pipelined flow control - send multiple chunks before waiting for ACK
CHUNKS_IN_FLIGHT = 3

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


def commands_to_bytes(commands, loop_index=None):
    """Convert command list to raw bytes.

    Returns: (bytes, loop_byte_offset)
        loop_byte_offset is the byte offset where the loop starts, or None.
    """
    output = bytearray()
    loop_byte_offset = None

    for i, (cmd, args) in enumerate(commands):
        if loop_index is not None and i == loop_index:
            loop_byte_offset = len(output)
        output.append(cmd)
        output.extend(args)

    return bytes(output), loop_byte_offset


# =============================================================================
# Streaming
# =============================================================================

def stream_vgm(port, baud, vgm_path, dac_rate=1, no_dac=False, loop_count=None, verbose=False):
    """Stream VGM file using binary protocol.

    Args:
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

    # Preprocess VGM
    print("  Preprocessing VGM...")
    commands, loop_index = preprocess_vgm(data, header['data_offset'], header['loop_offset'])
    original_cmd_count = len(commands)
    original_bytes = sum(1 + len(args) for cmd, args in commands)

    # Apply DAC processing
    if no_dac:
        commands, loop_index = strip_dac(commands, loop_index)
        print(f"  DAC stripped (FM/PSG only)")
    elif dac_rate > 1:
        commands, loop_index = apply_dac_rate_reduction(commands, dac_rate, loop_index)
        print(f"  DAC rate reduction: 1/{dac_rate} (keeping every {dac_rate}th sample)")

    # Apply wait optimization (merges and RLE)
    commands, loop_index = apply_wait_optimization(commands, loop_index)
    print(f"  Wait optimization: {original_cmd_count} -> {len(commands)} commands")

    # Convert to bytes
    stream_data, loop_byte_offset = commands_to_bytes(commands, loop_index)
    compression_ratio = len(stream_data) / original_bytes * 100 if original_bytes > 0 else 100
    print(f"  Stream size: {len(stream_data):,} bytes ({compression_ratio:.1f}% of original)")

    if loop_byte_offset is not None:
        print(f"  Loop byte offset: {loop_byte_offset:,}")

    # Connect
    print(f"\nConnecting to {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=5.0, write_timeout=5.0)
    except serial.SerialException as e:
        print(f"ERROR: {e}")
        return False

    time.sleep(2)  # Wait for Arduino reset

    # Drain any garbage from reset
    ser.reset_input_buffer()

    # Send PING and wait for ACK+READY handshake
    print("Waiting for Arduino...")
    got_ready = False

    for attempt in range(5):
        if attempt > 0:
            print(f"  Retry {attempt}...")

        ser.reset_input_buffer()
        ser.write(bytes([CMD_PING]))

        # Wait for ACK followed by READY
        got_ack = False
        timeout = time.time()
        while time.time() - timeout < 1.0:
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == CMD_ACK:
                    got_ack = True
                elif b == FLOW_READY and got_ack:
                    got_ready = True
                    print("  Arduino ready!")
                    break
            time.sleep(0.01)

        if got_ready:
            break

    if not got_ready:
        print("\nERROR: No response from Arduino.")
        print("  - Make sure the new firmware is uploaded")
        print(f"  - Check that baud rate matches (using {baud})")
        ser.close()
        return False

    ser.reset_input_buffer()

    # Stream data
    print("\nStreaming...")
    pos = 0
    total = len(stream_data)
    start_time = time.time()
    last_progress = -1
    chunk_size = CHUNK_SIZE_UNO
    retransmits = 0
    pending_chunks = []

    def send_chunk(data):
        """Send a chunk with header, length, data, and checksum."""
        length = len(data)
        checksum = length
        for b in data:
            checksum ^= b
        packet = bytes([CHUNK_HEADER, length]) + data + bytes([checksum & 0xFF])
        ser.write(packet)

    def check_responses():
        """Check for READY/NAK signals. Returns (acks, naks) count."""
        acks = 0
        naks = 0
        while ser.in_waiting:
            b = ser.read(1)[0]
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
            while len(pending_chunks) < CHUNKS_IN_FLIGHT and pos < len(current_data):
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

            # If pipeline full or done sending, wait for responses
            if pending_chunks and (len(pending_chunks) >= CHUNKS_IN_FLIGHT or pos >= len(current_data)):
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

                    # Start next loop iteration immediately
                    # Don't wait for pending_chunks - just keep streaming
                    total_bytes_streamed += len(current_data)
                    loop_number += 1
                    pos = 0
                    last_progress = -1
                    current_data = stream_data_loop
                    print(f"\n  Starting loop {loop_number}...")
                else:
                    # Not looping - send end marker, Arduino will ACK pending chunks
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
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Stream VGM files to Arduino using binary protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python stream_vgm.py song.vgm --port COM3
    python stream_vgm.py song.vgz --port /dev/ttyUSB0
    python stream_vgm.py song.vgm --dac-rate 2   # Half DAC sample rate
    python stream_vgm.py song.vgm --no-dac       # FM/PSG only, no DAC
    python stream_vgm.py song.vgm --loop         # Loop forever
    python stream_vgm.py song.vgm --loop 3       # Loop 3 times

DAC Options (for songs with PCM/DAC audio):
    --dac-rate 2   Skip every other DAC sample (halves commands)
    --dac-rate 4   Keep 1 in 4 DAC samples (quarters commands)
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
    parser.add_argument('--dac-rate', type=int, default=1, choices=[1, 2, 4],
                        help='DAC sample rate divisor (1=full, 2=half, 4=quarter)')
    parser.add_argument('--no-dac', action='store_true',
                        help='Strip all DAC/PCM data (FM/PSG only, smallest size)')
    parser.add_argument('--loop', nargs='?', const=0, type=int, default=None, metavar='N',
                        help='Loop playback: --loop for infinite, --loop N to play N times')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')

    args = parser.parse_args()

    if args.list_ports:
        list_ports()
        return 0

    if not args.file:
        print("Usage: python stream_vgm.py <file.vgm> [options]")
        print("\nOptions:")
        print("  --port PORT      Serial port")
        print("  --baud BAUD      Baud rate (default: 500000)")
        print("  --dac-rate N     DAC sample rate divisor (1, 2, or 4)")
        print("  --no-dac         Strip all DAC data (FM/PSG only)")
        print("  --loop [N]       Loop forever (--loop) or N times (--loop 3)")
        print("  --list-ports     List available serial ports")
        return 1

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        return 1

    port = args.port or find_arduino_port()
    if not port:
        print("ERROR: Could not find Arduino. Use --port to specify.")
        list_ports()
        return 1

    success = stream_vgm(
        port,
        args.baud,
        args.file,
        dac_rate=args.dac_rate,
        no_dac=args.no_dac,
        loop_count=args.loop,
        verbose=args.verbose
    )
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
