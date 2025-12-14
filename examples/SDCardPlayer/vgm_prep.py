#!/usr/bin/env python3
"""
vgm_prep.py - Prepare VGM/VGZ files for low-memory playback

Converts VGM files for playback on memory-constrained devices (Uno, Mega)
by inlining DAC samples and optionally reducing DAC sample rate.

Features:
  - Decompress VGZ to VGM
  - Inline DAC data (no data bank needed, works on any device)
  - DAC rate reduction (1/2, 1/4) with proper timing preservation
  - Strip DAC entirely (FM/PSG only)
  - Preserves loop points

The output VGM file can be played directly from SD card without
requiring large RAM buffers for PCM data.

Usage:
    python vgm_prep.py input.vgz -o output.vgm
    python vgm_prep.py input.vgm --dac-rate 1 -o output_full.vgm
    python vgm_prep.py input.vgm --no-dac -o output_no_dac.vgm
"""

import argparse
import gzip
import os
import struct
import sys


# =============================================================================
# VGM Constants
# =============================================================================

VGM_MAGIC = b'Vgm '

# Header offsets
OFF_EOF = 0x04
OFF_VERSION = 0x08
OFF_SN76489_CLOCK = 0x0C
OFF_YM2612_CLOCK = 0x2C
OFF_TOTAL_SAMPLES = 0x18
OFF_LOOP_OFFSET = 0x1C
OFF_LOOP_SAMPLES = 0x20
OFF_DATA_OFFSET = 0x34

# Commands
CMD_PSG = 0x50
CMD_YM2612_P0 = 0x52
CMD_YM2612_P1 = 0x53
CMD_WAIT = 0x61
CMD_WAIT_NTSC = 0x62
CMD_WAIT_PAL = 0x63
CMD_END = 0x66
CMD_DATA_BLOCK = 0x67
CMD_PCM_SEEK = 0xE0

# DAC register
YM2612_DAC_REG = 0x2A

# Frame samples
FRAME_NTSC = 735
FRAME_PAL = 882


# =============================================================================
# VGM Processing
# =============================================================================

def decompress_vgz(data):
    """Decompress VGZ data if needed."""
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    return data


def read_u32(data, offset):
    """Read little-endian uint32."""
    return struct.unpack('<I', data[offset:offset+4])[0]


def write_u32(value):
    """Write little-endian uint32."""
    return struct.pack('<I', value)


def write_u16(value):
    """Write little-endian uint16."""
    return struct.pack('<H', value)


def parse_header(data):
    """Parse VGM header, return dict of fields."""
    if data[:4] != VGM_MAGIC:
        return None

    version = read_u32(data, OFF_VERSION)
    total_samples = read_u32(data, OFF_TOTAL_SAMPLES)

    loop_offset_rel = read_u32(data, OFF_LOOP_OFFSET)
    loop_offset = (OFF_LOOP_OFFSET + loop_offset_rel) if loop_offset_rel else 0
    loop_samples = read_u32(data, OFF_LOOP_SAMPLES)

    if version >= 0x150:
        data_offset_rel = read_u32(data, OFF_DATA_OFFSET)
        data_offset = (OFF_DATA_OFFSET + data_offset_rel) if data_offset_rel else 0x40
    else:
        data_offset = 0x40

    return {
        'version': version,
        'total_samples': total_samples,
        'loop_offset': loop_offset,
        'loop_samples': loop_samples,
        'data_offset': data_offset,
    }


def extract_pcm_data(data, data_offset):
    """Extract PCM data block from VGM, return (pcm_bytes, block_end_offset)."""
    pos = data_offset

    while pos < len(data):
        cmd = data[pos]

        if cmd == CMD_DATA_BLOCK:
            if pos + 7 > len(data):
                break
            marker = data[pos + 1]
            if marker != 0x66:
                pos += 1
                continue
            block_type = data[pos + 2]
            block_size = read_u32(data, pos + 3)

            if block_type == 0x00:  # YM2612 PCM
                pcm_start = pos + 7
                pcm_end = pcm_start + block_size
                if pcm_end <= len(data):
                    return data[pcm_start:pcm_end], pcm_end

            pos += 7 + block_size
        elif cmd == CMD_END:
            break
        elif cmd == CMD_PSG:
            pos += 2
        elif cmd in (CMD_YM2612_P0, CMD_YM2612_P1):
            pos += 3
        elif cmd == CMD_WAIT:
            pos += 3
        elif cmd in (CMD_WAIT_NTSC, CMD_WAIT_PAL):
            pos += 1
        elif 0x70 <= cmd <= 0x7F:
            pos += 1
        elif 0x80 <= cmd <= 0x8F:
            pos += 1
        elif cmd == CMD_PCM_SEEK:
            pos += 5
        else:
            # Skip unknown commands conservatively
            pos += 1

    return None, data_offset


