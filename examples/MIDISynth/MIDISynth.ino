/**
 * MIDISynth - MIDI Synthesizer for GenesisEngine
 *
 * Turns the GenesisEngine hardware into a USB MIDI synthesizer.
 *
 * MIDI Channel Mapping:
 *   Ch 1-6:  YM2612 FM channels 1-6
 *   Ch 7-9:  SN76489 PSG tone channels 1-3
 *   Ch 10:   SN76489 PSG noise channel
 *
 * Requires: Teensy 4.x with USB Type set to "Serial + MIDI"
 *           (Tools -> USB Type -> Serial + MIDI)
 */

#include <GenesisBoard.h>
#include "FMPatch.h"
#include "PSGEnvelope.h"
#include "FrequencyTables.h"
#include "DefaultPatches.h"

// =============================================================================
// Pin Configuration (match your hardware)
// =============================================================================

#define PIN_WR_P  2   // PSG write strobe
#define PIN_WR_Y  3   // YM2612 write strobe
#define PIN_IC_Y  4   // YM2612 reset
#define PIN_A0_Y  5   // YM2612 A0
#define PIN_A1_Y  6   // YM2612 A1
#define PIN_SCK   13  // Shift register clock (hardware SPI)
#define PIN_SDI   11  // Shift register data (hardware SPI MOSI)

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);

// =============================================================================
// Patch Storage
// =============================================================================

// RAM storage for user-loaded patches
FMPatch fmPatches[16];           // 16 FM patch slots
PSGEnvelope psgEnvelopes[8];     // 8 PSG envelope slots

// Current patch assignment per channel
uint8_t fmChannelPatch[6] = {0, 1, 2, 3, 4, 5};
uint8_t psgChannelEnv[4] = {0, 1, 2, 3};  // Each PSG channel gets different envelope

// =============================================================================
// PSG Envelope State (software envelopes at 60Hz)
// =============================================================================

struct PSGChannelState {
    bool active;
    bool noteOn;
    uint8_t envPos;
    uint16_t baseTone;
    uint8_t baseNote;
};

PSGChannelState psgState[4] = {0};

// =============================================================================
// Synth Mode
// =============================================================================

enum SynthMode {
    MODE_MULTI,      // 6 independent FM channels (Ch 1-6), each with own patch
    MODE_POLY6       // 6-voice polyphonic on Ch 1, all same patch
};

SynthMode synthMode = MODE_MULTI;
uint8_t polyPatchSlot = 0;  // Patch slot used in poly mode

// =============================================================================
// FM Channel/Voice State
// =============================================================================

struct FMChannelState {
    bool noteOn;
    uint8_t currentNote;
    uint8_t velocity;
    uint32_t timestamp;  // For voice stealing (when note started)
};

FMChannelState fmState[6] = {0};

// Pitch bend per FM channel (-8192 to +8191)
// In poly mode, all voices share the pitch bend from channel 0
int16_t fmPitchBend[6] = {0, 0, 0, 0, 0, 0};

// =============================================================================
// Timing
// =============================================================================

uint32_t lastEnvTick = 0;
const uint32_t ENV_TICK_US = 16667;  // 60Hz = 16667 microseconds

// =============================================================================
// Forward Declarations
// =============================================================================

void setFMFrequencyWithBend(uint8_t ch, uint8_t note, int16_t bend);
void fmNoteOn(uint8_t ch, uint8_t note, uint8_t velocity);
void fmNoteOff(uint8_t ch, uint8_t note);
void fmKeyOff(uint8_t ch);
void writeFMPatch(uint8_t ch, const FMPatch& patch);

// =============================================================================
// Voice Allocator (for Poly Mode)
// =============================================================================

/**
 * Find a voice to use for a new note.
 * Strategy:
 *   1. Reuse voice if same note is retriggered
 *   2. Use first free voice
 *   3. Steal oldest voice (voice stealing)
 */
