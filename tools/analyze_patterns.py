#!/usr/bin/env python3
"""Analyze VGM patterns for compression optimization."""

import sys
import struct
import gzip
from collections import Counter, defaultdict
from pathlib import Path

def decompress_vgz(data: bytes) -> bytes:
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    return data

def analyze_vgm(path: str):
    with open(path, 'rb') as f:
        data = decompress_vgz(f.read())

    if data[:4] != b'Vgm ':
        print("Not a valid VGM file")
        return

    version = struct.unpack('<I', data[0x08:0x0C])[0]
    if version >= 0x150:
        data_offset_rel = struct.unpack('<I', data[0x34:0x38])[0]
        data_offset = 0x34 + data_offset_rel if data_offset_rel else 0x40
    else:
        data_offset = 0x40

    # Track YM2612 register writes
    ym_port0_regs = Counter()  # Which registers are written
    ym_port1_regs = Counter()
    ym_port0_values = defaultdict(Counter)  # What values per register
    ym_port1_values = defaultdict(Counter)
    psg_values = Counter()

    # Track wait patterns
    wait_values = Counter()

    # Track sequences (pairs and triples of commands)
    prev_cmd = None
    prev_prev_cmd = None
    pairs = Counter()
    triples = Counter()

    # Track frame patterns (commands between waits)
    current_frame = []
    frame_patterns = Counter()

    pos = data_offset
    while pos < len(data):
        cmd = data[pos]

        if cmd == 0x66:  # End
            break
        elif cmd == 0x50:  # PSG
            val = data[pos + 1]
            psg_values[val] += 1
            current_frame.append(('PSG', val))
            cmd_tuple = ('PSG', val)
            pos += 2
        elif cmd == 0x52:  # YM2612 port 0
            reg = data[pos + 1]
            val = data[pos + 2]
            ym_port0_regs[reg] += 1
            ym_port0_values[reg][val] += 1
            current_frame.append(('YM0', reg, val))
            cmd_tuple = ('YM0', reg)
            pos += 3
        elif cmd == 0x53:  # YM2612 port 1
            reg = data[pos + 1]
            val = data[pos + 2]
            ym_port1_regs[reg] += 1
            ym_port1_values[reg][val] += 1
            current_frame.append(('YM1', reg, val))
            cmd_tuple = ('YM1', reg)
            pos += 3
        elif cmd == 0x61:  # Wait N
            samples = struct.unpack('<H', data[pos + 1:pos + 3])[0]
            wait_values[samples] += 1
            if current_frame:
                frame_patterns[tuple(current_frame)] += 1
                current_frame = []
            cmd_tuple = ('WAIT', samples)
            pos += 3
        elif cmd == 0x62:  # Wait 735
            wait_values[735] += 1
            if current_frame:
                frame_patterns[tuple(current_frame)] += 1
                current_frame = []
            cmd_tuple = ('WAIT', 735)
            pos += 1
        elif cmd == 0x63:  # Wait 882
            wait_values[882] += 1
            if current_frame:
                frame_patterns[tuple(current_frame)] += 1
                current_frame = []
            cmd_tuple = ('WAIT', 882)
            pos += 1
        elif cmd >= 0x70 and cmd <= 0x7F:  # Short wait
            samples = (cmd & 0x0F) + 1
            wait_values[samples] += 1
            if current_frame:
                frame_patterns[tuple(current_frame)] += 1
                current_frame = []
            cmd_tuple = ('WAIT', samples)
            pos += 1
        elif cmd == 0x67:  # Data block
            block_size = struct.unpack('<I', data[pos + 3:pos + 7])[0]
            pos += 7 + block_size
            continue
        elif cmd >= 0x80 and cmd <= 0x8F:  # DAC + wait
            pos += 1
            continue
        elif cmd == 0xE0:  # PCM seek
            pos += 5
            continue
        else:
            pos += 1
            continue

        # Track pairs
        if prev_cmd:
            pairs[(prev_cmd, cmd_tuple)] += 1
        if prev_prev_cmd and prev_cmd:
            triples[(prev_prev_cmd, prev_cmd, cmd_tuple)] += 1

        prev_prev_cmd = prev_cmd
        prev_cmd = cmd_tuple

    # Print analysis
    print(f"\n{'='*60}")
    print(f"PATTERN ANALYSIS: {Path(path).name}")
    print(f"{'='*60}")

    print(f"\n--- YM2612 Port 0 Register Usage (top 15) ---")
    for reg, count in ym_port0_regs.most_common(15):
        unique_vals = len(ym_port0_values[reg])
        print(f"  Reg 0x{reg:02X}: {count:6} writes, {unique_vals:3} unique values")

    print(f"\n--- YM2612 Port 1 Register Usage (top 10) ---")
    for reg, count in ym_port1_regs.most_common(10):
        unique_vals = len(ym_port1_values[reg])
        print(f"  Reg 0x{reg:02X}: {count:6} writes, {unique_vals:3} unique values")

    print(f"\n--- Wait Value Distribution (top 10) ---")
    for samples, count in wait_values.most_common(10):
        ms = samples / 44.1
        print(f"  {samples:5} samples ({ms:6.1f}ms): {count:6} times")

    print(f"\n--- PSG Value Distribution (top 10) ---")
    for val, count in psg_values.most_common(10):
        print(f"  0x{val:02X}: {count:6} times")

    print(f"\n--- Repeated Frame Patterns ---")
    repeated = [(p, c) for p, c in frame_patterns.items() if c > 5 and len(p) > 1]
    repeated.sort(key=lambda x: x[1] * len(x[0]), reverse=True)
    for pattern, count in repeated[:10]:
        size = len(pattern)
        savings = count * (size - 1)  # bytes saved if we use 1-byte reference
        print(f"  {count:4}x pattern of {size:2} cmds (saves ~{savings} bytes)")

    print(f"\n--- Command Pairs (top 10) ---")
    for pair, count in pairs.most_common(10):
        print(f"  {count:5}x: {pair[0]} -> {pair[1]}")

    # Estimate compression potential
    print(f"\n{'='*60}")
    print("COMPRESSION POTENTIAL")
    print(f"{'='*60}")

    # Count registers that have very few unique values
    simple_regs = 0
    for reg, vals in ym_port0_values.items():
        if len(vals) <= 4:
            simple_regs += ym_port0_regs[reg]
    for reg, vals in ym_port1_values.items():
        if len(vals) <= 4:
            simple_regs += ym_port1_regs[reg]

    total_ym = sum(ym_port0_regs.values()) + sum(ym_port1_regs.values())
    print(f"YM registers with <=4 unique values: {simple_regs}/{total_ym} ({100*simple_regs/max(1,total_ym):.1f}%)")

    # Frame pattern potential
    total_frames = sum(frame_patterns.values())
    repeated_frames = sum(c for p, c in frame_patterns.items() if c > 1)
    print(f"Repeated frame patterns: {repeated_frames}/{total_frames} ({100*repeated_frames/max(1,total_frames):.1f}%)")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python analyze_patterns.py <vgm_file>")
        sys.exit(1)
    analyze_vgm(sys.argv[1])
