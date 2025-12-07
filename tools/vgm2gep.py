#!/usr/bin/env python3
"""
vgm2gep.py - Convert VGM/VGZ files to GEP (Genesis Engine Packed) format

GEP is an optimized format for AVR microcontrollers that achieves 2-4x
better compression than VGM while maintaining full playback quality.

Usage:
    python vgm2gep.py input.vgm -o output.h
    python vgm2gep.py input.vgm --platform uno
    python vgm2gep.py input.vgm --strip-dac
    python vgm2gep.py input.vgm --dpcm  # 4-bit DAC compression
"""

import argparse
import gzip
import struct
import sys
from pathlib import Path
from collections import Counter
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

# GEP format constants
GEP_MAGIC = b'GEP\x01'
GEP_FLAG_PSG = 0x01
GEP_FLAG_YM2612 = 0x02
GEP_FLAG_DAC = 0x04
GEP_FLAG_MULTI_CHUNK = 0x08
GEP_FLAG_DPCM = 0x10

# Command bytes
CMD_WAIT_SHORT_BASE = 0x00      # 0x00-0x3F: wait 1-64 samples
CMD_DICT_WRITE_BASE = 0x40      # 0x40-0x7F: dict entry 0-63
CMD_PSG_MULTI_BASE = 0x80       # 0x80-0x8F: 1-16 PSG writes
CMD_WAIT_FRAMES_BASE = 0x90     # 0x90-0x9F: wait 1-16 frames
CMD_YM_KEY_BASE = 0xA0          # 0xA0-0xAB: key on/off ch 0-11
CMD_DICT_EXT = 0xB0
CMD_YM_RAW_P0 = 0xB1
CMD_YM_RAW_P1 = 0xB2
CMD_PSG_RAW = 0xB3
CMD_WAIT_LONG = 0xB4
CMD_LOOP_MARK = 0xB5
CMD_DAC_WRITE = 0xB6
CMD_DAC_SEEK = 0xB7
CMD_DAC_BLOCK = 0xB8
CMD_DAC_RUN = 0xB9
CMD_DPCM_BLOCK = 0xBA
CMD_SAMPLE_PLAY = 0xBB          # Play sample: [sample_id] [rate]
CMD_DAC_START = 0xBC            # Start DAC stream: [pos_lo] [pos_hi] [rate]
CMD_DAC_WAIT_BASE = 0xC0        # 0xC0-0xCF: DAC + wait 0-15
CMD_SAMPLE_BASE = 0xD0          # 0xD0-0xDF: Play sample 0-15 (quick)
CMD_CHUNK_END = 0xFE
CMD_END = 0xFF

# Flags
GEP_FLAG_SAMPLES = 0x20         # Uses sample-based DAC

# DPCM 4-bit delta table (optimized for 8-bit unsigned audio)
DPCM_STEPS = [-34, -21, -13, -8, -5, -3, -1, 0, 1, 3, 5, 8, 13, 21, 34, 55]

# Platform chunk size limits
PLATFORM_LIMITS = {
    'uno': 24 * 1024,
    'mega': 32 * 1024,  # Per-array limit on AVR
    'teensy40': 512 * 1024,
    'teensy41': 2 * 1024 * 1024,
    'esp32': 512 * 1024,
    'rp2040': 512 * 1024,
}

SAMPLES_PER_FRAME = 735  # NTSC

@dataclass
class VGMCommand:
    """Represents a parsed VGM command."""
    type: str  # 'ym0', 'ym1', 'psg', 'wait', 'dac', 'dac_seek', 'end', 'loop'
    reg: int = 0
    val: int = 0
    samples: int = 0
    pcm_pos: int = 0


def decompress_vgz(data: bytes) -> bytes:
    """Decompress VGZ (gzip-compressed VGM) data."""
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    return data


