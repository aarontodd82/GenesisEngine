# MIDISynth

A MIDI synthesizer for the GenesisEngine board. Play the YM2612 FM chip and SN76489 PSG from any DAW, keyboard, or sequencer using real Sega Genesis hardware.

## Platform Requirements

**Teensy 4.x** works as a standalone USB MIDI instrument. Connect it to your computer and it appears as "Genesis Engine" in your MIDI device list. You can load custom patches using the included `genesis_patch.py` script, though the companion app below offers more features.

**Arduino Uno/Mega** requires the [GenesisEngineSynthApp](https://github.com/aarontodd82/GenesisEngineSynthApp) companion application. The app bridges your computer's MIDI to the Arduino over serial. Download releases for Windows, Mac, and Linux from the GitHub page. On Windows, you'll also need [LoopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) to create a virtual MIDI port for your DAW.

## Setup

### Teensy

1. In Arduino IDE: **Tools → USB Type → Serial + MIDI**
2. Upload the sketch
3. Connect via USB - your computer sees "Genesis Engine"

### Arduino

1. Upload the sketch
2. Run GenesisEngineSynthApp
3. Select your Arduino's COM port and connect
4. On Windows: Create a LoopMIDI port, select it in the app, then route your DAW to that port

## Loading Patches

The synth starts with 8 built-in FM patches. To load custom patches:

**Using GenesisEngineSynthApp** (recommended): The app provides a patch browser, bank management, and real-time parameter editing.

**Using genesis_patch.py** (Teensy only):
```bash
pip install python-rtmidi
python genesis_patch.py load piano.tfi 1      # Load to channel 1
python genesis_patch.py bank sonic2.gyb       # Load a GYB bank
```

Supported formats: TFI, DMP, GYB. Find patches at [VGMRips](https://vgmrips.net) or create your own with [Furnace Tracker](https://github.com/tildearrow/furnace).

## Quick Reference

### MIDI Channels

| Channel | Hardware |
|---------|----------|
| 1-6 | YM2612 FM |
| 7-9 | SN76489 Tone |
| 10 | SN76489 Noise |

### Synth Modes

- **Multi-timbral** (default): Each FM channel plays independently
- **Poly**: Channel 1 controls all 6 FM voices polyphonically

Switch with CC 126 (multi) or CC 127 (poly).

### Key MIDI Controls

| Message | Effect |
|---------|--------|
| Program Change | Select patch slot |
| Pitch Bend | ±2 semitones (FM) |
| CC 1 | Vibrato depth (FM) |
| CC 7 | Volume |
| CC 10 | Pan (FM) |
| CC 64 | Sustain pedal (FM) |

### Real-Time Parameter CCs

| CC | Parameter |
|----|-----------|
| 14 | Algorithm (0-7) |
| 15 | Feedback (0-7) |
| 16-19 | Operator 1-4 Total Level |
