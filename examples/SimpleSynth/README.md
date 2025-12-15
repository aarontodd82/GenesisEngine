# SimpleSynth

Direct chip control demo for the GenesisEngine board. Play notes on the YM2612 FM chip and SN76489 PSG using simple serial commands.

This example demonstrates the **synthesis utilities** in the GenesisEngine library, showing how to control the chips without VGM playback.

## Features

- Load FM patches from the built-in library
- Play notes by MIDI number
- PSG tone generation
- Simple serial command interface

## Quick Start

1. Upload the sketch to your board
2. Open Serial Monitor at 115200 baud
3. Try these commands:
   - `n60` — Play middle C on FM
   - `p3` — Switch to Lead Synth patch
   - `n72` — Play note 72 (C5)
   - `s` — Stop note
   - `t60` — Play middle C on PSG
   - `q` — Silence all
   - `?` — Show help

## Serial Commands

| Command | Description |
|---------|-------------|
| `n<note>` | Play FM note (0-127, 60 = middle C) |
| `s` | Stop FM note |
| `p<num>` | Change FM patch (0-7) |
| `t<note>` | Play PSG tone on channel 0 |
| `q` | Silence all |
| `?` | Show help |

## Built-in Patches

| Slot | Sound |
|------|-------|
| 0 | Bright EP (Electric Piano) |
| 1 | Synth Bass |
| 2 | Brass |
| 3 | Lead Synth |
| 4 | Organ |
| 5 | Strings |
| 6 | Pluck/Guitar |
| 7 | Bell/Chime |

## Using the Synthesis Utilities

This example shows the basics of direct chip control:

```cpp
#include <GenesisBoard.h>
#include <synth/FMPatch.h>
#include <synth/FMFrequency.h>
#include <synth/PSGFrequency.h>
#include <synth/DefaultPatches.h>

// Load a patch
FMPatch patch;
memcpy_P(&patch, &defaultFMPatches[0], sizeof(FMPatch));
FMPatchUtils::loadToChannel(board, 0, patch);

// Play a note
FMFrequency::writeToChannel(board, 0, 60);  // Set frequency
FMFrequency::keyOn(board, 0);               // Start note

// Stop the note
FMFrequency::keyOff(board, 0);

// PSG tone
PSGFrequency::playNote(board, 0, 60, 2);    // Channel 0, note 60, volume 2
```

## Building On This

For a full synthesizer with MIDI support, see the **MIDISynth** example. SimpleSynth is intentionally minimal to demonstrate the core synthesis APIs.

The synthesis utilities are also useful for:
- Sound effects in games
- Custom instruments
- Algorithmic composition
- Learning FM synthesis

## Pin Configuration

Adjust the pin definitions in the sketch to match your wiring to the Genesis Engine board.
