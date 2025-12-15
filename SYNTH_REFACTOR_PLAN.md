# Synthesis Utilities Extraction Plan

This document tracks the refactoring of synthesis utilities from the MIDISynth example into the core library, enabling users to easily control YM2612 and SN76489 chips without VGM playback.

## Goals

1. Extract reusable synthesis code from `examples/MIDISynth/` into `src/synth/`
2. Create a simple example (`SimpleSynth`) demonstrating direct chip control
3. Refactor MIDISynth to use the new library utilities (eat our own dog food)
4. Maintain backward compatibility - existing code should still compile

## Design Decisions

- **Namespaces over classes**: Use `namespace FMFrequency` for stateless utility functions (C++ idiomatic)
- **Include paths**: `#include <synth/FMPatch.h>` style (standard Arduino library convention)
- **Separation of concerns**: Frequency writing and key on/off are separate operations (explicit > magic)
- **Velocity handling**: Stays in MIDISynth (application-specific, different synths want different curves)
- **PROGMEM portability**: Use platform_detect.h macros for cross-platform flash storage

---

## New File Structure

```
src/
├── synth/
│   ├── FMOperator.h         [NEW] Operator parameter structure
│   ├── FMPatch.h            [NEW] Patch structure + load helpers
│   ├── FMFrequency.h        [NEW] MIDI note to F-number conversion
│   ├── FMFrequency.cpp      [NEW] Frequency table (PROGMEM)
│   ├── PSGFrequency.h       [NEW] MIDI note to PSG tone conversion
│   ├── PSGFrequency.cpp     [NEW] Tone table (PROGMEM)
│   ├── PSGEnvelope.h        [NEW] Software envelope structure
│   └── DefaultPatches.h     [NEW] Built-in FM patches and PSG envelopes

examples/
├── SimpleSynth/
│   └── SimpleSynth.ino      [NEW] Minimal synthesis demo
├── MIDISynth/
│   ├── MIDISynth.ino        [MODIFY] Use library utilities
│   ├── FMPatch.h            [DELETE] Moved to library
│   ├── FrequencyTables.h    [DELETE] Moved to library
│   ├── PSGEnvelope.h        [DELETE] Moved to library
│   └── DefaultPatches.h     [DELETE] Moved to library
```

---

## Implementation Checklist

### Phase 1: Create Library Structure

- [x] **1.1** Create `src/synth/` directory
- [x] **1.2** Create `src/synth/FMOperator.h`
  - Source: `examples/MIDISynth/FMPatch.h` lines 9-20
  - Pure data structure, 10 bytes per operator
- [x] **1.3** Create `src/synth/FMPatch.h` and `src/synth/FMPatch.cpp`
  - Source: `examples/MIDISynth/FMPatch.h` lines 24-78
  - Add `FMPatch` namespace with helper functions
- [x] **1.4** Create `src/synth/FMFrequency.h` and `src/synth/FMFrequency.cpp`
  - Source: `examples/MIDISynth/FrequencyTables.h` lines 1-57
  - Source: `examples/MIDISynth/MIDISynth.ino` lines 480-494 (setFMFrequency)
  - Source: `examples/MIDISynth/MIDISynth.ino` lines 981-1003 (setFMFrequencyWithBend)
- [x] **1.5** Create `src/synth/PSGFrequency.h` and `src/synth/PSGFrequency.cpp`
  - Source: `examples/MIDISynth/FrequencyTables.h` lines 59-92
  - Add helper for writing tone to channel
- [x] **1.6** Create `src/synth/PSGEnvelope.h`
  - Source: `examples/MIDISynth/PSGEnvelope.h` entire file
  - Add `PSGEnvelopeState` runtime state structure
- [x] **1.7** Create `src/synth/DefaultPatches.h` and `src/synth/DefaultPatches.cpp`
  - Source: `examples/MIDISynth/DefaultPatches.h` entire file
  - Update includes to use library paths

### Phase 2: Create SimpleSynth Example

- [x] **2.1** Create `examples/SimpleSynth/SimpleSynth.ino`
  - Minimal example: load patch, play notes via serial commands
  - Demonstrates library usage without MIDI complexity
  - ~150 lines