int8_t allocateVoice(uint8_t note) {
    // 1. Check if this note is already playing (retrigger - reuse same voice)
    for (int i = 0; i < 6; i++) {
        if (fmState[i].noteOn && fmState[i].currentNote == note) {
            return i;
        }
    }

    // 2. Find first free voice
    for (int i = 0; i < 6; i++) {
        if (!fmState[i].noteOn) {
            return i;
        }
    }

    // 3. All voices busy - steal oldest note
    uint32_t oldest = UINT32_MAX;
    int8_t oldestIdx = 0;
    for (int i = 0; i < 6; i++) {
        if (fmState[i].timestamp < oldest) {
            oldest = fmState[i].timestamp;
            oldestIdx = i;
        }
    }

    // Release the stolen voice before reusing
    fmKeyOff(oldestIdx);

    return oldestIdx;
}

/**
 * Find the voice playing a specific note (for note-off).
 * If multiple voices play same note, returns the oldest one (FIFO release).
 * Returns -1 if note not found.
 */
int8_t findVoiceByNote(uint8_t note) {
    int8_t found = -1;
    uint32_t oldest = UINT32_MAX;

    for (int i = 0; i < 6; i++) {
        if (fmState[i].noteOn && fmState[i].currentNote == note) {
            if (fmState[i].timestamp < oldest) {
                oldest = fmState[i].timestamp;
                found = i;
            }
        }
    }
    return found;
}

/**
 * Switch to poly mode - load same patch on all 6 FM channels
 */
void enablePolyMode(uint8_t patchSlot) {
    synthMode = MODE_POLY6;
    polyPatchSlot = patchSlot;

    // Load the same patch on all 6 channels
    for (uint8_t ch = 0; ch < 6; ch++) {
        fmChannelPatch[ch] = patchSlot;  // All channels use same patch
        writeFMPatch(ch, fmPatches[patchSlot]);
        fmState[ch].noteOn = false;
    }

    Serial.print("Poly mode enabled, patch slot ");
    Serial.println(patchSlot);
}

/**
 * Switch to multi-timbral mode - restore individual patches per channel
 */
void enableMultiMode() {
    synthMode = MODE_MULTI;

    // Restore individual patches per channel
    for (uint8_t ch = 0; ch < 6; ch++) {
        writeFMPatch(ch, fmPatches[fmChannelPatch[ch]]);
        fmState[ch].noteOn = false;
    }

    Serial.println("Multi mode enabled");
}

// =============================================================================
// Setup
// =============================================================================

void setup() {
    Serial.begin(115200);

    board.begin();
    board.reset();

    // Load default patches from PROGMEM
    loadDefaultPatches();

    // Initialize all FM channels with their assigned patches
    for (uint8_t ch = 0; ch < 6; ch++) {
        writeFMPatch(ch, fmPatches[fmChannelPatch[ch]]);
        // Set stereo output (both L+R enabled)
        setFMPanning(ch, 0xC0);  // Both speakers
    }

    // Silence PSG
    board.silencePSG();

    Serial.println("MIDISynth ready");
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
    // Process USB MIDI from computer
    while (usbMIDI.read()) {
        handleMIDI(
            usbMIDI.getType(),
            usbMIDI.getChannel(),
            usbMIDI.getData1(),
            usbMIDI.getData2(),
            usbMIDI.getSysExArray(),
            usbMIDI.getSysExArrayLength()
        );
    }

    // Update PSG envelopes at 60Hz
    uint32_t now = micros();
    if (now - lastEnvTick >= ENV_TICK_US) {
        lastEnvTick += ENV_TICK_US;
        updatePSGEnvelopes();
    }
}

// =============================================================================
// MIDI Handler
// =============================================================================

void handleMIDI(uint8_t type, uint8_t channel, uint8_t d1, uint8_t d2,
                const uint8_t* sysex, uint16_t sysexLen) {

    // MIDI channels are 1-indexed in the library
    uint8_t ch = channel - 1;

    switch (type) {
        case usbMIDI.NoteOn:
            if (d2 > 0) {
                noteOn(ch, d1, d2);
            } else {
                noteOff(ch, d1);
            }
            break;

        case usbMIDI.NoteOff:
            noteOff(ch, d1);
            break;

        case usbMIDI.ControlChange:
            handleCC(ch, d1, d2);
            break;

        case usbMIDI.ProgramChange:
            handleProgramChange(ch, d1);
            break;

        case usbMIDI.PitchBend:
            handlePitchBend(ch, (d2 << 7) | d1);
            break;

        case usbMIDI.SystemExclusive:
            if (sysex && sysexLen > 0) {
                handleSysEx(sysex, sysexLen);
            }
            break;
    }
}

