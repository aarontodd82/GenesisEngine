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

# DAC commands
CMD_DAC_DATA_BLOCK = 0x80

# Compression commands
CMD_RLE_WAIT_FRAME_1 = 0xC0
CMD_DPCM_BLOCK = 0xC1

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

DEFAULT_BAUD = 230400

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
    }


# =============================================================================
# VGM Processing
# =============================================================================

def preprocess_vgm(data, data_offset):
    """
    Preprocess VGM data:
    1. Extract PCM data block
    2. Inline DAC bytes for 0x80-0x8F commands
    3. Convert to stream of (command_byte, args) tuples
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

    while pos < len(data):
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

    return commands


def apply_wait_optimization(commands):
    """
    Optimize wait commands:
    1. Merge consecutive waits into single CMD_WAIT_FRAMES
    2. Convert small waits to short wait commands (0x70-0x7F)
    3. Use RLE for runs of frame waits
    """
    optimized = []
    i = 0

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
        cmd, args = commands[i]

        # Accumulate consecutive waits
        if is_wait_cmd(cmd):
            total_samples = get_wait_samples(cmd, args)
            j = i + 1

            # Merge consecutive waits
            while j < len(commands) and is_wait_cmd(commands[j][0]):
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

    return optimized


def strip_dac(commands):
    """
    Remove all DAC commands, converting them to waits.
    Preserves timing by keeping the wait portion of 0x80-0x8F commands.
    """
    stripped = []

    for cmd, args in commands:
        # DAC + wait commands (0x80-0x8F)
        if 0x80 <= cmd <= 0x8F:
            # Extract wait portion and convert to short wait
            wait = cmd & 0x0F
            if wait > 0:
                stripped.append((0x70 + wait - 1, b''))  # Short wait (0x70-0x7F)
            # If wait is 0, just skip the command entirely
        else:
            stripped.append((cmd, args))

    return stripped


def apply_dac_compression(commands, dac_rate=1, dac_bits=8):
    """
    Compress DAC data:
    - dac_rate: 1 = full rate, 2 = half rate, 4 = quarter rate (skip samples)
    - dac_bits: 8 = full quality, 6 = reduced, 4 = low quality

    Lower rates and bits = smaller data but lower quality.
    """
    if dac_rate == 1 and dac_bits == 8:
        return commands  # No compression

    compressed = []
    dac_count = 0

    for cmd, args in commands:
        # DAC + wait commands (0x80-0x8F)
        if 0x80 <= cmd <= 0x8F and args:
            dac_count += 1

            # Rate reduction: skip samples
            if dac_rate > 1 and (dac_count % dac_rate) != 1:
                # Skip this sample, but keep the wait
                wait = cmd & 0x0F
                if wait > 0:
                    compressed.append((0x70 + wait - 1, b''))  # Short wait
                continue

            # Bit reduction: reduce precision
            if dac_bits < 8:
                sample = args[0]
                shift = 8 - dac_bits
                sample = (sample >> shift) << shift  # Reduce and restore
                args = bytes([sample])

            compressed.append((cmd, args))
        else:
            compressed.append((cmd, args))

    return compressed


def apply_dpcm_compression(commands):
    """
    Apply DPCM compression to DAC data (0x80 commands with 0 wait).
    Groups consecutive DAC writes and compresses to 4-bit deltas.
    Achieves ~50% compression on DAC data.
    """
    compressed = []
    i = 0

    while i < len(commands):
        cmd, args = commands[i]

        # Look for runs of DAC+wait with 0 wait (0x80 only)
        if cmd == 0x80 and args:
            dac_samples = [args[0]]
            j = i + 1

            # Collect consecutive 0x80 samples (DAC write with no wait)
            while j < len(commands) and commands[j][0] == 0x80 and commands[j][1]:
                dac_samples.append(commands[j][1][0])
                j += 1

            # Need at least 4 samples to make DPCM worthwhile
            if len(dac_samples) >= 4:
                # Encode as DPCM
                dpcm_data = encode_dpcm(dac_samples)
                compressed.append((CMD_DPCM_BLOCK, bytes([len(dpcm_data)]) + dpcm_data))
                i = j
                continue

        compressed.append((cmd, args))
        i += 1

    return compressed


def encode_dpcm(samples):
    """
    Encode samples as DPCM (4-bit deltas packed into bytes).
    Each byte contains two 4-bit signed deltas (+8 to make unsigned).
    """
    last_sample = 0x80  # Start at midpoint
    dpcm_bytes = bytearray()

    i = 0
    while i < len(samples) - 1:
        # First delta
        delta1 = samples[i] - last_sample
        delta1 = max(-8, min(7, delta1))  # Clamp to 4-bit signed
        last_sample = (last_sample + delta1) & 0xFF

        # Second delta
        delta2 = samples[i + 1] - last_sample
        delta2 = max(-8, min(7, delta2))
        last_sample = (last_sample + delta2) & 0xFF

        # Pack two deltas into one byte (add 8 to make unsigned 0-15)
        packed = ((delta1 + 8) << 4) | (delta2 + 8)
        dpcm_bytes.append(packed)

        i += 2

    return bytes(dpcm_bytes)


def commands_to_bytes(commands):
    """Convert command list to raw bytes."""
    output = bytearray()
    for cmd, args in commands:
        output.append(cmd)
        output.extend(args)
    return bytes(output)


# =============================================================================
# Streaming
# =============================================================================

def stream_vgm(port, baud, vgm_path, use_dpcm=False, dac_rate=1, dac_bits=8, no_dac=False, verbose=False):
    """Stream VGM file using binary protocol."""

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

    # Preprocess VGM
    print("  Preprocessing VGM...")
    commands = preprocess_vgm(data, header['data_offset'])
    original_cmd_count = len(commands)
    original_bytes = sum(1 + len(args) for cmd, args in commands)

    # Apply DAC processing
    if no_dac:
        commands = strip_dac(commands)
        print(f"  DAC stripped (FM/PSG only)")
    elif dac_rate > 1 or dac_bits < 8:
        commands = apply_dac_compression(commands, dac_rate, dac_bits)
        print(f"  DAC compression: rate=1/{dac_rate}, bits={dac_bits}")

    # Apply wait optimization (merges and RLE)
    commands = apply_wait_optimization(commands)
    print(f"  Wait optimization: {original_cmd_count} -> {len(commands)} commands")

    # Apply DPCM compression for DAC streams
    if use_dpcm:
        before_dpcm = len(commands)
        commands = apply_dpcm_compression(commands)
        print(f"  DPCM compression: {before_dpcm} -> {len(commands)} commands")

    # Convert to bytes
    stream_data = commands_to_bytes(commands)
    compression_ratio = len(stream_data) / original_bytes * 100 if original_bytes > 0 else 100
    print(f"  Stream size: {len(stream_data):,} bytes ({compression_ratio:.1f}% of original)")

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
        while pos < total or pending_chunks:
            # Send chunks up to pipeline limit
            while len(pending_chunks) < CHUNKS_IN_FLIGHT and pos < total:
                chunk_end = min(pos + chunk_size, total)
                chunk_data = stream_data[pos:chunk_end]
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
                for start_pos, end_pos in chunks_to_resend:
                    send_chunk(stream_data[start_pos:end_pos])
                    pending_chunks.append((start_pos, end_pos))

            # Handle ACKs
            if acks > 0:
                pending_chunks = pending_chunks[acks:]

            # If pipeline full, wait for response
            if len(pending_chunks) >= CHUNKS_IN_FLIGHT:
                acks, naks = wait_for_response(0.1)
                if naks > 0:
                    retransmits += naks
                    chunks_to_resend = pending_chunks[:naks]
                    pending_chunks = pending_chunks[naks:]
                    for start_pos, end_pos in chunks_to_resend:
                        send_chunk(stream_data[start_pos:end_pos])
                        pending_chunks.append((start_pos, end_pos))
                if acks > 0:
                    pending_chunks = pending_chunks[acks:]

            # Progress display
            confirmed_pos = pos - sum(end - start for start, end in pending_chunks)
            progress = confirmed_pos * 100 // total if total > 0 else 100
            if progress != last_progress:
                last_progress = progress
                elapsed = time.time() - start_time
                rate = confirmed_pos / elapsed / 1024 if elapsed > 0 else 0
                print(f"\r  {progress}% {rate:.1f}KB/s q:{len(pending_chunks)} rtx:{retransmits}   ", end="", flush=True)

        # Send end marker and wait for final ACK
        ser.write(bytes([CHUNK_END]))
        wait_for_response(1.0)

        print(f"\n\nStream complete! Waiting for playback...")

        # Monitor for end
        end_wait_start = time.time()
        while time.time() - end_wait_start < 3:
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == FLOW_READY:
                    print("  Playback finished!")
                    break
            time.sleep(0.1)

        elapsed = time.time() - start_time
        print(f"\nStats:")
        print(f"  Total bytes: {total:,}")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Average rate: {total / elapsed / 1024:.1f} KB/s")

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
    python stream_vgm.py song.vgz --port /dev/ttyUSB0 --baud 250000
    python stream_vgm.py song.vgm --dpcm         # Enable DPCM for DAC audio
    python stream_vgm.py song.vgm --dac-rate 2   # Half DAC sample rate
    python stream_vgm.py song.vgm --dac-bits 6   # Reduce DAC to 6-bit

DAC Compression (for songs with PCM/DAC audio):
    --dac-rate 2   Skip every other DAC sample (halves data)
    --dac-rate 4   Keep 1 in 4 DAC samples (quarters data)
    --dac-bits 6   Reduce to 6-bit DAC (slight quality loss)
    --dac-bits 4   Reduce to 4-bit DAC (noticeable quality loss)
    --dpcm         DPCM encoding (50% compression, slight quality loss)
        """
    )

    parser.add_argument('file', nargs='?', help='VGM/VGZ file to stream')
    parser.add_argument('--port', '-p', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--baud', '-b', type=int, default=DEFAULT_BAUD,
                        help=f'Baud rate (default: {DEFAULT_BAUD})')
    parser.add_argument('--list-ports', '-l', action='store_true',
                        help='List available serial ports')
    parser.add_argument('--dpcm', action='store_true',
                        help='Enable DPCM compression for DAC audio')
    parser.add_argument('--dac-rate', type=int, default=1, choices=[1, 2, 4],
                        help='DAC sample rate divisor (1=full, 2=half, 4=quarter)')
    parser.add_argument('--dac-bits', type=int, default=8, choices=[4, 6, 8],
                        help='DAC bit depth (8=full, 6=reduced, 4=low)')
    parser.add_argument('--no-dac', action='store_true',
                        help='Strip all DAC/PCM data (FM/PSG only, smallest size)')
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
        print("  --dpcm           Enable DPCM compression")
        print("  --dac-rate N     DAC sample rate divisor (1, 2, or 4)")
        print("  --dac-bits N     DAC bit depth (4, 6, or 8)")
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
        use_dpcm=args.dpcm,
        dac_rate=args.dac_rate,
        dac_bits=args.dac_bits,
        no_dac=args.no_dac,
        verbose=args.verbose
    )
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
