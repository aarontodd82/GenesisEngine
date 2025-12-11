#!/usr/bin/env python3
"""
genesis_patch.py - Patch management tool for GenesisEngine MIDISynth

Commands:
    list-ports              List available MIDI ports
    load <file> <channel>   Load TFI/DMP patch to FM channel (1-6)
    store <file> <slot>     Store patch to slot (1-16)
    recall <slot> <channel> Recall patch from slot to channel
    bank <file>             Load GYB bank (use Program Change to select)
    convert <in> <out>      Convert between patch formats
    poly                    Switch to poly mode (6-voice polyphonic on Ch 1)
    multi                   Switch to multi-timbral mode (6 independent channels)

Requirements:
    pip install python-rtmidi

Examples:
    python genesis_patch.py list-ports
    python genesis_patch.py load piano.tfi 1
    python genesis_patch.py store bass.dmp 5
    python genesis_patch.py bank sonic2.gyb
    python genesis_patch.py poly
    python genesis_patch.py multi
"""

import sys
import struct
import argparse

try:
    import rtmidi
except ImportError:
    print("Error: python-rtmidi required. Install with: pip install python-rtmidi")
    sys.exit(1)


# =============================================================================
# MIDI Communication
# =============================================================================

MANUFACTURER_ID = 0x7D  # Educational/development use

# SysEx commands
CMD_LOAD_PATCH = 0x01
CMD_LOAD_PSG_ENV = 0x02
CMD_STORE_PATCH = 0x03
CMD_RECALL_PATCH = 0x04


def find_teensy_port(midiout):
    """Find the Teensy MIDI port"""
    ports = midiout.get_ports()
    for i, port in enumerate(ports):
        if "Teensy" in port or "MIDISynth" in port:
            return i
    return None


def send_sysex(midiout, cmd, data):
    """Send a SysEx message to the device"""
    # Format: F0 7D 00 <cmd> <data...> F7
    msg = [0xF0, MANUFACTURER_ID, 0x00, cmd] + list(data) + [0xF7]
    midiout.send_message(msg)


def send_patch_to_channel(midiout, channel, patch_data):
    """Send FM patch data to a channel (0-5 internal)"""
    if len(patch_data) != 42:
        raise ValueError(f"Patch must be 42 bytes, got {len(patch_data)}")
    send_sysex(midiout, CMD_LOAD_PATCH, [channel] + list(patch_data))
    print(f"Sent patch to FM channel {channel + 1}")


def store_patch_to_slot(midiout, slot, patch_data):
    """Store FM patch to a slot (0-15 internal)"""
    if len(patch_data) != 42:
        raise ValueError(f"Patch must be 42 bytes, got {len(patch_data)}")
    send_sysex(midiout, CMD_STORE_PATCH, [slot] + list(patch_data))
    print(f"Stored patch to slot {slot + 1}")


def recall_patch(midiout, slot, channel):
    """Recall patch from slot to channel (0-based internal)"""
    send_sysex(midiout, CMD_RECALL_PATCH, [channel, slot])
    print(f"Recalled slot {slot + 1} to channel {channel + 1}")


# =============================================================================
# Patch File Parsing
# =============================================================================

def load_tfi(filename):
    """Load a TFI file (42 bytes)"""
    with open(filename, 'rb') as f:
        data = f.read(42)
    if len(data) != 42:
        raise ValueError(f"TFI file should be 42 bytes, got {len(data)}")
    return data


def load_dmp(filename):
    """Load a DMP file and convert to TFI format"""
    with open(filename, 'rb') as f:
        data = f.read()

    if len(data) < 4:
        raise ValueError("DMP file too short")

    version = data[0]
    if version == 0x0B:  # Version 11
        system = data[1]
        mode = data[2]
        if system != 0x02:
            raise ValueError(f"Not a Genesis DMP (system={system})")
        if mode != 0x01:
            raise ValueError("Not an FM instrument (mode=0, this is PSG)")
        return parse_dmp_v11(data)
    elif version == 0x0A:  # Version 10
        mode = data[1]
        if mode != 0x01:
            raise ValueError("Not an FM instrument")
        return parse_dmp_v10(data)
    else:
        raise ValueError(f"Unsupported DMP version: {version}")


