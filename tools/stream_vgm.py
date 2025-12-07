#!/usr/bin/env python3
"""
stream_vgm.py - Stream VGM/VGZ files to Arduino over serial with error correction

Uses checksummed chunks with ACK/NAK for reliable transmission:
  - Sends: [0x01][length][data...][checksum]
  - Waits for: 'A' (ACK) or 'N' (NAK)
  - Retransmits on NAK

Usage:
    python stream_vgm.py song.vgm --port COM3 --baud 250000
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

# Protocol constants
PROTO_CHUNK = 0x01
PROTO_END = 0x02
PROTO_ACK = ord('A')
PROTO_NAK = ord('N')
PROTO_READY = ord('R')

# Chunk size - must fit in Arduino's 64-byte hardware serial buffer
CHUNK_SIZE = 48

# Default baud rates
BAUD_RATES = {
    'uno': 115200,
    'mega': 250000,
    'teensy': 500000,
}
DEFAULT_BAUD = 500000


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


def preprocess_vgm(data, data_offset):
    """Preprocess VGM - inline DAC bytes, remove data blocks."""
    # Extract PCM data block
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

    # Now inline DAC bytes
    output = bytearray()
    pos = data_offset
    pcm_pos = 0

    while pos < len(data):
        cmd = data[pos]

        if cmd == 0x67:  # Skip data block
            block_size = struct.unpack('<I', data[pos + 3:pos + 7])[0]
            pos += 7 + block_size
        elif cmd == 0x66:  # End
            output.append(cmd)
            break
        elif cmd == 0x50:  # PSG
            output.extend(data[pos:pos + 2])
            pos += 2
        elif cmd in (0x52, 0x53):  # YM2612
            output.extend(data[pos:pos + 3])
            pos += 3
        elif cmd == 0x61:  # Wait N
            output.extend(data[pos:pos + 3])
            pos += 3
        elif cmd in (0x62, 0x63):  # Wait frame
            output.append(cmd)
            pos += 1
        elif 0x70 <= cmd <= 0x7F:  # Short wait
            output.append(cmd)
            pos += 1
        elif 0x80 <= cmd <= 0x8F:  # DAC + wait - inline PCM byte
            output.append(cmd)
            if pcm_data and pcm_pos < len(pcm_data):
                output.append(pcm_data[pcm_pos])
                pcm_pos += 1
            else:
                output.append(0x80)
            pos += 1
        elif cmd == 0xE0:  # PCM seek
            if pos + 5 <= len(data):
                pcm_pos = struct.unpack('<I', data[pos + 1:pos + 5])[0]
            pos += 5
        else:
            output.append(cmd)
            pos += 1

    return bytes(output)


def compute_checksum(length, data):
    """Compute XOR checksum."""
    checksum = length
    for b in data:
        checksum ^= b
    return checksum & 0xFF


def stream_vgm(port, baud, vgm_path, verbose=False):
    """Stream VGM file with checksummed chunks."""

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

    # Preprocess
    print("  Preprocessing...")
    vgm_data = preprocess_vgm(data, header['data_offset'])
    print(f"  Stream size: {len(vgm_data):,} bytes")

    # Connect
    print(f"\nConnecting to {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=2.0, write_timeout=5.0)
    except serial.SerialException as e:
        print(f"ERROR: {e}")
        return False

    time.sleep(2)  # Wait for Arduino reset

    # Read startup messages, but watch for READY signal
    print("Arduino startup:")
    got_ready = False
    start = time.time()
    while time.time() - start < 3:
        if ser.in_waiting:
            b = ser.read(1)[0]
            if b == PROTO_READY:
                got_ready = True
                print("  [Got READY signal]")
            elif b == ord('\n'):
                pass
            elif b == ord('\r'):
                pass
            else:
                # Accumulate text for display
                text = chr(b)
                while ser.in_waiting:
                    c = ser.read(1)[0]
                    if c == PROTO_READY:
                        got_ready = True
                    elif c == ord('\n'):
                        break
                    elif c >= 32:
                        text += chr(c)
                if text.strip():
                    print(f"  {text.strip()}")
        else:
            time.sleep(0.05)

        # If we got ready, we can proceed
        if got_ready:
            break

    if not got_ready:
        print("  [No READY signal yet, sending nudge...]")
        ser.write(bytes([PROTO_END]))  # Send END to reset state
        time.sleep(0.5)
        # Drain and look for READY
        while ser.in_waiting:
            if ser.read(1)[0] == PROTO_READY:
                got_ready = True
                break

    # Stream with checksums
    print("\nStreaming with error correction...")

    pos = 0
    total = len(vgm_data)
    chunks_sent = 0
    retransmits = 0
    start_time = time.time()

    try:
        # Wait for initial READY
        while True:
            if ser.in_waiting:
                if ser.read(1)[0] == PROTO_READY:
                    break
            if time.time() - start_time > 5:
                break

        while pos < total:
            # Send chunk
            chunk_end = min(pos + CHUNK_SIZE, total)
            chunk_data = vgm_data[pos:chunk_end]
            length = len(chunk_data)
            checksum = compute_checksum(length, chunk_data)

            packet = bytes([PROTO_CHUNK, length]) + chunk_data + bytes([checksum])
            ser.write(packet)

            # Wait for ACK/NAK
            while True:
                if ser.in_waiting:
                    response = ser.read(1)[0]
                    if response == PROTO_ACK:
                        pos = chunk_end
                        chunks_sent += 1
                        break
                    elif response == PROTO_NAK:
                        retransmits += 1
                        # Buffer full - wait for READY then resend
                        while not ser.in_waiting or ser.read(1)[0] != PROTO_READY:
                            pass
                        ser.write(packet)
                    # Ignore READY during normal flow

            # Progress
            elapsed = time.time() - start_time
            if elapsed > 0:
                rate = pos / elapsed / 1024
                progress = pos * 100 // total
                bar_width = 30
                filled = bar_width * pos // total
                bar = "=" * filled + "-" * (bar_width - filled)
                print(f"\r  [{bar}] {progress}% {rate:.1f}KB/s retx:{retransmits}  ", end="", flush=True)

        # Send end marker
        ser.write(bytes([PROTO_END]))

        # Wait for final ACK
        timeout = time.time()
        while time.time() - timeout < 2:
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"\n  Arduino: {line}")
            time.sleep(0.1)

        print(f"\n\nDone! Sent {chunks_sent} chunks, {retransmits} retransmits")
        ser.close()
        return True

    except KeyboardInterrupt:
        print("\n\nInterrupted")
        ser.close()
        return False


def main():
    parser = argparse.ArgumentParser(description="Stream VGM to Arduino with error correction")
    parser.add_argument('file', nargs='?', help='VGM/VGZ file')
    parser.add_argument('--port', '-p', help='Serial port')
    parser.add_argument('--baud', '-b', type=int, default=DEFAULT_BAUD, help=f'Baud rate (default: {DEFAULT_BAUD})')
    parser.add_argument('--list-ports', '-l', action='store_true', help='List serial ports')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    if args.list_ports:
        list_ports()
        return 0

    if not args.file:
        print("Usage: python stream_vgm.py <file.vgm> [--port PORT] [--baud BAUD]")
        print("\nRun with --list-ports to see available ports")
        return 1

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        return 1

    port = args.port or find_arduino_port()
    if not port:
        print("ERROR: Could not find Arduino. Use --port to specify.")
        list_ports()
        return 1

    success = stream_vgm(port, args.baud, args.file, args.verbose)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
