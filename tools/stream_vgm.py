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

# Flow control
FLOW_READY = ord('R')
FLOW_NAK = ord('N')

# Timing
FRAME_SAMPLES_NTSC = 735
FRAME_SAMPLES_PAL = 882

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_BAUD = 500000
CHUNK_SIZE = 48  # Must match Arduino's CHUNK_SIZE
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


def apply_rle_compression(commands):
    """
    Apply RLE compression to consecutive wait commands.
    Compresses runs of CMD_WAIT_NTSC (0x62) into CMD_RLE_WAIT_FRAME_1.
    """
    compressed = []
    i = 0

    while i < len(commands):
        cmd, args = commands[i]

        # Look for runs of NTSC frame waits
        if cmd == CMD_WAIT_NTSC:
            count = 1
            while (i + count < len(commands) and
                   commands[i + count][0] == CMD_WAIT_NTSC and
                   count < 255):
                count += 1

            if count >= 2:
                # Use RLE encoding
                compressed.append((CMD_RLE_WAIT_FRAME_1, bytes([count])))
                i += count
                continue

        compressed.append((cmd, args))
        i += 1

    return compressed


def apply_dpcm_compression(commands, use_dpcm=True):
    """
    Apply DPCM compression to DAC data (0x80-0x8F commands).
    Groups consecutive DAC writes and compresses to 4-bit deltas.
    """
    if not use_dpcm:
        return commands

    compressed = []
    i = 0

    while i < len(commands):
        cmd, args = commands[i]

        # Look for runs of DAC+wait with 0 wait (0x80)
        if cmd == 0x80 and args:
            dac_samples = [args[0]]
            j = i + 1

            # Collect consecutive DAC samples
            while j < len(commands) and commands[j][0] == 0x80 and commands[j][1]:
                dac_samples.append(commands[j][1][0])
                j += 1

            # Need at least 4 samples to make DPCM worthwhile (2 packed bytes)
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

def stream_vgm(port, baud, vgm_path, use_rle=True, use_dpcm=False, verbose=False):
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

    # Apply compression
    if use_rle:
        commands = apply_rle_compression(commands)
        print(f"  RLE compression: {original_cmd_count} -> {len(commands)} commands")

    if use_dpcm:
        before_dpcm = len(commands)
        commands = apply_dpcm_compression(commands)
        print(f"  DPCM compression: {before_dpcm} -> {len(commands)} commands")

    # Convert to bytes
    stream_data = commands_to_bytes(commands)
    print(f"  Stream size: {len(stream_data):,} bytes")

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

    # Wait for READY signal
    print("Waiting for Arduino...")
    got_ready = False
    start = time.time()

    while time.time() - start < 5:
        if ser.in_waiting:
            b = ser.read(1)[0]
            if verbose:
                print(f"  [0x{b:02X} '{chr(b) if 32 <= b < 127 else '?'}']")
            if b == FLOW_READY:
                got_ready = True
                print("  Arduino ready! (got READY)")
                break
            elif b == CMD_ACK:
                got_ready = True
                print("  Arduino ready! (got ACK)")
                break
        else:
            time.sleep(0.01)

    if not got_ready:
        # Try PING handshake multiple times
        for attempt in range(3):
            print(f"  Sending PING (attempt {attempt + 1})...")
            ser.reset_input_buffer()
            ser.write(bytes([CMD_PING]))
            time.sleep(0.2)

            timeout = time.time()
            while time.time() - timeout < 0.5:
                if ser.in_waiting:
                    b = ser.read(1)[0]
                    if verbose:
                        print(f"  [0x{b:02X} '{chr(b) if 32 <= b < 127 else '?'}']")
                    if b == CMD_ACK:
                        got_ready = True
                        print("  Got ACK!")
                        break
                    elif b == FLOW_READY:
                        got_ready = True
                        print("  Got READY!")
                        break
                time.sleep(0.01)

            if got_ready:
                break

    if not got_ready:
        print("\nERROR: No response from Arduino.")
        print("  - Make sure the new firmware is uploaded")
        print("  - Check that baud rate matches (Arduino: 250000)")
        print(f"  - Current baud: {baud}")
        ser.close()
        return False

    # Stream data with chunked protocol
    print("\nStreaming...")
    pos = 0
    total = len(stream_data)
    start_time = time.time()
    last_progress = -1
    retransmits = 0

    def send_chunk(data):
        """Send a chunk with header, length, data, and checksum."""
        length = len(data)
        checksum = length
        for b in data:
            checksum ^= b
        packet = bytes([CHUNK_HEADER, length]) + data + bytes([checksum & 0xFF])
        ser.write(packet)

    def wait_for_ready():
        """Wait for READY signal from Arduino. Returns False on timeout/NAK."""
        nonlocal retransmits
        while True:
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == FLOW_READY:
                    return True
                elif b == FLOW_NAK:
                    retransmits += 1
                    return False  # Need to resend
                elif verbose:
                    print(f"\n  [0x{b:02X}]", end="")
            # Brief sleep to avoid busy-waiting
            time.sleep(0.0001)

    try:
        while pos < total:
            # Prepare chunk
            chunk_end = min(pos + CHUNK_SIZE, total)
            chunk_data = stream_data[pos:chunk_end]

            # Send chunk and wait for ACK
            send_chunk(chunk_data)

            if wait_for_ready():
                pos = chunk_end  # Move forward on success
            # On NAK, we'll resend the same chunk next iteration

            # Progress display (update every 1%)
            progress = pos * 100 // total
            if progress != last_progress:
                last_progress = progress
                elapsed = time.time() - start_time
                if elapsed > 0:
                    rate = pos / elapsed / 1024
                    bar_width = 30
                    filled = bar_width * pos // total
                    bar = "=" * filled + "-" * (bar_width - filled)
                    print(f"\r  [{bar}] {progress}% {rate:.1f}KB/s rtx:{retransmits}", end="", flush=True)

        # Send end marker
        ser.write(bytes([CHUNK_END]))
        wait_for_ready()

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
    python stream_vgm.py song.vgm --no-rle  # Disable RLE compression
    python stream_vgm.py song.vgm --dpcm    # Enable DPCM for DAC audio
        """
    )

    parser.add_argument('file', nargs='?', help='VGM/VGZ file to stream')
    parser.add_argument('--port', '-p', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--baud', '-b', type=int, default=DEFAULT_BAUD,
                        help=f'Baud rate (default: {DEFAULT_BAUD})')
    parser.add_argument('--list-ports', '-l', action='store_true',
                        help='List available serial ports')
    parser.add_argument('--no-rle', action='store_true',
                        help='Disable RLE compression')
    parser.add_argument('--dpcm', action='store_true',
                        help='Enable DPCM compression for DAC audio')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')

    args = parser.parse_args()

    if args.list_ports:
        list_ports()
        return 0

    if not args.file:
        print("Usage: python stream_vgm.py <file.vgm> [options]")
        print("\nOptions:")
        print("  --port PORT    Serial port")
        print("  --baud BAUD    Baud rate (default: 250000)")
        print("  --no-rle       Disable RLE compression")
        print("  --dpcm         Enable DPCM compression")
        print("  --list-ports   List available serial ports")
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
        use_rle=not args.no_rle,
        use_dpcm=args.dpcm,
        verbose=args.verbose
    )
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
