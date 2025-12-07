#!/usr/bin/env python3
"""
vgm_analyze.py - Analyze VGM file structure and size breakdown

Usage:
    python vgm_analyze.py input.vgm
    python vgm_analyze.py input.vgz
"""

import argparse
import gzip
import struct
from collections import defaultdict


def decompress_vgz(data: bytes) -> bytes:
    """Decompress VGZ (gzip-compressed VGM) data."""
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    return data


def analyze_vgm(filepath: str):
    """Analyze VGM file and print size breakdown."""

    with open(filepath, 'rb') as f:
        raw_data = f.read()

    compressed_size = len(raw_data)
    data = decompress_vgz(raw_data)
    total_size = len(data)

    print(f"File: {filepath}")
    print(f"Compressed size: {compressed_size:,} bytes ({compressed_size/1024:.1f} KB)")
    print(f"Uncompressed size: {total_size:,} bytes ({total_size/1024:.1f} KB)")
    print(f"Compression ratio: {compressed_size/total_size*100:.1f}%")
    print()

    # Parse header
    if data[:4] != b'Vgm ':
        print("ERROR: Not a valid VGM file")
        return

    version = struct.unpack('<I', data[0x08:0x0C])[0]
    total_samples = struct.unpack('<I', data[0x18:0x1C])[0]
    gd3_offset = struct.unpack('<I', data[0x14:0x18])[0]

    # Data offset
    if version >= 0x150:
        data_offset_rel = struct.unpack('<I', data[0x34:0x38])[0]
        data_offset = 0x34 + data_offset_rel if data_offset_rel else 0x40
    else:
        data_offset = 0x40

    # GD3 absolute offset
    gd3_abs = 0x14 + gd3_offset if gd3_offset else 0

    print(f"VGM Version: {(version >> 8) & 0xFF}.{version & 0xFF:02X}")
    print(f"Duration: {total_samples / 44100:.2f} seconds")
    print(f"Header size: {data_offset} bytes")
    print()

    # Analyze commands
    pos = data_offset
    command_counts = defaultdict(int)
    command_bytes = defaultdict(int)
    pcm_data_size = 0
    wait_samples = 0

    while pos < len(data):
        if gd3_abs and pos >= gd3_abs:
            break

        cmd = data[pos]
        start_pos = pos

        # PSG write
        if cmd == 0x50:
            pos += 2
        # YM2612 writes
        elif cmd in (0x52, 0x53):
            pos += 3
        # Other chip writes (2 bytes)
        elif cmd == 0x51 or (cmd >= 0x54 and cmd <= 0x5F):
            pos += 3
        # Wait N samples
        elif cmd == 0x61:
            wait_samples += struct.unpack('<H', data[pos+1:pos+3])[0]
            pos += 3
        # Wait 735 samples (NTSC frame)
        elif cmd == 0x62:
            wait_samples += 735
            pos += 1
        # Wait 882 samples (PAL frame)
        elif cmd == 0x63:
            wait_samples += 882
            pos += 1
        # End of data
        elif cmd == 0x66:
            pos += 1
            break
        # Data block
        elif cmd == 0x67:
            block_size = struct.unpack('<I', data[pos+3:pos+7])[0]
            pcm_data_size += block_size
            pos += 7 + block_size
        # Short wait (0x7n = wait n+1 samples)
        elif cmd >= 0x70 and cmd <= 0x7F:
            wait_samples += (cmd & 0x0F) + 1
            pos += 1
        # DAC write + wait (0x8n)
        elif cmd >= 0x80 and cmd <= 0x8F:
            wait_samples += (cmd & 0x0F)
            pos += 1
        # DAC stream commands
        elif cmd == 0x90:
            pos += 5
        elif cmd == 0x91:
            pos += 5
        elif cmd == 0x92:
            pos += 6
        elif cmd == 0x93:
            pos += 11
        elif cmd == 0x94:
            pos += 2
        elif cmd == 0x95:
            pos += 5
        # PCM seek
        elif cmd == 0xE0:
            pos += 5
        # 1-byte commands (0x30-0x3F)
        elif cmd >= 0x30 and cmd <= 0x3F:
            pos += 2
        # 2-byte commands (0x40-0x4E)
        elif cmd >= 0x40 and cmd <= 0x4E:
            pos += 3
        # 0x4F
        elif cmd == 0x4F:
            pos += 2
        # 2-byte commands (0xA0-0xBF)
        elif cmd >= 0xA0 and cmd <= 0xBF:
            pos += 3
        # 3-byte commands (0xC0-0xDF)
        elif cmd >= 0xC0 and cmd <= 0xDF:
            pos += 4
        # 4-byte commands (0xE1-0xFF)
        elif cmd >= 0xE1 and cmd <= 0xFF:
            pos += 5
        else:
            pos += 1

        cmd_size = pos - start_pos
        command_counts[cmd] += 1
        command_bytes[cmd] += cmd_size

    # Calculate sizes
    header_size = data_offset
    command_data_size = sum(command_bytes.values())
    gd3_size = len(data) - gd3_abs if gd3_abs else 0

    print("=" * 50)
    print("SIZE BREAKDOWN")
    print("=" * 50)
    print(f"Header:           {header_size:>8,} bytes ({header_size/total_size*100:>5.1f}%)")
    print(f"PCM/DAC Data:     {pcm_data_size:>8,} bytes ({pcm_data_size/total_size*100:>5.1f}%)")
    print(f"Command Data:     {command_data_size - pcm_data_size:>8,} bytes ({(command_data_size-pcm_data_size)/total_size*100:>5.1f}%)")
    print(f"GD3 Tags:         {gd3_size:>8,} bytes ({gd3_size/total_size*100:>5.1f}%)")
    print(f"{'-' * 50}")
    print(f"TOTAL:            {total_size:>8,} bytes")
    print()

    print("=" * 50)
    print("COMMAND BREAKDOWN (top 10 by size)")
    print("=" * 50)

    cmd_names = {
        0x50: "PSG write",
        0x51: "YM2413 write",
        0x52: "YM2612 port 0",
        0x53: "YM2612 port 1",
        0x61: "Wait N samples",
        0x62: "Wait 735 (NTSC)",
        0x63: "Wait 882 (PAL)",
        0x66: "End of data",
        0x67: "Data block (PCM)",
        0xE0: "PCM seek",
    }

    # Add short wait names
    for i in range(0x70, 0x80):
        cmd_names[i] = f"Wait {i - 0x70 + 1}"

    # Add DAC+wait names
    for i in range(0x80, 0x90):
        cmd_names[i] = f"DAC+wait {i - 0x80}"

    sorted_cmds = sorted(command_bytes.items(), key=lambda x: x[1], reverse=True)[:10]

    for cmd, size in sorted_cmds:
        name = cmd_names.get(cmd, f"Unknown 0x{cmd:02X}")
        count = command_counts[cmd]
        print(f"0x{cmd:02X} {name:<20} {count:>8,} cmds  {size:>8,} bytes ({size/total_size*100:>5.1f}%)")

    print()
    print("=" * 50)
    print("OPTIMIZATION SUGGESTIONS")
    print("=" * 50)

    if pcm_data_size > total_size * 0.3:
        print(f"! PCM data is {pcm_data_size/total_size*100:.0f}% of file")
        print("  - This VGM has sampled audio (DAC) which is large")
        print("  - Consider: Strip PCM for smaller file (loses drum samples)")
        print("  - Consider: Use SD card streaming instead of PROGMEM")

    if command_counts.get(0x67, 0) > 0:
        print(f"! Contains {command_counts[0x67]} PCM data block(s)")
        print("  - PCM blocks contain sampled audio for YM2612 DAC")

    dac_cmds = sum(command_counts.get(i, 0) for i in range(0x80, 0x90))
    if dac_cmds > 1000:
        print(f"! {dac_cmds:,} DAC write commands")
        print("  - Heavy use of PCM playback (common in Genesis games)")

    # Compression suggestion
    if compressed_size < total_size * 0.5:
        print(f"! Compresses well ({compressed_size/total_size*100:.0f}% of original)")
        print("  - On Teensy/ESP32: Keep as VGZ and decompress on-the-fly")
        print("  - On Arduino: Must use uncompressed (no RAM for decompression)")


def main():
    parser = argparse.ArgumentParser(description='Analyze VGM file structure')
    parser.add_argument('input', help='Input VGM/VGZ file')
    args = parser.parse_args()

    analyze_vgm(args.input)


if __name__ == '__main__':
    main()