// =============================================================================
// Note On/Off
// =============================================================================

void noteOn(uint8_t midiCh, uint8_t note, uint8_t velocity) {
    if (synthMode == MODE_POLY6) {
        // Poly mode: MIDI Ch 1 controls all 6 FM voices
        if (midiCh == 0) {
            int8_t voice = allocateVoice(note);
            if (voice >= 0) {
                fmNoteOn(voice, note, velocity);
            }
        } else if (midiCh >= 6 && midiCh < 9) {
            // PSG still works on Ch 7-9
            psgNoteOn(midiCh - 6, note, velocity);
        } else if (midiCh == 9) {
            psgNoiseOn(note, velocity);
        }
    } else {
        // Multi mode: Ch 1-6 = FM 1-6, Ch 7-9 = PSG, Ch 10 = Noise
        if (midiCh < 6) {
            fmNoteOn(midiCh, note, velocity);
        } else if (midiCh < 9) {
            psgNoteOn(midiCh - 6, note, velocity);
        } else if (midiCh == 9) {
            psgNoiseOn(note, velocity);
        }
    }
}

void noteOff(uint8_t midiCh, uint8_t note) {
    if (synthMode == MODE_POLY6) {
        // Poly mode: find which voice is playing this note
        if (midiCh == 0) {
            int8_t voice = findVoiceByNote(note);
            if (voice >= 0) {
                fmNoteOff(voice, note);
            }
        } else if (midiCh >= 6 && midiCh < 9) {
            psgNoteOff(midiCh - 6);
        } else if (midiCh == 9) {
            psgNoiseOff();
        }
    } else {
        // Multi mode
        if (midiCh < 6) {
            fmNoteOff(midiCh, note);
        } else if (midiCh < 9) {
            psgNoteOff(midiCh - 6);
        } else if (midiCh == 9) {
            psgNoiseOff();
        }
    }
}

// =============================================================================
// FM Functions
// =============================================================================

void fmNoteOn(uint8_t ch, uint8_t note, uint8_t velocity) {
    // Store state
    fmState[ch].noteOn = true;
    fmState[ch].currentNote = note;
    fmState[ch].velocity = velocity;
    fmState[ch].timestamp = millis();  // For voice stealing

    // Apply velocity to carrier TL (simple scaling)
    applyVelocity(ch, velocity);

    // Set frequency (apply any active pitch bend)
    // In poly mode, use channel 0's pitch bend for all voices
    int16_t bend = (synthMode == MODE_POLY6) ? fmPitchBend[0] : fmPitchBend[ch];
    if (bend != 0) {
        setFMFrequencyWithBend(ch, note, bend);
    } else {
        setFMFrequency(ch, note);
    }

    // Key on (all 4 operators)
    fmKeyOn(ch);
}

void fmNoteOff(uint8_t ch, uint8_t note) {
    // Only release if this is the note that's playing
    if (fmState[ch].noteOn && fmState[ch].currentNote == note) {
        fmState[ch].noteOn = false;
        fmKeyOff(ch);
    }
}

void fmKeyOn(uint8_t ch) {
    // Key on register is always on port 0
    // Bits 4-7: operator enable (all 4 = 0xF0)
    // Bits 0-2: channel (0-2 for port 0, 4-6 for port 1)
    uint8_t chBits = (ch >= 3) ? (ch - 3 + 4) : ch;
    board.writeYM2612(0, 0x28, 0xF0 | chBits);
}

void fmKeyOff(uint8_t ch) {
    uint8_t chBits = (ch >= 3) ? (ch - 3 + 4) : ch;
    board.writeYM2612(0, 0x28, 0x00 | chBits);
}