def parse_dmp_v11(data):
    """Parse DMP v11 format to TFI"""
    # v11: version, system, mode, lfo, fb, alg, lfo2, then operators
    # Byte 3: LFO (PMS)
    # Byte 4: FB
    # Byte 5: ALG
    # Byte 6: LFO2 (AMS)
    fb = data[4]
    alg = data[5]

    tfi = bytearray(42)
    tfi[0] = alg
    tfi[1] = fb

    # Parse 4 operators starting at byte 7
    # DMP op order: MUL, TL, AR, DR, SL, RR, AM, RS, DT, D2R, SSG (11 bytes)
    # TFI op order: MUL, DT, TL, RS, AR, DR, SR, RR, SL, SSG (10 bytes)
    pos = 7
    for op in range(4):
        mul = data[pos]
        tl = data[pos + 1]
        ar = data[pos + 2]
        dr = data[pos + 3]
        sl = data[pos + 4]
        rr = data[pos + 5]
        am = data[pos + 6]
        rs = data[pos + 7]
        dt = data[pos + 8]
        d2r = data[pos + 9]
        ssg = data[pos + 10]

        # Write to TFI format
        tfi_pos = 2 + op * 10
        tfi[tfi_pos + 0] = mul
        tfi[tfi_pos + 1] = dt
        tfi[tfi_pos + 2] = tl
        tfi[tfi_pos + 3] = rs
        tfi[tfi_pos + 4] = ar
        tfi[tfi_pos + 5] = dr
        tfi[tfi_pos + 6] = d2r  # SR = D2R
        tfi[tfi_pos + 7] = rr
        tfi[tfi_pos + 8] = sl
        tfi[tfi_pos + 9] = ssg

        pos += 11

    return bytes(tfi)


def parse_dmp_v10(data):
    """Parse DMP v10 format to TFI"""
    # v10: version, mode, lfo, fb, alg, lfo2, then operators
    fb = data[3]
    alg = data[4]

    tfi = bytearray(42)
    tfi[0] = alg
    tfi[1] = fb

    pos = 6
    for op in range(4):
        mul = data[pos]
        tl = data[pos + 1]
        ar = data[pos + 2]
        dr = data[pos + 3]
        sl = data[pos + 4]
        rr = data[pos + 5]
        am = data[pos + 6]
        rs = data[pos + 7]
        dt = data[pos + 8]
        d2r = data[pos + 9]
        ssg = data[pos + 10]

        tfi_pos = 2 + op * 10
        tfi[tfi_pos + 0] = mul
        tfi[tfi_pos + 1] = dt
        tfi[tfi_pos + 2] = tl
        tfi[tfi_pos + 3] = rs
        tfi[tfi_pos + 4] = ar
        tfi[tfi_pos + 5] = dr
        tfi[tfi_pos + 6] = d2r
        tfi[tfi_pos + 7] = rr
        tfi[tfi_pos + 8] = sl
        tfi[tfi_pos + 9] = ssg

        pos += 11

    return bytes(tfi)


def load_gyb(filename):
    """Load a GYB bank file, returns list of (name, tfi_data) tuples"""
    with open(filename, 'rb') as f:
        data = f.read()

    # Check signature
    if len(data) < 5 or data[0] != 0x1A or data[1] != 0x0C:
        raise ValueError("Not a valid GYB file")

    version = data[2]
    melody_count = data[3]
    drum_count = data[4]

    patches = []

    if version == 1 or version == 2:
        # Version 1/2 format
        pos = 5

        # Skip LFO speed byte in v2
        if version == 2:
            pos += 1

        # Read melody instruments
        for i in range(melody_count):
            if pos + 32 > len(data):
                break
            patch_data = data[pos:pos + 32]
            pos += 32

            # Convert GYB format to TFI
            tfi = gyb_to_tfi(patch_data)
            patches.append((f"Melody {i}", tfi))

        # Read drum instruments
        for i in range(drum_count):
            if pos + 32 > len(data):
                break
            patch_data = data[pos:pos + 32]
            pos += 32
            tfi = gyb_to_tfi(patch_data)
            patches.append((f"Drum {i}", tfi))

    return patches


