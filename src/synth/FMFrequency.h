#ifndef GENESIS_FM_FREQUENCY_H
#define GENESIS_FM_FREQUENCY_H

#include <stdint.h>
#include "../config/platform_detect.h"

// Forward declaration
class GenesisBoard;

/**
 * YM2612 Frequency Table Entry
 *
 * The YM2612 uses F-numbers and blocks to set pitch:
 * - F-number: 11-bit value (0-2047) determining frequency within an octave
 * - Block: 3-bit octave selector (0-7)
 *
 * Formula: freq = (F-number * clock) / (144 * 2^(21-block))
 * where clock = 7670453 Hz (NTSC)
 */
struct FMFreqEntry {
    uint16_t fnum;   // F-number (0-2047)
    uint8_t  block;  // Block/octave (0-7)
};

/**
 * FM Frequency Utilities for YM2612
 *
 * Functions for converting MIDI notes to YM2612 frequency registers
 * and applying pitch bend.
 */
namespace FMFrequency {
    /**
     * Convert MIDI note (0-127) to YM2612 F-number and block
     *
     * Uses A4=440Hz standard tuning with NTSC clock (7670453 Hz).
     * Notes outside the chip's range are clamped.
     *
     * @param midiNote MIDI note number (0-127, 60=middle C)
     * @param fnum Output: F-number (0-2047)
     * @param block Output: Block/octave (0-7)
     */
    void midiToFM(uint8_t midiNote, uint16_t* fnum, uint8_t* block);

    /**
     * Apply pitch bend to an F-number
     *
     * Standard MIDI pitch bend is 14-bit (0-16383) with 8192 as center.
     * This function expects the centered value (-8192 to +8191).
     *
     * @param fnum Base F-number from midiToFM()
     * @param bend Pitch bend offset (-8192 to +8191, 0=no bend)
     * @param bendRange Bend range in semitones (default 2, standard MIDI)
     * @return Modified F-number (clamped to 0-2047)
     */
    uint16_t applyBend(uint16_t fnum, int16_t bend, uint8_t bendRange = 2);

    /**
     * Write frequency to FM channel
     *
     * Looks up the MIDI note in the frequency table and writes to the
     * YM2612's frequency registers. Does NOT trigger key on.
     *
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     * @param midiNote MIDI note number (0-127)
     */
    void writeToChannel(GenesisBoard& board, uint8_t channel, uint8_t midiNote);

    /**
     * Write frequency with pitch bend to FM channel
     *
     * Same as writeToChannel but applies pitch bend to the frequency.
     * Does NOT trigger key on.
     *
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     * @param midiNote MIDI note number (0-127)
     * @param bend Pitch bend offset (-8192 to +8191)
     */
    void writeToChannelWithBend(GenesisBoard& board, uint8_t channel,
                                 uint8_t midiNote, int16_t bend);

    /**
     * Key on (start note) for FM channel
     *
     * Triggers the attack phase of the envelope for specified operators.
     * Call this after setting frequency to start the note.
     *
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     * @param operatorMask Which operators to key on (default 0xF0 = all four)
     *                     Bits 4-7 enable operators 1-4
     */
    void keyOn(GenesisBoard& board, uint8_t channel, uint8_t operatorMask = 0xF0);

    /**
     * Key off (release note) for FM channel
     *
     * Triggers the release phase of the envelope.
     * The note will decay according to the release rate (RR) setting.
     *
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     */
    void keyOff(GenesisBoard& board, uint8_t channel);
}

/**
 * Pre-calculated frequency table for MIDI notes 0-127
 *
 * Based on A4=440Hz standard tuning, NTSC clock (7670453 Hz).
 * Stored in PROGMEM for AVR compatibility.
 *
 * Access with:
 *   uint16_t fnum = GENESIS_READ_WORD(&fmFreqTable[note].fnum);
 *   uint8_t block = GENESIS_READ_BYTE(&fmFreqTable[note].block);
 *
 * Or use FMFrequency::midiToFM() which handles this automatically.
 */
extern const FMFreqEntry fmFreqTable[128] GENESIS_PROGMEM;

#endif // GENESIS_FM_FREQUENCY_H