def process_vgm(data, dac_rate=1, strip_dac=False, verbose=False):
    """
    Process VGM data:
    - Inline DAC samples (convert data bank to direct writes)
    - Apply DAC rate reduction
    - Optionally strip DAC entirely

    Returns processed VGM data bytes.
    """
    header = parse_header(data)
    if not header:
        raise ValueError("Invalid VGM file")

    data_offset = header['data_offset']
    loop_offset = header['loop_offset']

    # Extract PCM data
    pcm_data, pcm_block_end = extract_pcm_data(data, data_offset)
    pcm_pos = 0

    if verbose:
        if pcm_data:
            print(f"  PCM data block: {len(pcm_data):,} bytes")
        else:
            print("  No PCM data block found")

    # Build output
    # Start with header (we'll update offsets later)
    output = bytearray(data[:data_offset])

    # Track positions for loop point mapping
    input_to_output = {}  # Map input file positions to output positions
    new_loop_offset = 0

    # Process commands
    pos = data_offset
    dac_count = 0
    dac_written = 0
    dac_skipped = 0

    # Skip past the data block if present
    if pcm_data and pos < pcm_block_end:
        # Find and skip the data block
        while pos < len(data):
            if data[pos] == CMD_DATA_BLOCK:
                marker = data[pos + 1] if pos + 1 < len(data) else 0
                if marker == 0x66 and pos + 7 <= len(data):
                    block_size = read_u32(data, pos + 3)
                    pos = pos + 7 + block_size
                    break
            pos += 1

    # Reset to data_offset and process all commands
    pos = data_offset

    while pos < len(data):
        # Record position mapping for loop point
        input_to_output[pos] = len(output)

        # Check if this is the loop point
        if loop_offset and pos == loop_offset:
            new_loop_offset = len(output)

        cmd = data[pos]

        # Data block - skip entirely (we inline the data)
        if cmd == CMD_DATA_BLOCK:
            if pos + 7 <= len(data):
                marker = data[pos + 1]
                if marker == 0x66:
                    block_size = read_u32(data, pos + 3)
                    pos += 7 + block_size
                    continue
            pos += 1
            continue

        # End of VGM
        if cmd == CMD_END:
            output.append(CMD_END)
            break

        # PSG write
        if cmd == CMD_PSG:
            output.extend(data[pos:pos+2])
            pos += 2
            continue

        # YM2612 writes
        if cmd in (CMD_YM2612_P0, CMD_YM2612_P1):
            output.extend(data[pos:pos+3])
            pos += 3
            continue

        # Wait commands
        if cmd == CMD_WAIT:
            output.extend(data[pos:pos+3])
            pos += 3
            continue

        if cmd in (CMD_WAIT_NTSC, CMD_WAIT_PAL):
            output.append(cmd)
            pos += 1
            continue

        # Short wait (0x70-0x7F)
        if 0x70 <= cmd <= 0x7F:
            output.append(cmd)
            pos += 1
            continue

        # DAC + wait (0x80-0x8F)
        if 0x80 <= cmd <= 0x8F:
            wait_samples = cmd & 0x0F

            if strip_dac:
                # Strip DAC - just output wait if needed
                if wait_samples > 0:
                    output.append(0x70 + wait_samples - 1)
                pos += 1
                dac_skipped += 1
                continue

            # Get DAC sample
            if pcm_data and pcm_pos < len(pcm_data):
                dac_sample = pcm_data[pcm_pos]
                pcm_pos += 1
            else:
                dac_sample = 0x80  # Silence

            dac_count += 1

            # Apply rate reduction
            if dac_rate > 1 and (dac_count % dac_rate) != 1:
                # Skip this sample, but preserve wait timing
                if wait_samples > 0:
                    output.append(0x70 + wait_samples - 1)
                dac_skipped += 1
            else:
                # Write DAC sample as direct YM2612 write
                output.append(CMD_YM2612_P0)
                output.append(YM2612_DAC_REG)
                output.append(dac_sample)
                dac_written += 1

                # Add wait if needed
                if wait_samples > 0:
                    output.append(0x70 + wait_samples - 1)

            pos += 1
            continue

        # PCM seek
        if cmd == CMD_PCM_SEEK:
            if pos + 5 <= len(data):
                pcm_pos = read_u32(data, pos + 1)
            pos += 5
            continue

        # DAC stream commands (0x90-0x95) - skip
        if cmd == 0x90:
            pos += 5
            continue
        if cmd == 0x91:
            pos += 5
            continue
        if cmd == 0x92:
            pos += 6
            continue
        if cmd == 0x93:
            pos += 11
            continue
        if cmd == 0x94:
            pos += 2
            continue
        if cmd == 0x95:
            pos += 5
            continue

        # Other 2-byte commands (0x30-0x3F, 0x4F, 0x50)
        if (0x30 <= cmd <= 0x3F) or cmd == 0x4F:
            output.extend(data[pos:pos+2])
            pos += 2
            continue

        # Other 3-byte commands (0x40-0x4E, 0x51-0x5F, 0xA0-0xBF)
        if (0x40 <= cmd <= 0x4E) or (0x51 <= cmd <= 0x5F) or (0xA0 <= cmd <= 0xBF):
            output.extend(data[pos:pos+3])
            pos += 3
            continue

        # 4-byte commands (0xC0-0xDF)
        if 0xC0 <= cmd <= 0xDF:
            output.extend(data[pos:pos+4])
            pos += 4
            continue

        # 5-byte commands (0xE0-0xFF except E0 which is PCM seek)
        if 0xE1 <= cmd <= 0xFF:
            output.extend(data[pos:pos+5])
            pos += 5
            continue

        # Unknown - skip single byte
        pos += 1

    if verbose:
        if pcm_data:
            print(f"  DAC samples: {dac_written:,} written, {dac_skipped:,} skipped")
            if dac_rate > 1:
                print(f"  DAC rate reduction: 1/{dac_rate}")

    # Update header
    # EOF offset (relative to 0x04)
    eof_offset = len(output) - 4
    output[0x04:0x08] = write_u32(eof_offset)

    # Loop offset (relative to 0x1C)
    if new_loop_offset > 0:
        loop_offset_rel = new_loop_offset - OFF_LOOP_OFFSET
        output[OFF_LOOP_OFFSET:OFF_LOOP_OFFSET+4] = write_u32(loop_offset_rel)
    else:
        output[OFF_LOOP_OFFSET:OFF_LOOP_OFFSET+4] = write_u32(0)

    # Data offset stays the same (relative to 0x34)
    # It's already correct in the copied header

    return bytes(output)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Prepare VGM/VGZ files for low-memory playback",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Convert VGZ to VGM with inlined DAC (default: 1/4 rate for Uno/Mega)
    python vgm_prep.py song.vgz -o song.vgm

    # Full quality DAC (for Teensy or other fast boards)
    python vgm_prep.py song.vgm --dac-rate 1 -o song_full.vgm

    # Strip all DAC data (FM/PSG only)
    python vgm_prep.py song.vgm --no-dac -o song_no_dac.vgm

    # Process all VGZ files in a directory
    for f in *.vgz; do python vgm_prep.py "$f" -o "${f%.vgz}.vgm"; done