def gyb_to_tfi(gyb_data):
    """Convert GYB instrument data to TFI format"""
    # GYB stores raw register values
    # We need to extract and reformat to TFI

    tfi = bytearray(42)

    # Algorithm and Feedback are in byte 0 of GYB
    alg_fb = gyb_data[0]
    tfi[0] = alg_fb & 0x07  # Algorithm
    tfi[1] = (alg_fb >> 3) & 0x07  # Feedback

    # Operator data follows
    # GYB format: DT/MUL, TL, RS/AR, AM/DR, SR, SL/RR, SSG-EG per operator
    for op in range(4):
        gyb_pos = 1 + op * 7
        tfi_pos = 2 + op * 10

        dt_mul = gyb_data[gyb_pos + 0]
        tl = gyb_data[gyb_pos + 1]
        rs_ar = gyb_data[gyb_pos + 2]
        am_dr = gyb_data[gyb_pos + 3]
        sr = gyb_data[gyb_pos + 4]
        sl_rr = gyb_data[gyb_pos + 5]
        ssg = gyb_data[gyb_pos + 6] if gyb_pos + 6 < len(gyb_data) else 0

        tfi[tfi_pos + 0] = dt_mul & 0x0F  # MUL
        tfi[tfi_pos + 1] = (dt_mul >> 4) & 0x07  # DT
        tfi[tfi_pos + 2] = tl & 0x7F  # TL
        tfi[tfi_pos + 3] = (rs_ar >> 6) & 0x03  # RS
        tfi[tfi_pos + 4] = rs_ar & 0x1F  # AR
        tfi[tfi_pos + 5] = am_dr & 0x1F  # DR
        tfi[tfi_pos + 6] = sr & 0x1F  # SR
        tfi[tfi_pos + 7] = sl_rr & 0x0F  # RR
        tfi[tfi_pos + 8] = (sl_rr >> 4) & 0x0F  # SL
        tfi[tfi_pos + 9] = ssg & 0x0F  # SSG

    return bytes(tfi)


def load_patch(filename):
    """Load a patch file, auto-detecting format"""
    ext = filename.lower().split('.')[-1]

    if ext == 'tfi':
        return load_tfi(filename)
    elif ext == 'dmp':
        return load_dmp(filename)
    else:
        raise ValueError(f"Unknown file format: {ext}")


def save_tfi(filename, patch_data):
    """Save patch data as TFI file"""
    with open(filename, 'wb') as f:
        f.write(patch_data)
    print(f"Saved {filename}")


# =============================================================================
# CLI Commands
# =============================================================================

def cmd_list_ports(args):
    """List available MIDI ports"""
    midiout = rtmidi.MidiOut()
    ports = midiout.get_ports()

    if not ports:
        print("No MIDI output ports found")
        return

    print("Available MIDI output ports:")
    for i, port in enumerate(ports):
        marker = " <-- Teensy?" if "Teensy" in port else ""
        print(f"  {i}: {port}{marker}")


def cmd_load(args):
    """Load patch file to channel"""
    patch_data = load_patch(args.file)

    midiout = rtmidi.MidiOut()
    port_idx = find_teensy_port(midiout)
    if port_idx is None:
        print("Error: Teensy MIDI port not found. Use --port to specify.")
        return 1

    midiout.open_port(port_idx)
    send_patch_to_channel(midiout, args.channel - 1, patch_data)  # Convert to 0-based
    midiout.close_port()


def cmd_store(args):
    """Store patch to slot"""
    patch_data = load_patch(args.file)

    midiout = rtmidi.MidiOut()
    port_idx = find_teensy_port(midiout)
    if port_idx is None:
        print("Error: Teensy MIDI port not found")
        return 1

    midiout.open_port(port_idx)
    store_patch_to_slot(midiout, args.slot - 1, patch_data)  # Convert to 0-based
    midiout.close_port()


def cmd_recall(args):
    """Recall patch from slot to channel"""
    midiout = rtmidi.MidiOut()
    port_idx = find_teensy_port(midiout)
    if port_idx is None:
        print("Error: Teensy MIDI port not found")
        return 1

    midiout.open_port(port_idx)
    recall_patch(midiout, args.slot - 1, args.channel - 1)  # Convert to 0-based
    midiout.close_port()