void setFMFrequency(uint8_t ch, uint8_t note) {
    uint8_t port = (ch >= 3) ? 1 : 0;
    uint8_t chReg = ch % 3;

    // Clamp note to valid range
    if (note > 127) note = 127;

    // Look up F-number and block from table
    uint16_t fnum = pgm_read_word(&fmFreqTable[note].fnum);
    uint8_t block = pgm_read_byte(&fmFreqTable[note].block);

    // Write frequency (high byte first for latching)
    board.writeYM2612(port, 0xA4 + chReg, (block << 3) | (fnum >> 8));
    board.writeYM2612(port, 0xA0 + chReg, fnum & 0xFF);
}

void setFMPanning(uint8_t ch, uint8_t pan) {
    uint8_t port = (ch >= 3) ? 1 : 0;
    uint8_t chReg = ch % 3;

    // Register B4-B6: L/R/AMS/PMS
    // We preserve AMS/PMS and just set L/R bits
    board.writeYM2612(port, 0xB4 + chReg, pan);
}

// Helper: get carrier mask for algorithm
// IMPORTANT: Our FMPatch stores operators in TFI order: S1, S3, S2, S4 (indices 0, 1, 2, 3)
// Carriers by algorithm (in slot terms):
//   ALG 0-3: S4 only           → index 3
//   ALG 4:   S2, S4            → indices 2, 3
//   ALG 5-6: S2, S3, S4        → indices 2, 1, 3
//   ALG 7:   S1, S2, S3, S4    → all
void getCarrierMask(uint8_t algorithm, bool* isCarrier) {
    isCarrier[0] = isCarrier[1] = isCarrier[2] = isCarrier[3] = false;

    switch (algorithm) {
        case 0: case 1: case 2: case 3:
            // S4 only
            isCarrier[3] = true;
            break;
        case 4:
            // S2 and S4
            isCarrier[2] = true;  // S2 is at index 2 in TFI order
            isCarrier[3] = true;  // S4 is at index 3
            break;
        case 5: case 6:
            // S2, S3, S4
            isCarrier[1] = true;  // S3 is at index 1 in TFI order
            isCarrier[2] = true;  // S2 is at index 2
            isCarrier[3] = true;  // S4 is at index 3
            break;
        case 7:
            // All operators are carriers
            isCarrier[0] = isCarrier[1] = isCarrier[2] = isCarrier[3] = true;
            break;
    }
}

void applyVelocity(uint8_t ch, uint8_t velocity) {
    // Scale carrier TL based on velocity
    // TL 0 = loudest, 127 = silent
    // Velocity 127 = loudest, 0 = silent

    FMPatch& patch = fmPatches[fmChannelPatch[ch]];
    bool isCarrier[4];
    getCarrierMask(patch.algorithm, isCarrier);

    // Calculate velocity attenuation (0 = none, ~40 = significant)
    uint8_t attenuation = (127 - velocity) / 3;

    uint8_t port = (ch >= 3) ? 1 : 0;
    uint8_t chReg = ch % 3;

    // Operator register offsets: S1=0, S3=8, S2=4, S4=12
    const uint8_t opOffsets[4] = {0, 8, 4, 12};

    for (int op = 0; op < 4; op++) {
        if (isCarrier[op]) {
            uint8_t tl = patch.op[op].tl;
            tl = min(127, tl + attenuation);
            board.writeYM2612(port, 0x40 + opOffsets[op] + chReg, tl);
        }
    }
}

void applyVolumeAttenuation(uint8_t ch, const FMPatch& patch, uint8_t attenuation) {
    // Apply attenuation to carrier operators
    bool isCarrier[4];
    getCarrierMask(patch.algorithm, isCarrier);

    uint8_t port = (ch >= 3) ? 1 : 0;
    uint8_t chReg = ch % 3;
    const uint8_t opOffsets[4] = {0, 8, 4, 12};

    for (int op = 0; op < 4; op++) {
        if (isCarrier[op]) {
            uint8_t tl = patch.op[op].tl;
            tl = min(127, tl + attenuation);
            board.writeYM2612(port, 0x40 + opOffsets[op] + chReg, tl);
        }
    }
}

