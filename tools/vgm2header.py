#!/usr/bin/env python3
"""
vgm2header.py - Convert VGM/VGZ files to C header files for Arduino

Part of the FM-90s Genesis Engine library
https://github.com/your-repo/GenesisEngine

Usage:
    python vgm2header.py input.vgm [output.h]
    python vgm2header.py input.vgz [output.h]
    python vgm2header.py *.vgm                  # Batch convert
    python vgm2header.py input.vgm --name my_song
    python vgm2header.py input.vgm --strip-dac  # Remove PCM for smaller size
    python vgm2header.py input.vgm --platform mega  # Truncate to fit Mega

Platforms and their flash limits:
    uno      - 28KB   (32KB - 4KB bootloader/lib)
    mega     - 200KB  (256KB - 56KB reserved)
    teensy40 - 1MB    (plenty of room)
    teensy41 - 7MB    (plenty of room)
    esp32    - 1MB    (plenty of room)

The output header can be included in Arduino sketches:
    #include "mysong.h"
    player.play(mysong_vgm, sizeof(mysong_vgm));
"""

import argparse
import gzip
import os
import sys
import struct
from pathlib import Path

# Platform flash size limits (in bytes)
# AVR has a 32KB limit per PROGMEM array due to 16-bit pointers
# Other platforms have much more room
PLATFORM_LIMITS = {
    'uno': 24 * 1024,       # 32KB flash total, keep VGM under 24KB
    'mega': 32 * 1024,      # AVR has 32KB per-array limit regardless of flash size
    'teensy40': 1024 * 1024,  # 2MB total, plenty of room
    'teensy41': 7 * 1024 * 1024,  # 8MB total, plenty of room
    'esp32': 1024 * 1024,   # Varies, but typically 4MB+
    'rp2040': 1024 * 1024,  # 2MB typical
}


def decompress_vgz(data: bytes) -> bytes:
    """Decompress VGZ (gzip-compressed VGM) data."""
    # Check for gzip magic number
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    return data