### Phase 3: Refactor MIDISynth

- [x] **3.1** Update MIDISynth includes to use library
- [x] **3.2** Delete local header files (FMPatch.h, FrequencyTables.h, PSGEnvelope.h, DefaultPatches.h)
- [x] **3.3** Replace `writeFMPatch()` with `FMPatch::loadToChannel()`
- [x] **3.4** Replace `setFMFrequency()` with `FMFrequency::writeToChannel()`
- [x] **3.5** Replace `setFMFrequencyWithBend()` with `FMFrequency::writeToChannelWithBend()`
- [x] **3.6** Replace `getCarrierMask()` with `FMPatch::getCarrierMask()`
- [x] **3.7** Replace `parsePatchData()` with `FMPatch::parseFromData()`
- [x] **3.8** Verify MIDISynth still compiles and works

### Phase 4: Testing & Cleanup

- [ ] **4.1** Test compilation on AVR (Uno/Mega)
- [x] **4.2** Test compilation on Teensy 4.x
- [ ] **4.3** Test compilation on ESP32
- [ ] **4.4** Verify SimpleSynth works (hardware test needed)
- [ ] **4.5** Verify MIDISynth works (hardware test needed)
- [x] **4.6** Create SimpleSynth README
- [x] **4.7** Update main README with synthesis utilities section

### Implementation Notes

**Namespace Rename:** The original plan had `namespace FMPatch` for utility functions, but C++ doesn't allow a struct and namespace with the same name. Renamed to `FMPatchUtils` to resolve the conflict.

---

## Detailed File Specifications

### src/synth/FMOperator.h

```cpp
#ifndef GENESIS_FM_OPERATOR_H
#define GENESIS_FM_OPERATOR_H

#include <stdint.h>

/**
 * YM2612 FM Operator parameters (10 bytes, TFI-compatible order)
 *
 * Each FM channel has 4 operators that combine to create the sound.
 * The algorithm setting determines how operators connect (modulator vs carrier).
 */
struct FMOperator {
    uint8_t mul;    // Multiplier (0-15, 0=0.5x, 1=1x, 2=2x, etc.)
    uint8_t dt;     // Detune (0-7, 3=center/no detune)
    uint8_t tl;     // Total Level/volume (0-127, 0=loudest, 127=silent)
    uint8_t rs;     // Rate Scaling (0-3, higher=faster decay at high notes)
    uint8_t ar;     // Attack Rate (0-31, 31=instant attack)
    uint8_t dr;     // Decay Rate (0-31)
    uint8_t sr;     // Sustain Rate (0-31, "second decay")
    uint8_t rr;     // Release Rate (0-15)
    uint8_t sl;     // Sustain Level (0-15, 0=max volume, 15=silent)
    uint8_t ssg;    // SSG-EG mode (0=off, 1-15=various looping envelopes)
};

#endif // GENESIS_FM_OPERATOR_H
```

### src/synth/FMPatch.h