def parse_vgm(data: bytes, strip_dac: bool = False) -> Tuple[List[VGMCommand], bytes, dict]:
    """Parse VGM file into commands, PCM data, and header info."""

    if data[:4] != b'Vgm ':
        raise ValueError("Invalid VGM magic")

    version = struct.unpack('<I', data[0x08:0x0C])[0]
    total_samples = struct.unpack('<I', data[0x18:0x1C])[0]
    loop_offset_rel = struct.unpack('<I', data[0x1C:0x20])[0]
    loop_samples = struct.unpack('<I', data[0x20:0x24])[0]

    sn76489_clock = struct.unpack('<I', data[0x0C:0x10])[0]
    ym2612_clock = 0
    if version >= 0x110:
        ym2612_clock = struct.unpack('<I', data[0x2C:0x30])[0]

    if version >= 0x150:
        data_offset_rel = struct.unpack('<I', data[0x34:0x38])[0]
        data_offset = 0x34 + data_offset_rel if data_offset_rel else 0x40
    else:
        data_offset = 0x40

    loop_offset = 0x1C + loop_offset_rel if loop_offset_rel else 0

    info = {
        'version': version,
        'total_samples': total_samples,
        'loop_offset': loop_offset,
        'loop_samples': loop_samples,
        'has_psg': sn76489_clock != 0,
        'has_ym2612': ym2612_clock != 0,
        'has_dac': False,
    }

    commands = []
    pcm_data = bytearray()
    pcm_seek_pos = 0
    current_offset = data_offset

    pos = data_offset
    while pos < len(data):
        # Check if we're at loop point
        if loop_offset and pos == loop_offset:
            commands.append(VGMCommand(type='loop'))

        cmd = data[pos]

        if cmd == 0x66:  # End
            commands.append(VGMCommand(type='end'))
            break

        elif cmd == 0x50:  # PSG write
            val = data[pos + 1]
            commands.append(VGMCommand(type='psg', val=val))
            pos += 2

        elif cmd == 0x52:  # YM2612 port 0
            reg = data[pos + 1]
            val = data[pos + 2]
            commands.append(VGMCommand(type='ym0', reg=reg, val=val))
            pos += 3

        elif cmd == 0x53:  # YM2612 port 1
            reg = data[pos + 1]
            val = data[pos + 2]
            commands.append(VGMCommand(type='ym1', reg=reg, val=val))
            pos += 3

        elif cmd == 0x61:  # Wait N samples
            samples = struct.unpack('<H', data[pos + 1:pos + 3])[0]
            commands.append(VGMCommand(type='wait', samples=samples))
            pos += 3

        elif cmd == 0x62:  # Wait 735 samples
            commands.append(VGMCommand(type='wait', samples=735))
            pos += 1

        elif cmd == 0x63:  # Wait 882 samples
            commands.append(VGMCommand(type='wait', samples=882))
            pos += 1

        elif cmd >= 0x70 and cmd <= 0x7F:  # Short wait
            samples = (cmd & 0x0F) + 1
            commands.append(VGMCommand(type='wait', samples=samples))
            pos += 1

        elif cmd == 0x67:  # Data block
            block_type = data[pos + 2]
            block_size = struct.unpack('<I', data[pos + 3:pos + 7])[0]
            if block_type == 0x00 and not strip_dac:  # YM2612 PCM
                pcm_data.extend(data[pos + 7:pos + 7 + block_size])
                info['has_dac'] = True
            pos += 7 + block_size

        elif cmd >= 0x80 and cmd <= 0x8F:  # DAC + wait
            wait = cmd & 0x0F
            if strip_dac:
                if wait > 0:
                    commands.append(VGMCommand(type='wait', samples=wait))
            else:
                commands.append(VGMCommand(type='dac', samples=wait, pcm_pos=pcm_seek_pos))
                pcm_seek_pos += 1
                info['has_dac'] = True
            pos += 1

        elif cmd == 0xE0:  # PCM seek
            pcm_seek_pos = struct.unpack('<I', data[pos + 1:pos + 5])[0]
            if not strip_dac:
                commands.append(VGMCommand(type='dac_seek', pcm_pos=pcm_seek_pos))
            pos += 5

        # Skip other commands
        elif cmd >= 0x30 and cmd <= 0x3F:
            pos += 2
        elif cmd >= 0x40 and cmd <= 0x4E:
            pos += 3
        elif cmd == 0x4F:
            pos += 2
        elif cmd >= 0x51 and cmd <= 0x5F:
            pos += 3
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
        elif cmd >= 0xA0 and cmd <= 0xBF:
            pos += 3
        elif cmd >= 0xC0 and cmd <= 0xDF:
            pos += 4
        elif cmd >= 0xE1 and cmd <= 0xFF:
            pos += 5
        else:
            pos += 1

    return commands, bytes(pcm_data), info


def compress_pcm_dpcm(pcm_data: bytes) -> Tuple[bytes, int]:
    """Compress PCM data using 4-bit DPCM encoding.

    Returns (compressed_data, original_size).
    Each byte encodes 2 samples as 4-bit delta indices.
    """
    if not pcm_data:
        return b'', 0

    compressed = bytearray()
    current = pcm_data[0]
    compressed.append(current)  # Store initial sample

    i = 1
    while i < len(pcm_data):
        # Encode two samples per byte
        nibbles = []
        for _ in range(2):
            if i >= len(pcm_data):
                nibbles.append(7)  # Delta 0
                break

            target = pcm_data[i]
            delta = target - current

            # Find best matching step
            best_idx = 7  # Default to 0 delta
            best_error = abs(delta)
            for idx, step in enumerate(DPCM_STEPS):
                error = abs(delta - step)
                if error < best_error:
                    best_error = error
                    best_idx = idx

            # Apply the step
            current = max(0, min(255, current + DPCM_STEPS[best_idx]))
            nibbles.append(best_idx)
            i += 1

        # Pack two nibbles into one byte
        if len(nibbles) == 2:
            compressed.append((nibbles[0] << 4) | nibbles[1])
        else:
            compressed.append(nibbles[0] << 4)

    return bytes(compressed), len(pcm_data)