def strip_dac_data(data: bytes) -> bytes:
    """Remove PCM/DAC data from VGM to reduce size.

    This removes:
    - Data blocks (0x67) containing PCM samples
    - DAC write + wait commands (0x80-0x8F) - accumulated into merged waits
    - PCM seek commands (0xE0)

    The result will play FM and PSG but no sampled drums/sounds.
    """
    # Parse header to find data offset
    if data[:4] != b'Vgm ':
        return data

    version = struct.unpack('<I', data[0x08:0x0C])[0]

    if version >= 0x150:
        data_offset_rel = struct.unpack('<I', data[0x34:0x38])[0]
        data_offset = 0x34 + data_offset_rel if data_offset_rel else 0x40
    else:
        data_offset = 0x40

    # Copy header as-is
    output = bytearray(data[:data_offset])

    pos = data_offset
    dac_commands_removed = 0
    pcm_blocks_removed = 0
    pending_wait = 0  # Accumulate waits to merge them

    def flush_wait():
        """Write accumulated wait to output."""
        nonlocal pending_wait
        if pending_wait == 0:
            return

        # Use most efficient encoding
        while pending_wait > 0:
            if pending_wait == 735:
                output.append(0x62)  # Wait NTSC frame
                pending_wait = 0
            elif pending_wait == 882:
                output.append(0x63)  # Wait PAL frame
                pending_wait = 0
            elif pending_wait <= 16:
                output.append(0x70 + pending_wait - 1)  # Short wait
                pending_wait = 0
            elif pending_wait <= 65535:
                output.append(0x61)
                output.extend(struct.pack('<H', pending_wait))
                pending_wait = 0
            else:
                # Very long wait - use max and continue
                output.append(0x61)
                output.extend(struct.pack('<H', 65535))
                pending_wait -= 65535

    while pos < len(data):
        cmd = data[pos]

        # End of data
        if cmd == 0x66:
            flush_wait()
            output.append(cmd)
            pos += 1
            # Copy GD3 and rest of file
            output.extend(data[pos:])
            break

        # Data block (0x67) - skip PCM blocks
        elif cmd == 0x67:
            flush_wait()
            marker = data[pos + 1]
            data_type = data[pos + 2]
            block_size = struct.unpack('<I', data[pos + 3:pos + 7])[0]

            if data_type == 0x00:  # YM2612 PCM data
                pcm_blocks_removed += 1
                pos += 7 + block_size
                continue
            else:
                # Keep non-PCM data blocks
                output.extend(data[pos:pos + 7 + block_size])
                pos += 7 + block_size

        # DAC write + wait (0x80-0x8F) - just accumulate wait
        elif cmd >= 0x80 and cmd <= 0x8F:
            pending_wait += (cmd & 0x0F)
            dac_commands_removed += 1
            pos += 1

        # PCM seek (0xE0) - skip entirely
        elif cmd == 0xE0:
            pos += 5
            continue

        # PSG write
        elif cmd == 0x50:
            flush_wait()
            output.extend(data[pos:pos + 2])
            pos += 2

        # YM2612 writes
        elif cmd in (0x52, 0x53):
            flush_wait()
            output.extend(data[pos:pos + 3])
            pos += 3

        # Other chip writes
        elif cmd == 0x51 or (cmd >= 0x54 and cmd <= 0x5F):
            flush_wait()
            output.extend(data[pos:pos + 3])
            pos += 3

        # Wait commands - accumulate
        elif cmd == 0x61:
            pending_wait += struct.unpack('<H', data[pos + 1:pos + 3])[0]
            pos += 3
        elif cmd == 0x62:
            pending_wait += 735
            pos += 1
        elif cmd == 0x63:
            pending_wait += 882
            pos += 1

        # Short wait (0x70-0x7F) - accumulate
        elif cmd >= 0x70 and cmd <= 0x7F:
            pending_wait += (cmd & 0x0F) + 1
            pos += 1

        # DAC stream commands (0x90-0x95) - skip
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

        # Other commands - copy as-is with appropriate length
        elif cmd >= 0x30 and cmd <= 0x3F:
            flush_wait()
            output.extend(data[pos:pos + 2])
            pos += 2
        elif cmd >= 0x40 and cmd <= 0x4E:
            flush_wait()
            output.extend(data[pos:pos + 3])
            pos += 3
        elif cmd == 0x4F:
            flush_wait()
            output.extend(data[pos:pos + 2])
            pos += 2
        elif cmd >= 0xA0 and cmd <= 0xBF:
            flush_wait()
            output.extend(data[pos:pos + 3])
            pos += 3
        elif cmd >= 0xC0 and cmd <= 0xDF:
            flush_wait()
            output.extend(data[pos:pos + 4])
            pos += 4
        elif cmd >= 0xE1 and cmd <= 0xFF:
            flush_wait()
            output.extend(data[pos:pos + 5])
            pos += 5
        else:
            flush_wait()
            # Unknown - copy single byte
            output.append(cmd)
            pos += 1

    print(f"  Stripped: {dac_commands_removed:,} DAC commands, {pcm_blocks_removed} PCM blocks")
    print(f"  Size reduced: {len(data):,} -> {len(output):,} bytes ({len(output)/len(data)*100:.1f}%)")

    return bytes(output)