```cpp
#ifndef GENESIS_FM_PATCH_H
#define GENESIS_FM_PATCH_H

#include <stdint.h>
#include "FMOperator.h"

// Forward declaration
class GenesisBoard;

/**
 * Stereo panning modes for FM channels
 */
enum FMPanMode : uint8_t {
    FM_PAN_CENTER = 0,  // Both speakers (register value 0xC0)
    FM_PAN_LEFT   = 1,  // Left only (register value 0x80)
    FM_PAN_RIGHT  = 2   // Right only (register value 0x40)
};

/**
 * Complete FM voice/patch definition (45 bytes)
 *
 * Compatible with TFI format (42 bytes) plus extended parameters.
 * Operator order is TFI standard: S1, S3, S2, S4 (indices 0, 1, 2, 3)
 */
struct FMPatch {
    // Core parameters (TFI-compatible, 42 bytes)
    uint8_t algorithm;  // Algorithm 0-7 (operator routing)
    uint8_t feedback;   // Feedback 0-7 (operator 1 self-modulation)
    FMOperator op[4];   // Four operators in TFI order: S1, S3, S2, S4

    // Extended parameters (3 bytes)
    uint8_t pan;        // FMPanMode: 0=center, 1=left, 2=right
    uint8_t ams;        // Amplitude Modulation Sensitivity (0-3)
    uint8_t pms;        // Phase Modulation Sensitivity (0-7, vibrato depth)

    /**
     * Get the raw YM2612 L/R/AMS/PMS register value (for register 0xB4+ch)
     */
    uint8_t getLRAMSPMS() const;

    /**
     * Initialize to sensible defaults (center pan, no LFO sensitivity)
     */
    void initDefaults();
};

// Patch size constants
#define FM_PATCH_SIZE_LEGACY   42  // TFI format (no pan/ams/pms)
#define FM_PATCH_SIZE_EXTENDED 45  // Full format

/**
 * FM Patch utility functions
 */
namespace FMPatch {
    /**
     * Load a patch to an FM channel (0-5)
     * Writes all operator parameters, algorithm, feedback, and panning.
     */
    void loadToChannel(GenesisBoard& board, uint8_t channel, const struct FMPatch& patch);

    /**
     * Parse patch data from raw bytes (TFI/SysEx format)
     * @param data Raw patch bytes
     * @param patch Output patch structure
     * @param extended If true, expects 45 bytes with pan/ams/pms; otherwise 42 bytes
     */
    void parseFromData(const uint8_t* data, struct FMPatch& patch, bool extended = false);

    /**
     * Get which operators are carriers for a given algorithm
     * Carriers produce audible output; modulators only affect other operators.
     * @param algorithm Algorithm number (0-7)
     * @param isCarrier Output array of 4 bools (indices match FMPatch::op order)
     */
    void getCarrierMask(uint8_t algorithm, bool* isCarrier);

    /**
     * YM2612 operator register offsets
     * Maps FMPatch::op index to register offset: S1=+0, S3=+8, S2=+4, S4=+12
     */
    extern const uint8_t OPERATOR_OFFSETS[4];
}

#endif // GENESIS_FM_PATCH_H
```

### src/synth/FMFrequency.h

```cpp
#ifndef GENESIS_FM_FREQUENCY_H
#define GENESIS_FM_FREQUENCY_H

#include <stdint.h>
#include "config/platform_detect.h"

// Forward declaration
class GenesisBoard;

/**
 * YM2612 frequency table entry
 * F-number (11-bit) + block/octave (3-bit)
 */
struct FMFreqEntry {
    uint16_t fnum;   // F-number (0-2047)
    uint8_t  block;  // Block/octave (0-7)
};

/**
 * FM frequency/pitch utilities for YM2612
 *
 * The YM2612 uses F-numbers and blocks to set pitch:
 * - F-number: 11-bit value determining base frequency within an octave
 * - Block: 3-bit octave selector (0-7)
 *
 * Formula: freq = (F-number * clock) / (144 * 2^(21-block))
 * where clock = 7670453 Hz (NTSC) or 7600489 Hz (PAL)
 */
namespace FMFrequency {
    /**
     * Convert MIDI note (0-127) to YM2612 F-number and block
     * Uses A4=440Hz standard tuning, NTSC clock.
     */
    void midiToFM(uint8_t midiNote, uint16_t* fnum, uint8_t* block);

    /**
     * Apply pitch bend to an F-number
     * @param fnum Base F-number
     * @param bend Pitch bend value (-8192 to +8191, 0=center)
     * @param bendRange Bend range in semitones (default 2)
     * @return Modified F-number (clamped to 0-2047)
     */
    uint16_t applyBend(uint16_t fnum, int16_t bend, uint8_t bendRange = 2);

    /**
     * Write frequency to FM channel (does NOT key on)
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     * @param midiNote MIDI note number (0-127)
     */
    void writeToChannel(GenesisBoard& board, uint8_t channel, uint8_t midiNote);

    /**
     * Write frequency with pitch bend to FM channel (does NOT key on)
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     * @param midiNote MIDI note number (0-127)
     * @param bend Pitch bend (-8192 to +8191)
     */
    void writeToChannelWithBend(GenesisBoard& board, uint8_t channel,
                                 uint8_t midiNote, int16_t bend);

    /**
     * Key on an FM channel (start note)
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     * @param operatorMask Which operators to enable (default 0xF0 = all four)
     */
    void keyOn(GenesisBoard& board, uint8_t channel, uint8_t operatorMask = 0xF0);

    /**
     * Key off an FM channel (release note)
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     */
    void keyOff(GenesisBoard& board, uint8_t channel);
}

// Frequency lookup table (128 entries, stored in PROGMEM)
extern const FMFreqEntry fmFreqTable[128] PROGMEM_ATTR;

#endif // GENESIS_FM_FREQUENCY_H
```