def downsample_dac_commands(commands: List[VGMCommand], factor: int) -> List[VGMCommand]:
    """Reduce DAC sample rate by keeping every Nth sample.

    This is lossy but can significantly reduce size for high sample rate DAC.
    factor=2 means keep every other sample (halve the rate).
    """
    if factor <= 1:
        return commands

    result = []
    dac_count = 0
    accumulated_wait = 0

    for cmd in commands:
        if cmd.type == 'dac':
            dac_count += 1
            accumulated_wait += cmd.samples

            if dac_count % factor == 0:
                # Keep this sample with accumulated wait time
                result.append(VGMCommand(
                    type='dac',
                    samples=accumulated_wait,
                    pcm_pos=cmd.pcm_pos
                ))
                accumulated_wait = 0
        elif cmd.type == 'dac_seek':
            # Adjust seek position for downsampling
            result.append(VGMCommand(
                type='dac_seek',
                pcm_pos=cmd.pcm_pos // factor
            ))
        elif cmd.type == 'wait':
            accumulated_wait += cmd.samples
            if accumulated_wait > 0 and dac_count % factor != 0:
                # Flush accumulated wait
                result.append(VGMCommand(type='wait', samples=accumulated_wait))
                accumulated_wait = 0
            else:
                result.append(cmd)
        else:
            if accumulated_wait > 0:
                result.append(VGMCommand(type='wait', samples=accumulated_wait))
                accumulated_wait = 0
            result.append(cmd)

    if accumulated_wait > 0:
        result.append(VGMCommand(type='wait', samples=accumulated_wait))

    return result


def downsample_pcm(pcm_data: bytes, factor: int) -> bytes:
    """Downsample PCM data by keeping every Nth sample."""
    if factor <= 1 or not pcm_data:
        return pcm_data
    return bytes(pcm_data[i] for i in range(0, len(pcm_data), factor))


@dataclass
class Sample:
    """A detected PCM sample."""
    start: int          # Start position in PCM data
    length: int         # Number of samples
    avg_rate: int       # Average playback rate (samples between DAC writes)


def detect_samples(commands: List[VGMCommand], pcm_data: bytes) -> Tuple[List[Sample], Dict[int, int]]:
    """Detect unique samples from DAC seek positions.

    Analyzes the actual timing between DAC writes to determine the true
    playback rate for each sample. This includes any wait commands that
    occur during DAC streaming.

    Returns:
        samples: List of detected Sample objects
        seek_to_sample: Dict mapping seek position to sample index
    """
    from collections import defaultdict

    # Find all DAC seek positions and collect the streams that follow
    # Track: start_pos -> [(length, total_wait_samples), ...]
    streams: Dict[int, List[Tuple[int, int]]] = defaultdict(list)

    current_start = 0
    current_length = 0
    total_wait = 0
    in_dac_stream = False

    for cmd in commands:
        if cmd.type == 'dac_seek':
            # Save previous stream if any
            if current_length > 0:
                streams[current_start].append((current_length, total_wait))
            # Start new stream
            current_start = cmd.pcm_pos
            current_length = 0
            total_wait = 0
            in_dac_stream = True

        elif cmd.type == 'dac':
            current_length += 1
            total_wait += cmd.samples  # Wait embedded in DAC command
            in_dac_stream = True

        elif cmd.type == 'wait':
            # If we're in a DAC stream, this wait is part of the sample timing
            # (some VGMs use separate wait commands between DAC writes)
            if in_dac_stream and current_length > 0:
                total_wait += cmd.samples

        elif cmd.type in ('end', 'loop'):
            # End of stream
            if current_length > 0:
                streams[current_start].append((current_length, total_wait))
            current_length = 0
            total_wait = 0
            in_dac_stream = False

        else:
            # Other commands (ym0, ym1, psg) don't affect DAC timing
            # but if we see them, the DAC stream continues
            pass

    # Save final stream
    if current_length > 0:
        streams[current_start].append((current_length, total_wait))

    # Create samples from unique start positions, merging overlapping ones
    samples = []
    seek_to_sample = {}

    # Sort by start position
    sorted_starts = sorted(streams.keys())

    i = 0
    while i < len(sorted_starts):
        start_pos = sorted_starts[i]
        stream_instances = streams[start_pos]

        # Use the longest instance
        max_length = max(inst[0] for inst in stream_instances)

        # Calculate average rate
        total_samples = sum(inst[0] for inst in stream_instances)
        total_waits = sum(inst[1] for inst in stream_instances)
        avg_rate = total_waits // total_samples if total_samples > 0 else 3

        # Collect all seek positions that fall within this sample's range
        sample_end = start_pos + max_length
        merged_positions = [start_pos]

        j = i + 1
        while j < len(sorted_starts) and sorted_starts[j] < sample_end:
            merged_positions.append(sorted_starts[j])
            # Update length if this stream extends further
            other_instances = streams[sorted_starts[j]]
            for inst in other_instances:
                potential_end = sorted_starts[j] + inst[0]
                if potential_end > sample_end:
                    sample_end = potential_end
                    max_length = sample_end - start_pos
            j += 1

        # Only create sample if it's within PCM bounds
        if start_pos + max_length <= len(pcm_data):
            sample_idx = len(samples)
            samples.append(Sample(start=start_pos, length=max_length, avg_rate=max(1, avg_rate)))

            # Map all merged positions to this sample
            for pos in merged_positions:
                seek_to_sample[pos] = sample_idx

            if len(merged_positions) > 1:
                print(f"    Sample {sample_idx}: start={start_pos}, len={max_length}, rate={avg_rate} (~{44100/max(1,avg_rate):.0f}Hz) [merged {len(merged_positions)} seeks]")
            else:
                print(f"    Sample {sample_idx}: start={start_pos}, len={max_length}, rate={avg_rate} (~{44100/max(1,avg_rate):.0f}Hz)")

        i = j  # Skip all merged positions

    return samples, seek_to_sample