def truncate_to_size(data: bytes, max_size: int) -> bytes:
    """Truncate VGM data to fit within max_size bytes.

    This cuts off the VGM at a command boundary, adds an end marker (0x66),
    and updates the header with the new total samples.
    """
    if len(data) <= max_size:
        return data

    # Parse header to find data offset
    if data[:4] != b'Vgm ':
        return data

    version = struct.unpack('<I', data[0x08:0x0C])[0]

    if version >= 0x150:
        data_offset_rel = struct.unpack('<I', data[0x34:0x38])[0]
        data_offset = 0x34 + data_offset_rel if data_offset_rel else 0x40
    else:
        data_offset = 0x40

    # Copy header
    output = bytearray(data[:data_offset])

    pos = data_offset
    total_samples = 0

    # We need to leave room for the end marker (1 byte)
    # and we want to stop well before hitting the limit
    target_size = max_size - 10  # Leave some margin

    while pos < len(data) and len(output) < target_size:
        cmd = data[pos]

        # Determine command length and samples
        cmd_len = 1
        samples = 0

        if cmd == 0x66:  # End
            break
        elif cmd == 0x67:  # Data block
            marker = data[pos + 1]
            block_size = struct.unpack('<I', data[pos + 3:pos + 7])[0]
            cmd_len = 7 + block_size
        elif cmd == 0x50:  # PSG
            cmd_len = 2
        elif cmd in (0x52, 0x53):  # YM2612
            cmd_len = 3
        elif cmd == 0x61:  # Wait N
            cmd_len = 3
            samples = struct.unpack('<H', data[pos + 1:pos + 3])[0]
        elif cmd == 0x62:  # Wait 735
            samples = 735
        elif cmd == 0x63:  # Wait 882
            samples = 882
        elif cmd >= 0x70 and cmd <= 0x7F:  # Short wait
            samples = (cmd & 0x0F) + 1
        elif cmd >= 0x80 and cmd <= 0x8F:  # DAC + wait
            samples = cmd & 0x0F
        elif cmd == 0xE0:  # PCM seek
            cmd_len = 5
        elif cmd >= 0x30 and cmd <= 0x3F:
            cmd_len = 2
        elif cmd >= 0x40 and cmd <= 0x4E:
            cmd_len = 3
        elif cmd == 0x4F:
            cmd_len = 2
        elif cmd >= 0x51 and cmd <= 0x5F:
            cmd_len = 3
        elif cmd == 0x90:
            cmd_len = 5
        elif cmd == 0x91:
            cmd_len = 5
        elif cmd == 0x92:
            cmd_len = 6
        elif cmd == 0x93:
            cmd_len = 11
        elif cmd == 0x94:
            cmd_len = 2
        elif cmd == 0x95:
            cmd_len = 5
        elif cmd >= 0xA0 and cmd <= 0xBF:
            cmd_len = 3
        elif cmd >= 0xC0 and cmd <= 0xDF:
            cmd_len = 4
        elif cmd >= 0xE1 and cmd <= 0xFF:
            cmd_len = 5

        # Check if this command would exceed the limit
        if len(output) + cmd_len + 1 > target_size:
            break

        # Copy command
        output.extend(data[pos:pos + cmd_len])
        total_samples += samples
        pos += cmd_len

    # Add end marker
    output.append(0x66)

    # Update header with new total samples
    output[0x18:0x1C] = struct.pack('<I', total_samples)

    # Clear loop info since we truncated
    output[0x1C:0x20] = struct.pack('<I', 0)  # loop offset
    output[0x20:0x24] = struct.pack('<I', 0)  # loop samples

    # Clear GD3 offset (we're not including it)
    output[0x14:0x18] = struct.pack('<I', 0)

    # Update EOF offset
    output[0x04:0x08] = struct.pack('<I', len(output) - 4)

    duration_secs = total_samples / 44100.0
    print(f"  Truncated: {len(data):,} -> {len(output):,} bytes")
    print(f"  New duration: {duration_secs:.1f}s ({total_samples:,} samples)")

    return bytes(output)


def parse_vgm_header(data: bytes) -> dict:
    """Parse VGM header and return info dict."""
    if len(data) < 64:
        raise ValueError("File too small to be a valid VGM")

    # Check magic
    magic = data[0:4]
    if magic != b'Vgm ':
        raise ValueError(f"Invalid VGM magic: {magic}")

    # Parse header fields (little-endian)
    info = {
        'magic': magic.decode('ascii'),
        'eof_offset': struct.unpack('<I', data[0x04:0x08])[0],
        'version': struct.unpack('<I', data[0x08:0x0C])[0],
        'sn76489_clock': struct.unpack('<I', data[0x0C:0x10])[0],
        'ym2413_clock': struct.unpack('<I', data[0x10:0x14])[0],
        'gd3_offset': struct.unpack('<I', data[0x14:0x18])[0],
        'total_samples': struct.unpack('<I', data[0x18:0x1C])[0],
        'loop_offset': struct.unpack('<I', data[0x1C:0x20])[0],
        'loop_samples': struct.unpack('<I', data[0x20:0x24])[0],
    }

    # YM2612 clock (v1.10+)
    if info['version'] >= 0x110 and len(data) >= 0x30:
        info['ym2612_clock'] = struct.unpack('<I', data[0x2C:0x30])[0]
    else:
        info['ym2612_clock'] = 0

    # Calculate duration
    info['duration_seconds'] = info['total_samples'] / 44100.0

    # Determine chip type
    chips = []
    if info['ym2612_clock'] > 0:
        chips.append('YM2612')
    if info['sn76489_clock'] > 0:
        chips.append('SN76489')
    if info['ym2413_clock'] > 0:
        chips.append('YM2413')
    info['chips'] = chips

    return info