### src/synth/PSGFrequency.h

```cpp
#ifndef GENESIS_PSG_FREQUENCY_H
#define GENESIS_PSG_FREQUENCY_H

#include <stdint.h>
#include "config/platform_detect.h"

// Forward declaration
class GenesisBoard;

/**
 * PSG frequency/tone utilities for SN76489
 *
 * The SN76489 uses a 10-bit counter for tone generation:
 * - Tone value N produces frequency: clock / (32 * N)
 * - clock = 3579545 Hz (NTSC) or 3546893 Hz (PAL)
 * - Valid range: 1-1023 (0 stops the oscillator)
 */
namespace PSGFrequency {
    /**
     * Convert MIDI note (0-127) to SN76489 tone value
     * Returns 10-bit value (clamped to 1-1023)
     */
    uint16_t midiToTone(uint8_t midiNote);

    /**
     * Write tone to PSG channel (does NOT set volume)
     * @param board GenesisBoard instance
     * @param channel PSG tone channel (0-2)
     * @param midiNote MIDI note number (0-127)
     */
    void writeToChannel(GenesisBoard& board, uint8_t channel, uint8_t midiNote);

    /**
     * Write raw tone value to PSG channel
     * @param board GenesisBoard instance
     * @param channel PSG tone channel (0-2)
     * @param tone 10-bit tone value (1-1023)
     */
    void writeToneValue(GenesisBoard& board, uint8_t channel, uint16_t tone);

    /**
     * Set PSG channel volume
     * @param board GenesisBoard instance
     * @param channel PSG channel (0-3, channel 3 is noise)
     * @param volume Volume level (0=loudest, 15=silent)
     */
    void setVolume(GenesisBoard& board, uint8_t channel, uint8_t volume);

    /**
     * Configure noise channel
     * @param board GenesisBoard instance
     * @param white True for white noise, false for periodic noise
     * @param shift Frequency shift (0-3): 0=high, 1=med, 2=low, 3=use tone ch2
     */
    void setNoise(GenesisBoard& board, bool white, uint8_t shift);
}

// Tone lookup table (128 entries, stored in PROGMEM)
extern const uint16_t psgToneTable[128] PROGMEM_ATTR;

#endif // GENESIS_PSG_FREQUENCY_H
```

### src/synth/PSGEnvelope.h

```cpp
#ifndef GENESIS_PSG_ENVELOPE_H
#define GENESIS_PSG_ENVELOPE_H

#include <stdint.h>

/**
 * PSG Software Envelope definition
 *
 * The SN76489 has no hardware envelope generator, so we implement
 * volume envelopes in software by updating at 60Hz.
 *
 * Each data byte contains:
 * - Lower nibble (bits 0-3): Volume (0=loudest, 15=silent)
 * - Upper nibble (bits 4-7): Reserved for pitch shift (future use)
 *
 * Set loopStart to 0xFF for one-shot envelopes (no loop).
 */
struct PSGEnvelope {
    uint8_t data[64];    // Envelope data (max 64 steps at 60Hz = ~1 second)
    uint8_t length;      // Actual length used (1-64)
    uint8_t loopStart;   // Loop point (0-63), or 0xFF for no loop
};

/**
 * Runtime state for tracking envelope playback on a PSG channel
 *
 * Create one instance per PSG channel that needs envelope support.
 * Call tick() at 60Hz to get the current volume level.
 */
struct PSGEnvelopeState {
    const PSGEnvelope* envelope;  // Pointer to envelope definition
    uint8_t position;             // Current position in envelope
    bool active;                  // Envelope is running
    bool gateOn;                  // Note is held (affects looping behavior)

    /**
     * Initialize state (call once)
     */
    void init();

    /**
     * Trigger envelope from the start
     * @param env Pointer to envelope definition (must remain valid)
     */
    void trigger(const PSGEnvelope* env);

    /**
     * Release the envelope (note off)
     * For looping envelopes, this allows them to finish.
     */
    void release();

    /**
     * Advance envelope by one tick (call at 60Hz)
     * @return Current volume (0-15), or 15 if envelope finished/inactive
     */
    uint8_t tick();

    /**
     * Check if envelope is still producing sound
     */
    bool isActive() const { return active; }
};

#endif // GENESIS_PSG_ENVELOPE_H
```

