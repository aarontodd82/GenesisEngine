# MIDISynth

Turn your GenesisEngine into a USB MIDI synthesizer. Play the YM2612 FM chip and SN76489 PSG from any DAW, MIDI keyboard, or sequencer.

## Requirements

- Teensy 4.0 or 4.1
- GenesisEngine board with YM2612 and SN76489
- USB cable to computer

## Setup

### 1. Configure Arduino IDE

In the Arduino IDE, set the USB type to include MIDI:

**Tools → USB Type → Serial + MIDI**

### 2. Adjust Pin Configuration (if needed)

Open `MIDISynth.ino` and verify the pin definitions match your wiring:

```cpp
#define PIN_WR_P  2   // PSG write strobe
#define PIN_WR_Y  3   // YM2612 write strobe
#define PIN_IC_Y  4   // YM2612 reset
#define PIN_A0_Y  5   // YM2612 A0
#define PIN_A1_Y  6   // YM2612 A1
#define PIN_SCK   13  // Shift register clock
#define PIN_SDI   11  // Shift register data
```

### 3. Upload

Upload the sketch to your Teensy. After upload, your computer should see a new MIDI device called "Teensy MIDI" or similar.

## How It Works

### MIDI Channel Mapping

The MIDI channel mapping is fixed:

| MIDI Channel | Hardware | Description |
|--------------|----------|-------------|
| 1-6 | YM2612 FM | 6 FM synthesis channels |
| 7-9 | SN76489 Tone | 3 PSG square wave channels |
| 10 | SN76489 Noise | 1 PSG noise channel |

FM is always channels 1-6. PSG is always channels 7-10. This mapping does not change between modes.

### Patch Slots

The synth has two separate banks of patch storage in RAM:

- **16 FM patch slots** (0-15) for YM2612
- **8 PSG envelope slots** (0-7) for SN76489

Each MIDI channel is assigned to a slot. Use **Program Change** to switch which slot a channel uses. Multiple channels can use the same slot.

**Note:** Patches are stored in RAM only. All patches reset to defaults when powered off.

### Synth Modes

**Multi-timbral Mode** (default)
- Each FM channel (1-6) plays independently with its own patch
- Good for: layered arrangements, different instruments per channel

**Poly Mode**
- MIDI Channel 1 controls all 6 FM voices with the same patch
- Automatic voice allocation - play chords and voices are assigned automatically
- When all 6 voices are in use, the oldest note is stolen
- Good for: playing the synth like a keyboard instrument

Switch modes:
- **CC 126**: Multi-timbral mode
- **CC 127**: Poly mode

PSG channels (7-10) work the same in both modes.

## Default Patches

On startup, the synth loads 8 built-in FM patches and 4 PSG envelopes.

### FM Patches (slots 0-7)

| Slot | Sound | Default Channel |
|------|-------|-----------------|
| 0 | Electric Piano | Ch 1 |
| 1 | Synth Bass | Ch 2 |
| 2 | Brass | Ch 3 |
| 3 | Lead Synth | Ch 4 |
| 4 | Organ | Ch 5 |
| 5 | Strings | Ch 6 |
| 6 | Pluck/Guitar | — |
| 7 | Bell/Chime | — |
| 8-15 | (empty) | — |

### PSG Envelopes (slots 0-7)

| Slot | Sound | Default Channel |
|------|-------|-----------------|
| 0 | Short Pluck | Ch 7 |
| 1 | Sustain (organ-like) | Ch 8 |
| 2 | Slow Attack Pad | Ch 9 |
| 3 | Tremolo | — |
| 4-7 | (empty) | — |

Channel 10 (noise) has no envelope - it plays different noise types based on note:
- Low notes (0-63): Periodic noise (buzzy)
- High notes (64-127): White noise (hi-hats, snares)

## MIDI Messages

| Message | Effect |
|---------|--------|
| Note On/Off | Play notes |
| Program Change | Select patch slot (0-15 for FM, 0-7 for PSG) |
| Pitch Bend | Bend pitch ±2 semitones (FM only) |
| CC 1 (Mod Wheel) | Vibrato depth (FM only) |
| CC 7 (Volume) | Channel volume |
| CC 10 (Pan) | Stereo panning (FM only) |
| CC 126 | Switch to Multi-timbral mode |
| CC 127 | Switch to Poly mode |

### Poly Mode Behavior

In poly mode, MIDI Channel 1 messages affect all 6 FM voices:
- **Notes** are allocated across voices automatically
- **Program Change** sets the patch for all 6 voices
- **Pitch Bend** bends all active voices together
- **Volume/Mod Wheel** affects all voices

## Loading Custom Patches

### Install Python Tool

```bash
pip install python-rtmidi
```

### Find Your MIDI Port

```bash
python genesis_patch.py list-ports
```

Look for "Teensy MIDI" in the list.

### Load a Patch to a Channel

Send a patch file directly to an FM channel (0-5):

```bash
python genesis_patch.py load piano.tfi 0
```

This loads the patch and immediately applies it to the channel.

### Store a Patch to a Slot

Store a patch in a slot (0-15) without applying it:

```bash
python genesis_patch.py store bass.dmp 5
```

Then use Program Change to select slot 5 on any channel.

### Load a Patch Bank

GYB banks contain multiple patches ripped from games:

```bash
python genesis_patch.py bank sonic2.gyb
```

This loads up to 16 patches into slots 0-15. Use Program Change to select them.

### Supported Formats

| Format | Description |
|--------|-------------|
| TFI | 42-byte FM patch (Furnace, TFM Music Maker) |
| DMP | DefleMask preset |
| GYB | Patch bank ripped from games |

### Where to Get Patches

- **[VGMRips](https://vgmrips.net)** - GYB banks from classic Genesis games
- **[Furnace Tracker](https://github.com/tildearrow/furnace)** - Create your own, export as TFI
- **[DefleMask](https://www.deflemask.com)** - Export as DMP or TFI
- **[OPN2BankEditor](https://github.com/Wohlstand/OPN2BankEditor)** - Convert between formats

## Advanced: Real-Time Parameter Control

Tweak FM parameters in real-time via CC (affects the current patch on that channel):

| CC | Parameter | Range |
|----|-----------|-------|
| 14 | Algorithm | 0-7 |
| 15 | Feedback | 0-7 |
| 16-19 | Operator 1-4 Total Level | 0-127 |

## Troubleshooting

**No sound:**
- Check that your DAW is sending to the Teensy MIDI port
- Verify MIDI channel (1-6 for FM, 7-10 for PSG)
- Make sure volume (CC 7) isn't at zero

**MIDI device not showing up:**
- Confirm USB Type is set to "Serial + MIDI" before uploading
- Try a different USB cable
- Restart your DAW after connecting

**Python tool can't find Teensy:**
- Run `list-ports` to see available MIDI devices
- On Windows, the port may be named differently
- Make sure no other application has the MIDI port open

**Patches reset on power cycle:**
- This is expected - patches are stored in RAM only
- Use the Python tool to reload patches after power-on
- Or load a GYB bank at startup from your DAW