def parse_gd3_tag(data: bytes, gd3_offset: int) -> dict:
    """Parse GD3 tag for track info."""
    if gd3_offset == 0:
        return {}

    # GD3 offset is relative to 0x14
    abs_offset = 0x14 + gd3_offset
    if abs_offset >= len(data):
        return {}

    # Check GD3 magic
    if data[abs_offset:abs_offset+4] != b'Gd3 ':
        return {}

    # GD3 strings are null-terminated UTF-16LE
    # Format: track name (EN), track name (JP), game name (EN), game name (JP),
    #         system name (EN), system name (JP), author (EN), author (JP),
    #         date, ripper, notes

    try:
        # Skip header (12 bytes: magic + version + length)
        string_data = data[abs_offset + 12:]

        # Parse strings
        strings = []
        pos = 0
        for _ in range(11):  # 11 strings in GD3
            end = string_data.find(b'\x00\x00', pos)
            if end == -1:
                break
            # Ensure we're at an even position for UTF-16
            if (end - pos) % 2 == 1:
                end += 1
            s = string_data[pos:end].decode('utf-16-le', errors='ignore')
            strings.append(s)
            pos = end + 2

        return {
            'track_name': strings[0] if len(strings) > 0 else '',
            'track_name_jp': strings[1] if len(strings) > 1 else '',
            'game_name': strings[2] if len(strings) > 2 else '',
            'game_name_jp': strings[3] if len(strings) > 3 else '',
            'system_name': strings[4] if len(strings) > 4 else '',
            'author': strings[6] if len(strings) > 6 else '',
        }
    except Exception:
        return {}


def sanitize_name(name: str) -> str:
    """Convert filename to valid C identifier."""
    # Remove extension and path
    name = Path(name).stem

    # Replace invalid characters with underscore
    result = ''
    for c in name:
        if c.isalnum() or c == '_':
            result += c
        else:
            result += '_'

    # Ensure it starts with a letter or underscore
    if result and result[0].isdigit():
        result = '_' + result

    # Ensure it's not empty
    if not result:
        result = 'vgm_data'

    return result.lower()


def format_bytes(data: bytes, bytes_per_line: int = 16) -> str:
    """Format bytes as C array initializer."""
    lines = []
    for i in range(0, len(data), bytes_per_line):
        chunk = data[i:i + bytes_per_line]
        hex_values = ', '.join(f'0x{b:02X}' for b in chunk)
        lines.append(f'  {hex_values},')
    return '\n'.join(lines)