// =============================================================================
// PSG Functions
// =============================================================================

void psgNoteOn(uint8_t ch, uint8_t note, uint8_t velocity) {
    if (ch >= 3) return;

    // Set frequency
    uint16_t tone = pgm_read_word(&psgToneTable[note]);
    psgState[ch].baseTone = tone;
    psgState[ch].baseNote = note;

    // Write tone
    board.writePSG(0x80 | (ch << 5) | (tone & 0x0F));
    board.writePSG((tone >> 4) & 0x3F);

    // Start envelope
    psgState[ch].active = true;
    psgState[ch].noteOn = true;
    psgState[ch].envPos = 0;

    // Set initial volume (envelope will take over)
    uint8_t vol = 15 - (velocity >> 3);  // 0-15, 0=loud
    board.writePSG(0x90 | (ch << 5) | vol);
}

void psgNoteOff(uint8_t ch) {
    if (ch >= 3) return;

    psgState[ch].noteOn = false;
    // Let envelope finish or silence immediately
    board.writePSG(0x90 | (ch << 5) | 0x0F);  // Silence
    psgState[ch].active = false;
}

void psgNoiseOn(uint8_t note, uint8_t velocity) {
    // Noise mode based on note
    // Low notes = periodic noise, high notes = white noise
    uint8_t mode = (note < 64) ? 0x00 : 0x04;  // Bit 2 = white noise
    uint8_t freq = note % 4;  // 0-3 frequency select

    board.writePSG(0xE0 | mode | freq);

    // Set volume
    uint8_t vol = 15 - (velocity >> 3);
    board.writePSG(0xF0 | vol);

    psgState[3].active = true;
    psgState[3].noteOn = true;
}

void psgNoiseOff() {
    board.writePSG(0xFF);  // Silence noise channel
    psgState[3].active = false;
    psgState[3].noteOn = false;
}

// =============================================================================
// PSG Envelope Processing (called at 60Hz)
// =============================================================================

void updatePSGEnvelopes() {
    for (uint8_t ch = 0; ch < 3; ch++) {
        if (!psgState[ch].active) continue;

        PSGEnvelope& env = psgEnvelopes[psgChannelEnv[ch]];

        if (psgState[ch].envPos >= env.length) {
            // Envelope finished
            if (!psgState[ch].noteOn) {
                psgState[ch].active = false;
                board.writePSG(0x90 | (ch << 5) | 0x0F);  // Silence
            }
            continue;
        }

        uint8_t data = env.data[psgState[ch].envPos];
        uint8_t volume = data & 0x0F;

        // Apply volume
        board.writePSG(0x90 | (ch << 5) | volume);

        // Advance position
        psgState[ch].envPos++;

        // Handle loop
        if (psgState[ch].envPos >= env.length && env.loopStart != 0xFF) {
            psgState[ch].envPos = env.loopStart;
        }
    }
}

// =============================================================================
// Control Change Handler
// =============================================================================

// LFO enabled state
bool lfoEnabled = false;