Output files can be played directly from SD card on any device,
including Arduino Uno and Mega, without requiring PCM data buffers.
        """
    )

    parser.add_argument('input', help='Input VGM or VGZ file')
    parser.add_argument('-o', '--output', help='Output VGM file (default: input_prep.vgm)')
    parser.add_argument('--dac-rate', type=int, choices=[1, 2, 4], default=4,
                        help='DAC sample rate divisor (1=full, 2=half, 4=quarter, default: 4)')
    parser.add_argument('--no-dac', action='store_true',
                        help='Strip all DAC/PCM data (FM/PSG only)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output')

    args = parser.parse_args()

    # Check input file
    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        return 1

    # Determine output filename
    if args.output:
        output_path = args.output
    else:
        base = os.path.splitext(args.input)[0]
        if base.endswith('.vgm'):
            base = base[:-4]
        suffix = ''
        if args.no_dac:
            suffix = '_nodac'
        elif args.dac_rate > 1:
            suffix = f'_{args.dac_rate}x'
        output_path = f"{base}{suffix}_prep.vgm"

    # Read input
    print(f"Reading: {args.input}")
    with open(args.input, 'rb') as f:
        data = f.read()

    original_size = len(data)

    # Decompress if VGZ
    data = decompress_vgz(data)
    if len(data) != original_size:
        print(f"  Decompressed: {original_size:,} -> {len(data):,} bytes")

    # Validate
    if data[:4] != VGM_MAGIC:
        print("Error: Not a valid VGM file")
        return 1

    # Process
    print("Processing...")
    try:
        output_data = process_vgm(
            data,
            dac_rate=args.dac_rate,
            strip_dac=args.no_dac,
            verbose=args.verbose
        )
    except Exception as e:
        print(f"Error processing VGM: {e}")
        return 1

    # Write output
    print(f"Writing: {output_path}")
    with open(output_path, 'wb') as f:
        f.write(output_data)

    # Summary
    ratio = len(output_data) / len(data) * 100
    print(f"  Output size: {len(output_data):,} bytes ({ratio:.1f}% of original)")

    if args.no_dac:
        print("  DAC data stripped - FM/PSG only")
    elif args.dac_rate > 1:
        print(f"  DAC rate reduced to 1/{args.dac_rate}")

    print("Done!")
    return 0


if __name__ == '__main__':
    sys.exit(main())