def generate_header(data: bytes, name: str, info: dict, gd3: dict) -> str:
    """Generate C header file content."""

    # Version string
    version = info['version']
    version_str = f"{(version >> 8) & 0xFF}.{version & 0xFF}"

    # Duration string
    duration = info['duration_seconds']
    minutes = int(duration // 60)
    seconds = duration % 60
    duration_str = f"{minutes}:{seconds:05.2f}"

    # Chips string
    chips_str = ', '.join(info['chips']) if info['chips'] else 'None'

    # Track info from GD3
    track_name = gd3.get('track_name', 'Unknown')
    game_name = gd3.get('game_name', 'Unknown')
    author = gd3.get('author', 'Unknown')

    # Size info
    size_bytes = len(data)
    size_kb = size_bytes / 1024.0

    # Warning for large files
    size_warning = ''
    if size_bytes > 28 * 1024:
        size_warning = f'''
// WARNING: This file is {size_kb:.1f} KB
// Arduino Uno has only 32KB flash (minus bootloader)
// Consider using Mega, Teensy, or SD card for large VGMs
'''

    header = f'''// =============================================================================
// VGM data generated by vgm2header.py
// Part of FM-90s Genesis Engine
// =============================================================================
//
// Track: {track_name}
// Game:  {game_name}
// Author: {author}
//
// VGM Version: {version_str}
// Chips: {chips_str}
// Duration: {duration_str}
// Size: {size_bytes} bytes ({size_kb:.1f} KB)
// Loop: {"Yes" if info['loop_offset'] > 0 else "No"}
//{size_warning}
// =============================================================================

#ifndef {name.upper()}_H
#define {name.upper()}_H

#include <Arduino.h>

#ifdef __AVR__
#include <avr/pgmspace.h>
const uint8_t {name}_vgm[] PROGMEM = {{
#else
const uint8_t {name}_vgm[] = {{
#endif
{format_bytes(data)}
}};

const size_t {name}_vgm_len = sizeof({name}_vgm);

// Track info (optional)
#define {name.upper()}_TRACK_NAME "{track_name}"
#define {name.upper()}_GAME_NAME "{game_name}"
#define {name.upper()}_DURATION_MS {int(duration * 1000)}

#endif // {name.upper()}_H
'''
    return header


def convert_file(input_path: str, output_path: str = None, name: str = None,
                  strip_dac: bool = False, platform: str = None) -> bool:
    """Convert a single VGM/VGZ file to a C header."""

    input_path = Path(input_path)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        return False

    # Read input file
    try:
        with open(input_path, 'rb') as f:
            raw_data = f.read()
    except IOError as e:
        print(f"Error reading {input_path}: {e}", file=sys.stderr)
        return False

    # Decompress if VGZ
    try:
        data = decompress_vgz(raw_data)
    except Exception as e:
        print(f"Error decompressing {input_path}: {e}", file=sys.stderr)
        return False

    # Strip DAC/PCM data if requested
    if strip_dac:
        print(f"Stripping DAC/PCM data...")
        data = strip_dac_data(data)

    # Truncate to platform size if specified
    if platform:
        platform = platform.lower()
        if platform not in PLATFORM_LIMITS:
            print(f"Warning: Unknown platform '{platform}'. Available: {', '.join(PLATFORM_LIMITS.keys())}", file=sys.stderr)
        else:
            max_size = PLATFORM_LIMITS[platform]
            if len(data) > max_size:
                print(f"Truncating to fit {platform} ({max_size // 1024}KB limit)...")
                data = truncate_to_size(data, max_size)

    # Parse VGM header
    try:
        info = parse_vgm_header(data)
    except ValueError as e:
        print(f"Error parsing {input_path}: {e}", file=sys.stderr)
        return False

    # Check for supported chips
    if not info['ym2612_clock'] and not info['sn76489_clock']:
        print(f"Warning: {input_path} has no YM2612 or SN76489 data", file=sys.stderr)
        if info['ym2413_clock']:
            print("  (YM2413/Master System FM is not supported by this hardware)", file=sys.stderr)

    # Parse GD3 tag
    gd3 = parse_gd3_tag(data, info['gd3_offset'])

    # Determine output name
    if name is None:
        name = sanitize_name(input_path.name)

    # Determine output path
    if output_path is None:
        output_path = input_path.with_suffix('.h')
    else:
        output_path = Path(output_path)

    # Generate header
    header_content = generate_header(data, name, info, gd3)

    # Write output
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(header_content)
    except IOError as e:
        print(f"Error writing {output_path}: {e}", file=sys.stderr)
        return False

    # Print summary
    print(f"Converted: {input_path.name}")
    print(f"  Output: {output_path.name}")
    print(f"  Size: {len(data)} bytes ({len(data)/1024:.1f} KB)")
    print(f"  Chips: {', '.join(info['chips'])}")
    print(f"  Duration: {info['duration_seconds']:.2f}s")
    if gd3.get('track_name'):
        print(f"  Track: {gd3['track_name']}")
    if gd3.get('game_name'):
        print(f"  Game: {gd3['game_name']}")
    print()

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Convert VGM/VGZ files to C headers for Arduino',
        epilog='Part of FM-90s Genesis Engine'
    )
    parser.add_argument('input', nargs='+', help='Input VGM/VGZ file(s)')
    parser.add_argument('-o', '--output', help='Output header file (single file mode)')
    parser.add_argument('-n', '--name', help='Variable name prefix (default: derived from filename)')
    parser.add_argument('-d', '--output-dir', help='Output directory for batch mode')
    parser.add_argument('--strip-dac', action='store_true',
                        help='Remove PCM/DAC data for smaller size (loses drum samples)')
    parser.add_argument('-p', '--platform',
                        choices=['uno', 'mega', 'teensy40', 'teensy41', 'esp32', 'rp2040'],
                        help='Target platform - truncates VGM to fit in flash')

    args = parser.parse_args()

    # Single file mode
    if len(args.input) == 1 and not any('*' in p for p in args.input):
        success = convert_file(args.input[0], args.output, args.name, args.strip_dac, args.platform)
        sys.exit(0 if success else 1)

    # Batch mode
    from glob import glob

    files = []
    for pattern in args.input:
        files.extend(glob(pattern))

    if not files:
        print("No files found matching the pattern(s)", file=sys.stderr)
        sys.exit(1)

    print(f"Converting {len(files)} file(s)...\n")

    success_count = 0
    for input_file in files:
        output_file = None
        if args.output_dir:
            output_file = Path(args.output_dir) / (Path(input_file).stem + '.h')

        if convert_file(input_file, output_file, strip_dac=args.strip_dac, platform=args.platform):
            success_count += 1

    print(f"Successfully converted {success_count}/{len(files)} files")
    sys.exit(0 if success_count == len(files) else 1)


if __name__ == '__main__':
    main()