void handleCC(uint8_t ch, uint8_t cc, uint8_t value) {
    // Mode switching CCs work on any channel
    switch (cc) {
        case 126:  // Mono mode (MIDI standard) - we use for Multi-timbral
            enableMultiMode();
            return;

        case 127:  // Poly mode (MIDI standard)
            // In poly mode, use Program Change value as patch slot
            // Default to slot 0 if not set
            enablePolyMode(polyPatchSlot);
            return;
    }

    // In poly mode, only respond to MIDI Ch 1 for FM controls
    if (synthMode == MODE_POLY6 && ch != 0 && ch < 6) {
        return;  // Ignore FM CCs on channels 2-6 in poly mode
    }

    if (ch >= 6) return;  // Only FM channels for CCs below

    switch (cc) {
        case 1:  // Mod wheel - LFO depth (vibrato)
            {
                // Enable LFO if mod wheel > 0
                if (value > 0 && !lfoEnabled) {
                    // Enable LFO at medium speed (freq index 4 ≈ 5.9 Hz)
                    board.writeYM2612(0, 0x22, 0x08 | 4);
                    lfoEnabled = true;
                } else if (value == 0 && lfoEnabled) {
                    // Disable LFO
                    board.writeYM2612(0, 0x22, 0x00);
                    lfoEnabled = false;
                }

                // Set PMS (Phase Modulation Sensitivity) based on value
                uint8_t pms = value >> 4;  // 0-7 from 0-127

                if (synthMode == MODE_POLY6) {
                    // Apply to all 6 channels in poly mode
                    for (uint8_t i = 0; i < 6; i++) {
                        uint8_t port = (i >= 3) ? 1 : 0;
                        uint8_t chReg = i % 3;
                        board.writeYM2612(port, 0xB4 + chReg, 0xC0 | pms);
                    }
                } else {
                    uint8_t port = (ch >= 3) ? 1 : 0;
                    uint8_t chReg = ch % 3;
                    board.writeYM2612(port, 0xB4 + chReg, 0xC0 | pms);
                }
            }
            break;

        case 7:  // Volume - scale carrier TLs
            {
                uint8_t attenuation = (127 - value);

                if (synthMode == MODE_POLY6) {
                    // Apply to all 6 channels in poly mode
                    FMPatch& patch = fmPatches[polyPatchSlot];
                    for (uint8_t i = 0; i < 6; i++) {
                        applyVolumeAttenuation(i, patch, attenuation);
                    }
                } else {
                    FMPatch& patch = fmPatches[fmChannelPatch[ch]];
                    applyVolumeAttenuation(ch, patch, attenuation);
                }
            }
            break;

        case 10: // Pan
            {
                uint8_t pan;
                if (value < 32) pan = 0x80;       // Left only
                else if (value > 96) pan = 0x40; // Right only
                else pan = 0xC0;                  // Center (both)
                setFMPanning(ch, pan);
            }
            break;

        case 14: // Algorithm (GenMDM-style)
            if (value < 8) {
                fmPatches[fmChannelPatch[ch]].algorithm = value;
                writeFMPatch(ch, fmPatches[fmChannelPatch[ch]]);
            }
            break;

        case 15: // Feedback
            if (value < 8) {
                fmPatches[fmChannelPatch[ch]].feedback = value;
                writeFMPatch(ch, fmPatches[fmChannelPatch[ch]]);
            }
            break;

        // TL for operators 1-4 (CC 16-19)
        case 16: case 17: case 18: case 19:
            {
                uint8_t op = cc - 16;
                fmPatches[fmChannelPatch[ch]].op[op].tl = value;
                writeOperatorTL(ch, op, value);
            }
            break;
    }
}

void writeOperatorTL(uint8_t ch, uint8_t op, uint8_t tl) {
    uint8_t port = (ch >= 3) ? 1 : 0;
    uint8_t chReg = ch % 3;
    const uint8_t opOffsets[4] = {0, 8, 4, 12};
    board.writeYM2612(port, 0x40 + opOffsets[op] + chReg, tl);
}

// =============================================================================
// Program Change Handler
// =============================================================================

void handleProgramChange(uint8_t ch, uint8_t program) {
    if (synthMode == MODE_POLY6) {
        // In poly mode, Ch 1 Program Change sets patch for all 6 voices
        if (ch == 0 && program < 16) {
            polyPatchSlot = program;
            for (uint8_t i = 0; i < 6; i++) {
                writeFMPatch(i, fmPatches[program]);
            }
            Serial.print("Poly patch changed to slot ");
            Serial.println(program);
        } else if (ch >= 6 && ch < 10) {
            // PSG still works
            uint8_t psgCh = ch - 6;
            if (program < 8) {
                psgChannelEnv[psgCh] = program;
            }
        }
    } else {
        // Multi mode: each channel has its own patch
        if (ch < 6) {
            if (program < 16) {
                fmChannelPatch[ch] = program;
                writeFMPatch(ch, fmPatches[program]);
            }
        } else if (ch < 10) {
            uint8_t psgCh = ch - 6;
            if (program < 8) {
                psgChannelEnv[psgCh] = program;
            }
        }
    }
}