### src/synth/DefaultPatches.h

```cpp
#ifndef GENESIS_DEFAULT_PATCHES_H
#define GENESIS_DEFAULT_PATCHES_H

#include "FMPatch.h"
#include "PSGEnvelope.h"
#include "config/platform_detect.h"

/**
 * Default FM Patches
 *
 * Classic Genesis-style sounds for immediate use.
 * Load with: memcpy_P(&myPatch, &defaultFMPatches[n], sizeof(FMPatch));
 *
 * Patches:
 *   0: Bright EP (Electric Piano)
 *   1: Synth Bass
 *   2: Brass
 *   3: Lead Synth
 *   4: Organ
 *   5: Strings
 *   6: Pluck/Guitar
 *   7: Bell/Chime
 */
#define DEFAULT_FM_PATCH_COUNT 8
extern const FMPatch defaultFMPatches[DEFAULT_FM_PATCH_COUNT] PROGMEM_ATTR;

/**
 * Default PSG Envelopes
 *
 * Software envelopes for SN76489 channels.
 * Load with: memcpy_P(&myEnv, &defaultPSGEnvelopes[n], sizeof(PSGEnvelope));
 *
 * Envelopes:
 *   0: Short pluck (quick decay, no loop)
 *   1: Sustain (organ-like, loops)
 *   2: Slow attack pad (fades in, loops sustain)
 *   3: Tremolo (volume wobble, loops)
 */
#define DEFAULT_PSG_ENV_COUNT 4
extern const PSGEnvelope defaultPSGEnvelopes[DEFAULT_PSG_ENV_COUNT] PROGMEM_ATTR;

#endif // GENESIS_DEFAULT_PATCHES_H
```

---

## Code Migration Reference

This section maps MIDISynth code to its new library location.

### From examples/MIDISynth/FMPatch.h

| Lines | Content | Destination |
|-------|---------|-------------|
| 1-5 | Header guard, includes | `src/synth/FMOperator.h` |
| 9-20 | `struct FMOperator` | `src/synth/FMOperator.h` |
| 24-29 | `enum PanMode` | `src/synth/FMPatch.h` (renamed `FMPanMode`) |
| 44-72 | `struct FMPatch` | `src/synth/FMPatch.h` |
| 74-77 | Size constants | `src/synth/FMPatch.h` |

### From examples/MIDISynth/FrequencyTables.h

| Lines | Content | Destination |
|-------|---------|-------------|
| 1-22 | Header, FM frequency docs | `src/synth/FMFrequency.h` |
| 18-21 | `struct FMFreq` | `src/synth/FMFrequency.h` (renamed `FMFreqEntry`) |
| 25-57 | `fmFreqTable[128]` | `src/synth/FMFrequency.cpp` |
| 59-68 | PSG frequency docs | `src/synth/PSGFrequency.h` |
| 70-92 | `psgToneTable[128]` | `src/synth/PSGFrequency.cpp` |

### From examples/MIDISynth/MIDISynth.ino

| Lines | Function | Destination |
|-------|----------|-------------|
| 480-494 | `setFMFrequency()` | `FMFrequency::writeToChannel()` |
| 467-472 | `fmKeyOn()` | `FMFrequency::keyOn()` |
| 475-478 | `fmKeyOff()` | `FMFrequency::keyOff()` |
| 506-536 | `getCarrierMask()` | `FMPatch::getCarrierMask()` |
| 981-1003 | `setFMFrequencyWithBend()` | `FMFrequency::writeToChannelWithBend()` |
| 1172-1206 | `parsePatchData()` | `FMPatch::parseFromData()` |
| 1208-1237 | `writeFMPatch()` | `FMPatch::loadToChannel()` |