def convert_dac_to_streaming(commands: List[VGMCommand]) -> List[VGMCommand]:
    """Convert DAC commands to streaming playback.

    Each dac_seek becomes a 'dac_start' with position and rate.
    DAC commands become waits (preserving timing).
    Player seeks to position and outputs bytes at rate during waits.
    """
    result = []
    pending_seek_pos = None
    dac_rate_accum = 0
    dac_count = 0

    for cmd in commands:
        if cmd.type == 'dac_seek':
            # New seek - if we had a pending one, emit it now with accumulated rate
            if pending_seek_pos is not None and dac_count > 0:
                avg_rate = dac_rate_accum // dac_count
                result.append(VGMCommand(type='dac_start', pcm_pos=pending_seek_pos, samples=max(1, avg_rate)))

            pending_seek_pos = cmd.pcm_pos
            dac_rate_accum = 0
            dac_count = 0

        elif cmd.type == 'dac':
            # First DAC after seek - emit the start command
            if pending_seek_pos is not None and dac_count == 0:
                # We'll emit after we know the rate from this DAC
                pass

            dac_rate_accum += cmd.samples
            dac_count += 1

            # Convert DAC wait to regular wait (preserve timing!)
            if cmd.samples > 0:
                result.append(VGMCommand(type='wait', samples=cmd.samples))

        else:
            # Non-DAC command - emit pending seek if any
            if pending_seek_pos is not None and dac_count > 0:
                avg_rate = dac_rate_accum // dac_count
                result.append(VGMCommand(type='dac_start', pcm_pos=pending_seek_pos, samples=max(1, avg_rate)))
                pending_seek_pos = None

            result.append(cmd)

    # Emit final pending seek
    if pending_seek_pos is not None and dac_count > 0:
        avg_rate = dac_rate_accum // dac_count
        result.append(VGMCommand(type='dac_start', pcm_pos=pending_seek_pos, samples=max(1, avg_rate)))

    return result


def optimize_commands(commands: List[VGMCommand]) -> List[VGMCommand]:
    """Remove redundant register writes by tracking state.

    Only removes consecutive duplicate writes to the same register.
    We cannot track state across Key On events because the YM2612
    may have different internal state after notes are triggered.
    """

    optimized = []
    removed = 0

    # Track the previous command for each port/register combination
    # We only remove if the IMMEDIATELY PREVIOUS write to this register
    # had the same value (true consecutive duplicate)
    prev_ym0: Dict[int, int] = {}  # reg -> val of last write
    prev_ym1: Dict[int, int] = {}

    for cmd in commands:
        if cmd.type == 'ym0':
            # Key On register (0x28) - never optimize, and clear state
            # because channel state may change after key events
            if cmd.reg == 0x28:
                # Clear tracked state for affected channel's registers
                # This ensures subsequent writes aren't incorrectly removed
                channel = cmd.val & 0x07  # Channel bits
                if channel <= 2:
                    # Port 0 channels 0-2: clear channel-specific registers
                    for base in [0x30, 0x34, 0x38, 0x3C,  # DT1/MUL
                                 0x40, 0x44, 0x48, 0x4C,  # TL
                                 0x50, 0x54, 0x58, 0x5C,  # RS/AR
                                 0x60, 0x64, 0x68, 0x6C,  # AM/D1R
                                 0x70, 0x74, 0x78, 0x7C,  # D2R
                                 0x80, 0x84, 0x88, 0x8C,  # D1L/RR
                                 0x90, 0x94, 0x98, 0x9C,  # SSG-EG
                                 0xA0, 0xA4,              # Freq
                                 0xB0, 0xB4]:             # FB/Algo, LR/AMS/FMS
                        reg = base + channel
                        prev_ym0.pop(reg, None)
                optimized.append(cmd)
            else:
                # Regular register - only remove true consecutive duplicates
                if prev_ym0.get(cmd.reg) == cmd.val:
                    removed += 1
                    continue
                prev_ym0[cmd.reg] = cmd.val
                optimized.append(cmd)

        elif cmd.type == 'ym1':
            if cmd.reg == 0x28:
                channel = cmd.val & 0x07
                if channel >= 4 and channel <= 6:
                    # Port 1 channels 4-6 (stored as 0-2 in port 1 regs)
                    ch_offset = channel - 4
                    for base in [0x30, 0x34, 0x38, 0x3C,
                                 0x40, 0x44, 0x48, 0x4C,
                                 0x50, 0x54, 0x58, 0x5C,
                                 0x60, 0x64, 0x68, 0x6C,
                                 0x70, 0x74, 0x78, 0x7C,
                                 0x80, 0x84, 0x88, 0x8C,
                                 0x90, 0x94, 0x98, 0x9C,
                                 0xA0, 0xA4,
                                 0xB0, 0xB4]:
                        reg = base + ch_offset
                        prev_ym1.pop(reg, None)
                optimized.append(cmd)
            else:
                if prev_ym1.get(cmd.reg) == cmd.val:
                    removed += 1
                    continue
                prev_ym1[cmd.reg] = cmd.val
                optimized.append(cmd)

        elif cmd.type == 'wait':
            # After a wait, we can't assume registers haven't been
            # touched by other means, so clear tracked state
            prev_ym0.clear()
            prev_ym1.clear()
            optimized.append(cmd)

        else:
            optimized.append(cmd)

    if removed > 0:
        print(f"  Removed {removed:,} redundant register writes")

    return optimized