// =============================================================================
// Pitch Bend Handler
// =============================================================================

void handlePitchBend(uint8_t ch, uint16_t bend) {
    // bend: 0-16383, center = 8192
    // Convert to signed: -8192 to +8191
    int16_t bendOffset = (int16_t)bend - 8192;

    if (synthMode == MODE_POLY6) {
        // In poly mode, Ch 1 pitch bend affects all 6 voices
        if (ch != 0) return;

        fmPitchBend[0] = bendOffset;

        // Update all active voices
        for (uint8_t i = 0; i < 6; i++) {
            if (fmState[i].noteOn) {
                setFMFrequencyWithBend(i, fmState[i].currentNote, bendOffset);
            }
        }
    } else {
        // Multi mode: each channel has its own pitch bend
        if (ch >= 6) return;

        fmPitchBend[ch] = bendOffset;

        if (fmState[ch].noteOn) {
            setFMFrequencyWithBend(ch, fmState[ch].currentNote, bendOffset);
        }
    }
}

void setFMFrequencyWithBend(uint8_t ch, uint8_t note, int16_t bend) {
    uint8_t port = (ch >= 3) ? 1 : 0;
    uint8_t chReg = ch % 3;

    if (note > 127) note = 127;

    // Get base frequency
    uint16_t fnum = pgm_read_word(&fmFreqTable[note].fnum);
    uint8_t block = pgm_read_byte(&fmFreqTable[note].block);

    // Apply pitch bend (±2 semitones = ±8192)
    // Each semitone is approximately fnum * 0.059 (2^(1/12) - 1)
    // For ±2 semitones: bend range maps to ±12% of fnum
    if (bend != 0) {
        // Scale: full bend = ±2 semitones ≈ ±12% frequency change
        int32_t bendAmount = ((int32_t)fnum * bend) / 68000;  // ~12% at full bend
        fnum = constrain(fnum + bendAmount, 0, 2047);
    }

    // Write frequency
    board.writeYM2612(port, 0xA4 + chReg, (block << 3) | (fnum >> 8));
    board.writeYM2612(port, 0xA0 + chReg, fnum & 0xFF);
}

// =============================================================================
// SysEx Handler
// =============================================================================

// SysEx format: F0 7D 00 <cmd> <data...> F7
// Commands:
//   0x01 = Load FM patch to channel: <channel> <42 bytes>
//   0x02 = Load PSG envelope: <channel> <length> <loopStart> <data...>
//   0x03 = Store FM patch to slot: <slot> <42 bytes>
//   0x04 = Recall FM patch to channel: <channel> <slot>

void handleSysEx(const uint8_t* data, uint16_t len) {
    // Minimum: F0 7D 00 cmd F7 = 5 bytes
    if (len < 5) return;

    // Check manufacturer ID (0x7D = educational/development)
    if (data[1] != 0x7D) return;

    uint8_t cmd = data[3];

    switch (cmd) {
        case 0x01:  // Load FM patch to channel
            if (len >= 5 + 1 + 42) {  // cmd + channel + patch
                uint8_t ch = data[4];
                if (ch < 6) {
                    parseTFIPatch(&data[5], fmPatches[fmChannelPatch[ch]]);
                    if (synthMode == MODE_POLY6) {
                        // In poly mode, update all 6 channels with the shared patch
                        for (uint8_t i = 0; i < 6; i++) {
                            writeFMPatch(i, fmPatches[fmChannelPatch[ch]]);
                        }
                        Serial.println("Loaded patch to all FM channels (poly mode)");
                    } else {
                        writeFMPatch(ch, fmPatches[fmChannelPatch[ch]]);
                        Serial.print("Loaded patch to FM channel ");
                        Serial.println(ch);
                    }
                }
            }
            break;

        case 0x02:  // Load PSG envelope
            if (len >= 5 + 3) {  // cmd + channel + length + loopStart
                uint8_t ch = data[4];
                uint8_t envLen = data[5];
                uint8_t loopStart = data[6];
                if (ch < 4 && envLen <= 64 && len >= 5 + 3 + envLen) {
                    PSGEnvelope& env = psgEnvelopes[psgChannelEnv[ch]];
                    env.length = envLen;
                    env.loopStart = loopStart;
                    memcpy(env.data, &data[7], envLen);
                    Serial.print("Loaded envelope to PSG channel ");
                    Serial.println(ch);
                }
            }
            break;

        case 0x03:  // Store FM patch to slot
            if (len >= 5 + 1 + 42) {
                uint8_t slot = data[4];
                if (slot < 16) {
                    parseTFIPatch(&data[5], fmPatches[slot]);
                    Serial.print("Stored patch to slot ");
                    Serial.println(slot);
                }
            }
            break;

        case 0x04:  // Recall FM patch to channel
            if (len >= 5 + 2) {
                uint8_t ch = data[4];
                uint8_t slot = data[5];
                if (ch < 6 && slot < 16) {
                    fmChannelPatch[ch] = slot;
                    writeFMPatch(ch, fmPatches[slot]);
                    Serial.print("Recalled slot ");
                    Serial.print(slot);
                    Serial.print(" to channel ");
                    Serial.println(ch);
                }
            }
            break;
    }
}