### From examples/MIDISynth/PSGEnvelope.h

| Lines | Content | Destination |
|-------|---------|-------------|
| 1-24 | Entire file | `src/synth/PSGEnvelope.h` (+ add `PSGEnvelopeState`) |

### From examples/MIDISynth/DefaultPatches.h

| Lines | Content | Destination |
|-------|---------|-------------|
| 1-171 | Entire file | `src/synth/DefaultPatches.h` (update includes) |

---

## Platform Compatibility Notes

### PROGMEM Handling

The library already has PROGMEM macros in `config/platform_detect.h`:

```cpp
// Already defined - USE THESE:
GENESIS_PROGMEM              // Use instead of PROGMEM
GENESIS_READ_BYTE(addr)      // Use instead of pgm_read_byte
GENESIS_READ_WORD(addr)      // Use instead of pgm_read_word
GENESIS_READ_DWORD(addr)     // Use instead of pgm_read_dword
```

All synth utilities must use these macros for cross-platform compatibility.

### Tested Platforms

- [ ] Arduino Uno (ATmega328P) - AVR, 2KB RAM, needs PROGMEM
- [ ] Arduino Mega (ATmega2560) - AVR, 8KB RAM, needs PROGMEM
- [ ] Teensy 4.0/4.1 - ARM Cortex-M7, plenty of RAM, PROGMEM optional
- [ ] ESP32 - Xtensa, PROGMEM works differently

---

## SimpleSynth Example Specification

Minimal example demonstrating synthesis utilities (~100 lines).

**Features:**
- Load FM patches from defaults
- Play notes via serial commands
- Change patches
- Basic PSG tones

**Serial Commands:**
- `n<note>` - Play MIDI note (e.g., `n60` for middle C)
- `s` - Stop current note
- `p<num>` - Load patch number (0-7)
- `t<note>` - Play PSG tone on channel 0
- `q` - Silence all

**Code Structure:**
```cpp
#include <GenesisBoard.h>
#include <synth/FMPatch.h>
#include <synth/FMFrequency.h>
#include <synth/PSGFrequency.h>
#include <synth/DefaultPatches.h>

// Setup board, load default patch
// Loop: read serial, play/stop notes
// Helper functions for note on/off
```

---

## Post-Refactor MIDISynth Structure

After refactoring, MIDISynth.ino will contain only application-specific code:

**Remains in MIDISynth (~900 lines):**
- Pin configuration and setup
- MIDI parsing (serial + USB)
- Voice allocation and stealing
- Sustain pedal handling
- Channel state management
- SysEx protocol handling
- Multi/Poly mode switching
- Velocity scaling (application-specific curves)
- LFO/modulation handling
- CC handlers

**Removed from MIDISynth (~460 lines):**
- FMPatch.h (79 lines) → library
- FrequencyTables.h (94 lines) → library
- PSGEnvelope.h (24 lines) → library
- DefaultPatches.h (171 lines) → library
- Inline functions (~100 lines) → library

---

## Verification Checklist

After implementation, verify:

1. [ ] `SimpleSynth` compiles on all platforms
2. [ ] `SimpleSynth` plays notes correctly
3. [ ] `MIDISynth` compiles on all platforms
4. [ ] `MIDISynth` responds to MIDI input correctly
5. [ ] `MIDISynth` SysEx patch loading still works
6. [ ] All other examples still compile
7. [ ] Library size increase is reasonable

---

## Notes for Future Sessions

If continuing this work in a new context window:

1. Read this file first: `SYNTH_REFACTOR_PLAN.md`
2. Check the Implementation Checklist for current progress
3. The Code Migration Reference section has exact line numbers
4. Test on at least one platform after each phase

Key files to read for context:
- `src/GenesisBoard.h` - Hardware driver API
- `src/config/platform_detect.h` - Platform macros
- `examples/MIDISynth/MIDISynth.ino` - Source of code being extracted