def merge_waits(commands: List[VGMCommand]) -> List[VGMCommand]:
    """Merge consecutive wait commands."""

    merged = []
    pending_wait = 0

    for cmd in commands:
        if cmd.type == 'wait':
            pending_wait += cmd.samples
        else:
            if pending_wait > 0:
                merged.append(VGMCommand(type='wait', samples=pending_wait))
                pending_wait = 0
            merged.append(cmd)

    if pending_wait > 0:
        merged.append(VGMCommand(type='wait', samples=pending_wait))

    return merged


def build_dictionary(commands: List[VGMCommand]) -> List[Tuple[int, int, int]]:
    """Build frequency-sorted dictionary of (port, reg, val) tuples."""

    counter: Counter = Counter()

    for cmd in commands:
        if cmd.type == 'ym0':
            counter[(0, cmd.reg, cmd.val)] += 1
        elif cmd.type == 'ym1':
            counter[(1, cmd.reg, cmd.val)] += 1

    # Sort by frequency, take top 256
    entries = counter.most_common(256)

    return [(port, reg, val) for (port, reg, val), count in entries]


def encode_gep(commands: List[VGMCommand], dictionary: List[Tuple[int, int, int]],
               pcm_data: bytes, info: dict, max_chunk_size: int = 0) -> List[bytes]:
    """Encode commands into GEP format, optionally splitting into chunks."""

    # Build reverse lookup for dictionary
    dict_lookup = {entry: idx for idx, entry in enumerate(dictionary)}

    chunks = []
    current_chunk = bytearray()
    loop_chunk = 0xFFFF
    loop_offset = 0xFFFF
    total_samples = 0

    # Track DAC position for seeking
    last_dac_pos = 0

    # First pass: merge consecutive DAC with sequential PCM positions into DAC_RUN
    # This encodes as: DAC_RUN [count] [wait nibbles...]
    # Each pair of waits is packed into 1 byte (4 bits each, 0-15)
    merged_commands = []
    dac_runs_created = 0
    dac_cmds_merged = 0
    i = 0
    while i < len(commands):
        cmd = commands[i]

        # Check for DAC run opportunity (consecutive sequential PCM positions)
        if cmd.type == 'dac':
            dac_cmds = [cmd]
            j = i + 1
            while j < len(commands) and commands[j].type == 'dac':
                next_cmd = commands[j]
                # Sequential PCM position and wait fits in 4 bits
                if next_cmd.pcm_pos == dac_cmds[-1].pcm_pos + 1 and next_cmd.samples <= 15:
                    dac_cmds.append(next_cmd)
                    j += 1
                else:
                    break

            # Need at least 4 to be worthwhile: 4 VGM bytes -> 3 GEP bytes
            if len(dac_cmds) >= 4 and all(c.samples <= 15 for c in dac_cmds):
                merged_commands.append(VGMCommand(
                    type='dac_run',
                    pcm_pos=cmd.pcm_pos,
                    val=len(dac_cmds),
                    # Store wait times in a list (we'll access via reg field as index)
                    reg=i  # Store original index to reconstruct waits
                ))
                # Save wait times for later encoding
                merged_commands[-1]._waits = [c.samples for c in dac_cmds]
                dac_runs_created += 1
                dac_cmds_merged += len(dac_cmds)
                i = j
                continue

        merged_commands.append(cmd)
        i += 1

    if dac_runs_created > 0:
        print(f"  DAC runs: {dac_runs_created} runs ({dac_cmds_merged:,} cmds merged)")

    commands = merged_commands

    i = 0
    while i < len(commands):
        cmd = commands[i]

        # Check if we need to start a new chunk
        if max_chunk_size > 0 and len(current_chunk) > max_chunk_size - 100:
            # Find a good split point (after a wait)
            if cmd.type == 'wait' or cmd.type == 'end':
                current_chunk.append(CMD_CHUNK_END)
                chunks.append(bytes(current_chunk))
                current_chunk = bytearray()

        if cmd.type == 'loop':
            loop_chunk = len(chunks)
            loop_offset = len(current_chunk)
            current_chunk.append(CMD_LOOP_MARK)

        elif cmd.type == 'end':
            current_chunk.append(CMD_END)

        elif cmd.type == 'wait':
            samples = cmd.samples
            total_samples += samples

            # Encode wait efficiently
            while samples > 0:
                if samples <= 64:
                    current_chunk.append(CMD_WAIT_SHORT_BASE + samples - 1)
                    samples = 0
                elif samples >= SAMPLES_PER_FRAME and samples <= 16 * SAMPLES_PER_FRAME:
                    frames = min(samples // SAMPLES_PER_FRAME, 16)
                    current_chunk.append(CMD_WAIT_FRAMES_BASE + frames - 1)
                    samples -= frames * SAMPLES_PER_FRAME
                elif samples <= 65535:
                    current_chunk.append(CMD_WAIT_LONG)
                    current_chunk.extend(struct.pack('<H', samples))
                    samples = 0
                else:
                    # Very long wait - use max and continue
                    current_chunk.append(CMD_WAIT_LONG)
                    current_chunk.extend(struct.pack('<H', 65535))
                    samples -= 65535

        elif cmd.type == 'psg':
            # Check if next commands are also PSG (group them)
            psg_vals = [cmd.val]
            j = i + 1
            while j < len(commands) and commands[j].type == 'psg' and len(psg_vals) < 16:
                psg_vals.append(commands[j].val)
                j += 1

            if len(psg_vals) == 1:
                current_chunk.append(CMD_PSG_RAW)
                current_chunk.append(cmd.val)
            else:
                current_chunk.append(CMD_PSG_MULTI_BASE + len(psg_vals) - 1)
                current_chunk.extend(psg_vals)
                i = j - 1  # Skip the grouped commands

        elif cmd.type in ('ym0', 'ym1'):
            port = 0 if cmd.type == 'ym0' else 1
            entry = (port, cmd.reg, cmd.val)

            # Use dictionary if available, otherwise raw write
            if entry in dict_lookup:
                idx = dict_lookup[entry]
                if idx < 64:
                    current_chunk.append(CMD_DICT_WRITE_BASE + idx)
                else:
                    current_chunk.append(CMD_DICT_EXT)
                    current_chunk.append(idx)
            else:
                current_chunk.append(CMD_YM_RAW_P0 if port == 0 else CMD_YM_RAW_P1)
                current_chunk.append(cmd.reg)
                current_chunk.append(cmd.val)

        elif cmd.type == 'dac':
            wait = cmd.samples
            if wait <= 15:
                current_chunk.append(CMD_DAC_WAIT_BASE + wait)
            else:
                current_chunk.append(CMD_DAC_WRITE)
                if wait > 0:
                    if wait <= 64:
                        current_chunk.append(CMD_WAIT_SHORT_BASE + wait - 1)
                    else:
                        current_chunk.append(CMD_WAIT_LONG)
                        current_chunk.extend(struct.pack('<H', wait))
            total_samples += wait
            last_dac_pos = cmd.pcm_pos + 1

        elif cmd.type == 'dac_seek':
            if cmd.pcm_pos != last_dac_pos:
                current_chunk.append(CMD_DAC_SEEK)
                current_chunk.extend(struct.pack('<H', cmd.pcm_pos))
                last_dac_pos = cmd.pcm_pos

        elif cmd.type == 'dac_block':
            count = cmd.val
            wait = cmd.samples

            # Seek if needed
            if cmd.pcm_pos != last_dac_pos:
                current_chunk.append(CMD_DAC_SEEK)
                current_chunk.extend(struct.pack('<H', cmd.pcm_pos))

            # DAC_BLOCK: [count] [wait_per_sample]
            # For very long blocks, split into 255-sample chunks
            remaining = count
            while remaining > 0:
                chunk_count = min(remaining, 255)
                current_chunk.append(CMD_DAC_BLOCK)
                current_chunk.append(chunk_count)
                current_chunk.append(wait)
                total_samples += chunk_count * wait
                remaining -= chunk_count

            last_dac_pos = cmd.pcm_pos + count

        elif cmd.type == 'dac_run':
            count = cmd.val
            waits = cmd._waits

            # Seek if needed
            if cmd.pcm_pos != last_dac_pos:
                current_chunk.append(CMD_DAC_SEEK)
                current_chunk.extend(struct.pack('<H', cmd.pcm_pos))

            # DAC_RUN format: 0xB9 [count_hi:4][count_lo:4] [wait0:4][wait1:4] ...
            # Actually simpler: 0xB9 [count] [packed waits...]
            # For now, split into 255-sample max runs
            offset = 0
            while offset < count:
                run_len = min(count - offset, 255)
                current_chunk.append(0xB9)  # DAC_RUN command
                current_chunk.append(run_len)

                # Pack wait nibbles
                run_waits = waits[offset:offset + run_len]
                for k in range(0, len(run_waits), 2):
                    w0 = run_waits[k]
                    w1 = run_waits[k + 1] if k + 1 < len(run_waits) else 0
                    current_chunk.append((w0 << 4) | w1)

                total_samples += sum(run_waits)
                offset += run_len

            last_dac_pos = cmd.pcm_pos + count

        elif cmd.type == 'sample_start':
            # Sample start: trigger sample playback with rate
            # Player reconstructs DAC timing from rate during waits
            sample_id = cmd.val
            rate = cmd.samples
            if sample_id < 16 and rate <= 15:
                # Quick 2-byte encoding: [0xD0+id] [rate]
                current_chunk.append(CMD_SAMPLE_BASE + sample_id)
                current_chunk.append(rate)
            else:
                # Extended encoding: [0xBB] [id] [rate]
                current_chunk.append(CMD_SAMPLE_PLAY)
                current_chunk.append(sample_id)
                current_chunk.append(rate)

        elif cmd.type == 'dac_start':
            # DAC stream start: seek to position and play at rate
            pos = cmd.pcm_pos
            rate = cmd.samples
            current_chunk.append(CMD_DAC_START)
            current_chunk.append(pos & 0xFF)
            current_chunk.append((pos >> 8) & 0xFF)
            current_chunk.append(rate)

        i += 1

    if current_chunk:
        chunks.append(bytes(current_chunk))

    return chunks, loop_chunk, loop_offset, total_samples


def generate_header(name: str, dictionary: List[Tuple[int, int, int]],
                    chunks: List[bytes], pcm_data: bytes, info: dict,
                    loop_chunk: int, loop_offset: int, total_samples: int,
                    samples: List[Sample] = None) -> str:
    """Generate C header file with GEP data."""

    # Build flags
    flags = 0
    if info['has_psg']:
        flags |= GEP_FLAG_PSG
    if info['has_ym2612']:
        flags |= GEP_FLAG_YM2612
    if info['has_dac'] and pcm_data:
        flags |= GEP_FLAG_DAC
    if len(chunks) > 1:
        flags |= GEP_FLAG_MULTI_CHUNK
    if info.get('has_dpcm'):
        flags |= GEP_FLAG_DPCM
    if samples:
        flags |= GEP_FLAG_SAMPLES

    # Calculate sizes
    dict_size = len(dictionary) * 3  # 3 bytes per entry: port, reg, val
    pcm_size = len(pcm_data)
    cmd_size = sum(len(c) for c in chunks)
    total_size = 16 + dict_size + pcm_size + cmd_size

    duration = total_samples / 44100.0

    def format_bytes(data: bytes, indent: str = "  ") -> str:
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hex_vals = ', '.join(f'0x{b:02X}' for b in chunk)
            lines.append(f'{indent}{hex_vals},')
        return '\n'.join(lines)

    # Build header
    header_bytes = bytearray()
    header_bytes.extend(GEP_MAGIC)
    header_bytes.extend(struct.pack('<H', flags))
    header_bytes.append(len(dictionary) if len(dictionary) < 256 else 0)
    header_bytes.append(1 if pcm_data else 0)
    header_bytes.extend(struct.pack('<I', total_samples))
    header_bytes.extend(struct.pack('<H', loop_chunk if loop_chunk <= 0xFFFF else 0xFFFF))
    header_bytes.extend(struct.pack('<H', loop_offset if loop_offset <= 0xFFFF else 0xFFFF))

    # Build dictionary bytes
    # Format: [port, reg, val] - 3 bytes per entry
    # Port is 0 or 1, reg is 0x00-0xB6, val is 0x00-0xFF
    dict_bytes = bytearray()
    for port, reg, val in dictionary:
        dict_bytes.append(port)
        dict_bytes.append(reg)
        dict_bytes.append(val)

    output = f'''// =============================================================================
// GEP (Genesis Engine Packed) data generated by vgm2gep.py
// Part of FM-90s Genesis Engine
// =============================================================================
//
// Duration: {duration:.2f}s
// Chunks: {len(chunks)}
// Dictionary: {len(dictionary)} entries
// PCM: {pcm_size} bytes
// Total: {total_size} bytes ({total_size/1024:.1f} KB)
// Loop: {"Yes" if loop_chunk != 0xFFFF else "No"}
//
// =============================================================================

#ifndef {name.upper()}_GEP_H
#define {name.upper()}_GEP_H

#include <Arduino.h>

#ifdef __AVR__
#include <avr/pgmspace.h>
#define GEP_PROGMEM PROGMEM
#else
#define GEP_PROGMEM
#endif

// Header (16 bytes)
const uint8_t {name}_gep_header[] GEP_PROGMEM = {{
{format_bytes(bytes(header_bytes))}
}};

// Dictionary ({len(dictionary)} entries, {dict_size} bytes)
const uint8_t {name}_gep_dict[] GEP_PROGMEM = {{
{format_bytes(bytes(dict_bytes))}
}};

'''

    # PCM data (or nullptr placeholder)
    if pcm_data:
        output += f'''// PCM data ({pcm_size} bytes)
const uint8_t {name}_gep_pcm[] GEP_PROGMEM = {{
{format_bytes(pcm_data)}
}};

'''
    else:
        output += f'''// No PCM data
#define {name}_gep_pcm nullptr

'''

    # Samples table (or placeholder)
    if samples:
        # Sample table format: [start_lo, start_hi, length_lo, length_hi, rate] - 5 bytes per sample
        sample_bytes = bytearray()
        for s in samples:
            sample_bytes.extend(struct.pack('<H', s.start))
            sample_bytes.extend(struct.pack('<H', s.length))
            sample_bytes.append(s.avg_rate)

        output += f'''// Samples table ({len(samples)} samples, {len(sample_bytes)} bytes)
// Format: [start_lo, start_hi, length_lo, length_hi, rate] per sample
const uint8_t {name}_gep_samples[] GEP_PROGMEM = {{
{format_bytes(bytes(sample_bytes))}
}};
const uint8_t {name}_gep_sample_count = {len(samples)};

'''
    else:
        output += f'''// No samples
#define {name}_gep_samples nullptr
#define {name}_gep_sample_count 0

'''

    # Output chunks
    for i, chunk in enumerate(chunks):
        output += f'''// Command chunk {i} ({len(chunk)} bytes)
const uint8_t {name}_gep_{i}[] GEP_PROGMEM = {{
{format_bytes(chunk)}
}};

'''

    # Always output consistent interface
    if len(chunks) > 1:
        chunk_ptrs = ', '.join(f'{name}_gep_{i}' for i in range(len(chunks)))
        chunk_sizes = ', '.join(str(len(c)) for c in chunks)
        output += f'''// Multi-chunk data
const uint8_t* const {name}_gep_chunks[] GEP_PROGMEM = {{ {chunk_ptrs} }};
const uint16_t {name}_gep_chunk_sizes[] GEP_PROGMEM = {{ {chunk_sizes} }};
const uint8_t {name}_gep_chunk_count = {len(chunks)};
#define {name}_gep_data nullptr

'''
    else:
        output += f'''// Single chunk
#define {name}_gep_data {name}_gep_0
#define {name}_gep_chunks nullptr
#define {name}_gep_chunk_sizes nullptr
#define {name}_gep_chunk_count 1

'''

    output += f'''#endif // {name.upper()}_GEP_H
'''

    return output


def convert_file(input_path: str, output_path: str = None, name: str = None,
                 strip_dac: bool = False, platform: str = None,
                 dac_downsample: int = 1, dpcm: bool = False,
                 use_samples: bool = False) -> bool:
    """Convert a VGM file to GEP format."""

    input_path = Path(input_path)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        return False

    print(f"Converting: {input_path.name}")

    # Read and decompress
    with open(input_path, 'rb') as f:
        raw_data = f.read()

    try:
        data = decompress_vgz(raw_data)
    except Exception as e:
        print(f"Error decompressing: {e}", file=sys.stderr)
        return False

    original_size = len(data)
    print(f"  Original VGM: {original_size:,} bytes")

    # Parse VGM
    try:
        commands, pcm_data, info = parse_vgm(data, strip_dac)
    except Exception as e:
        print(f"Error parsing: {e}", file=sys.stderr)
        return False

    print(f"  Commands: {len(commands):,}")
    if pcm_data:
        print(f"  PCM data: {len(pcm_data):,} bytes")

    # Streaming DAC - convert DAC commands to seek+rate triggers
    samples = None  # Not using sample table anymore
    if use_samples and pcm_data and not strip_dac:
        commands = convert_dac_to_streaming(commands)
        print(f"  Commands after DAC streaming conversion: {len(commands):,}")

    # DAC downsampling (lossy) - skip if using samples
    if dac_downsample > 1 and pcm_data and not use_samples:
        print(f"  Downsampling DAC by {dac_downsample}x (lossy)")
        commands = downsample_dac_commands(commands, dac_downsample)
        pcm_data = downsample_pcm(pcm_data, dac_downsample)
        print(f"  PCM after downsample: {len(pcm_data):,} bytes")

    # DPCM compression (lossy) - skip if using samples (samples use raw PCM)
    if dpcm and pcm_data and not use_samples:
        pcm_data, orig_pcm_size = compress_pcm_dpcm(pcm_data)
        info['has_dpcm'] = True
        print(f"  DPCM compressed: {len(pcm_data):,} bytes ({len(pcm_data)*100//orig_pcm_size}% of original)")

    # Optimize
    commands = optimize_commands(commands)
    commands = merge_waits(commands)
    print(f"  After optimization: {len(commands):,} commands")

    # Build dictionary
    dictionary = build_dictionary(commands)
    print(f"  Dictionary: {len(dictionary)} entries")

    # Determine chunk size
    max_chunk = 0
    if platform:
        max_chunk = PLATFORM_LIMITS.get(platform.lower(), 0)
        print(f"  Platform: {platform} (max {max_chunk//1024}KB per chunk)")

    # Encode
    chunks, loop_chunk, loop_offset, total_samples = encode_gep(
        commands, dictionary, pcm_data, info, max_chunk
    )

    # Calculate sizes
    dict_size = len(dictionary) * 3  # 3 bytes per entry: port, reg, val
    pcm_size = len(pcm_data)
    cmd_size = sum(len(c) for c in chunks)
    total_size = 16 + dict_size + pcm_size + cmd_size

    print(f"  GEP size: {total_size:,} bytes ({total_size/1024:.1f} KB)")
    print(f"  Compression: {total_size/original_size*100:.1f}% of original")
    print(f"  Chunks: {len(chunks)}")

    # Generate output
    if name is None:
        name = ''.join(c if c.isalnum() else '_' for c in input_path.stem).lower()
        if name[0].isdigit():
            name = '_' + name

    if output_path is None:
        output_path = input_path.with_suffix('.h')
    else:
        output_path = Path(output_path)

    header = generate_header(name, dictionary, chunks, pcm_data, info,
                            loop_chunk, loop_offset, total_samples, samples)

    with open(output_path, 'w') as f:
        f.write(header)

    print(f"  Output: {output_path}")
    print()

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Convert VGM/VGZ to GEP (Genesis Engine Packed) format'
    )
    parser.add_argument('input', help='Input VGM/VGZ file')
    parser.add_argument('-o', '--output', help='Output header file')
    parser.add_argument('-n', '--name', help='Variable name prefix')
    parser.add_argument('--strip-dac', action='store_true',
                        help='Remove DAC/PCM data entirely')
    parser.add_argument('-p', '--platform',
                        choices=['uno', 'mega', 'teensy40', 'teensy41', 'esp32', 'rp2040'],
                        help='Target platform for chunk sizing')
    parser.add_argument('--dac-rate', type=int, default=1, metavar='N',
                        help='Downsample DAC by factor N (lossy, 2=half rate)')
    parser.add_argument('--dpcm', action='store_true',
                        help='Use 4-bit DPCM compression for PCM (lossy, ~50%% size)')
    parser.add_argument('--samples', action='store_true',
                        help='Detect repeating DAC samples and use trigger-based playback')

    args = parser.parse_args()

    success = convert_file(args.input, args.output, args.name,
                          args.strip_dac, args.platform,
                          args.dac_rate, args.dpcm, args.samples)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