def cmd_bank(args):
    """Load GYB bank"""
    patches = load_gyb(args.file)

    print(f"Loaded {len(patches)} patches from {args.file}:")
    for i, (name, _) in enumerate(patches):
        print(f"  {i + 1}: {name}")

    midiout = rtmidi.MidiOut()
    port_idx = find_teensy_port(midiout)
    if port_idx is None:
        print("Error: Teensy MIDI port not found")
        return 1

    midiout.open_port(port_idx)

    # Store patches to slots (up to 16)
    for i, (name, tfi) in enumerate(patches[:16]):
        store_patch_to_slot(midiout, i, tfi)

    midiout.close_port()
    print(f"\nStored {min(len(patches), 16)} patches to slots 1-{min(len(patches), 16)}")
    print("Use Program Change to select patches")


def cmd_convert(args):
    """Convert between formats"""
    patch_data = load_patch(args.input)
    save_tfi(args.output, patch_data)


def cmd_list_bank(args):
    """List patches in a bank file"""
    patches = load_gyb(args.file)
    print(f"Patches in {args.file}:")
    for i, (name, _) in enumerate(patches):
        print(f"  {i + 1}: {name}")


def cmd_poly(args):
    """Switch to poly mode"""
    midiout = rtmidi.MidiOut()
    port_idx = find_teensy_port(midiout)
    if port_idx is None:
        print("Error: Teensy MIDI port not found")
        return 1

    midiout.open_port(port_idx)
    # CC 127 on channel 1 = poly mode (any value works)
    midiout.send_message([0xB0, 127, 127])
    midiout.close_port()
    print("Switched to Poly mode (6-voice polyphonic on MIDI Ch 1)")


def cmd_multi(args):
    """Switch to multi-timbral mode"""
    midiout = rtmidi.MidiOut()
    port_idx = find_teensy_port(midiout)
    if port_idx is None:
        print("Error: Teensy MIDI port not found")
        return 1

    midiout.open_port(port_idx)
    # CC 126 on channel 1 = multi mode (any value works)
    midiout.send_message([0xB0, 126, 127])
    midiout.close_port()
    print("Switched to Multi-timbral mode (6 independent FM channels)")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Patch management tool for GenesisEngine MIDISynth"
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # list-ports
    sub = subparsers.add_parser('list-ports', help='List available MIDI ports')
    sub.set_defaults(func=cmd_list_ports)

    # load
    sub = subparsers.add_parser('load', help='Load patch to channel')
    sub.add_argument('file', help='Patch file (TFI or DMP)')
    sub.add_argument('channel', type=int, choices=range(1, 7), help='FM channel (1-6)')
    sub.set_defaults(func=cmd_load)

    # store
    sub = subparsers.add_parser('store', help='Store patch to slot')
    sub.add_argument('file', help='Patch file (TFI or DMP)')
    sub.add_argument('slot', type=int, choices=range(1, 17), help='Slot (1-16)')
    sub.set_defaults(func=cmd_store)

    # recall
    sub = subparsers.add_parser('recall', help='Recall patch from slot')
    sub.add_argument('slot', type=int, choices=range(1, 17), help='Slot (1-16)')
    sub.add_argument('channel', type=int, choices=range(1, 7), help='FM channel (1-6)')
    sub.set_defaults(func=cmd_recall)

    # bank
    sub = subparsers.add_parser('bank', help='Load GYB bank file')
    sub.add_argument('file', help='GYB bank file')
    sub.set_defaults(func=cmd_bank)

    # list-bank
    sub = subparsers.add_parser('list-bank', help='List patches in bank')
    sub.add_argument('file', help='GYB bank file')
    sub.set_defaults(func=cmd_list_bank)

    # convert
    sub = subparsers.add_parser('convert', help='Convert between formats')
    sub.add_argument('input', help='Input file')
    sub.add_argument('output', help='Output file (TFI)')
    sub.set_defaults(func=cmd_convert)

    # poly
    sub = subparsers.add_parser('poly', help='Switch to poly mode (6-voice on Ch 1)')
    sub.set_defaults(func=cmd_poly)

    # multi
    sub = subparsers.add_parser('multi', help='Switch to multi-timbral mode')
    sub.set_defaults(func=cmd_multi)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == '__main__':
    sys.exit(main() or 0)