// =============================================================================
// Patch Loading
// =============================================================================

void parseTFIPatch(const uint8_t* data, FMPatch& patch) {
    patch.algorithm = data[0];
    patch.feedback = data[1];

    for (int op = 0; op < 4; op++) {
        const uint8_t* opData = &data[2 + op * 10];
        patch.op[op].mul = opData[0];
        patch.op[op].dt = opData[1];
        patch.op[op].tl = opData[2];
        patch.op[op].rs = opData[3];
        patch.op[op].ar = opData[4];
        patch.op[op].dr = opData[5];
        patch.op[op].sr = opData[6];
        patch.op[op].rr = opData[7];
        patch.op[op].sl = opData[8];
        patch.op[op].ssg = opData[9];
    }
}

void writeFMPatch(uint8_t ch, const FMPatch& patch) {
    uint8_t port = (ch >= 3) ? 1 : 0;
    uint8_t chReg = ch % 3;

    // Write algorithm and feedback
    board.writeYM2612(port, 0xB0 + chReg, (patch.feedback << 3) | patch.algorithm);

    // Operator register offsets: S1=0, S3=8, S2=4, S4=12
    // TFI stores in order: S1, S3, S2, S4 (indices 0, 1, 2, 3)
    const uint8_t opOffsets[4] = {0, 8, 4, 12};

    for (int i = 0; i < 4; i++) {
        uint8_t regOff = opOffsets[i];
        const FMOperator& op = patch.op[i];

        // Detune needs adjustment: TFI stores 0-7, chip expects 0-7 but centered at 3
        uint8_t dt = op.dt;

        board.writeYM2612(port, 0x30 + regOff + chReg, (dt << 4) | op.mul);
        board.writeYM2612(port, 0x40 + regOff + chReg, op.tl);
        board.writeYM2612(port, 0x50 + regOff + chReg, (op.rs << 6) | op.ar);
        board.writeYM2612(port, 0x60 + regOff + chReg, op.dr);
        board.writeYM2612(port, 0x70 + regOff + chReg, op.sr);
        board.writeYM2612(port, 0x80 + regOff + chReg, (op.sl << 4) | op.rr);
        board.writeYM2612(port, 0x90 + regOff + chReg, op.ssg);
    }
}

void loadDefaultPatches() {
    // Load default FM patches from PROGMEM
    for (int i = 0; i < DEFAULT_FM_PATCH_COUNT && i < 16; i++) {
        memcpy_P(&fmPatches[i], &defaultFMPatches[i], sizeof(FMPatch));
    }

    // Load default PSG envelopes from PROGMEM
    for (int i = 0; i < DEFAULT_PSG_ENV_COUNT && i < 8; i++) {
        memcpy_P(&psgEnvelopes[i], &defaultPSGEnvelopes[i], sizeof(PSGEnvelope));
    }
}
