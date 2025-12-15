#ifndef GENESIS_PSG_FREQUENCY_H
#define GENESIS_PSG_FREQUENCY_H

#include <stdint.h>
#include "../config/platform_detect.h"

// Forward declaration
class GenesisBoard;

/**
 * PSG Frequency Utilities for SN76489
 *
 * The SN76489 uses a 10-bit counter for tone generation:
 * - Tone value N produces frequency: clock / (32 * N)
 * - Clock = 3579545 Hz (NTSC)
 * - Valid range: 1-1023 (0 stops the oscillator)
 *
 * The chip has 3 tone channels and 1 noise channel.
 */
namespace PSGFrequency {
    /**
     * Convert MIDI note (0-127) to SN76489 tone value
     *
     * Returns 10-bit value suitable for tone channels (0-2).
     * Very low notes are clamped to 1023, very high to minimum audible.
     *
     * @param midiNote MIDI note number (0-127)
     * @return 10-bit tone value (1-1023)
     */
    uint16_t midiToTone(uint8_t midiNote);

    /**
     * Write tone to PSG channel
     *
     * Sets the frequency for a tone channel. Does NOT affect volume.
     * You must set volume separately to hear the tone.
     *
     * @param board GenesisBoard instance
     * @param channel PSG tone channel (0-2 only, not noise)
     * @param midiNote MIDI note number (0-127)
     */
    void writeToChannel(GenesisBoard& board, uint8_t channel, uint8_t midiNote);

    /**
     * Write raw tone value to PSG channel
     *
     * Low-level function for direct tone control.
     *
     * @param board GenesisBoard instance
     * @param channel PSG tone channel (0-2)
     * @param tone 10-bit tone value (1-1023)
     */
    void writeToneValue(GenesisBoard& board, uint8_t channel, uint16_t tone);

    /**
     * Set PSG channel volume
     *
     * @param board GenesisBoard instance
     * @param channel PSG channel (0-3, where 3 is noise)
     * @param volume Attenuation level (0=loudest, 15=silent)
     */
    void setVolume(GenesisBoard& board, uint8_t channel, uint8_t volume);

    /**
     * Configure and enable noise channel
     *
     * @param board GenesisBoard instance
     * @param white True for white noise, false for periodic noise
     * @param shift Frequency source:
     *              0 = N/512 (high frequency)
     *              1 = N/1024 (medium frequency)
     *              2 = N/2048 (low frequency)
     *              3 = Use tone channel 2's frequency
     */
    void setNoise(GenesisBoard& board, bool white, uint8_t shift);

    /**
     * Convenience: play a note on PSG channel with volume
     *
     * Sets both tone and volume in one call.
     *
     * @param board GenesisBoard instance
     * @param channel PSG tone channel (0-2)
     * @param midiNote MIDI note number (0-127)
     * @param volume Attenuation (0=loudest, 15=silent)
     */
    void playNote(GenesisBoard& board, uint8_t channel, uint8_t midiNote, uint8_t volume);

    /**
     * Silence a PSG channel
     *
     * Sets volume to maximum attenuation (15 = silent).
     *
     * @param board GenesisBoard instance
     * @param channel PSG channel (0-3)
     */
    void silence(GenesisBoard& board, uint8_t channel);
}

/**
 * Pre-calculated tone table for MIDI notes 0-127
 *
 * Formula: N = 3579545 / (32 * freq)
 * Based on NTSC PSG clock (3579545 Hz).
 *
 * Values > 1023 are clamped (very low notes).
 * Very high notes have small values (approaching minimum).
 *
 * Access with:
 *   uint16_t tone = GENESIS_READ_WORD(&psgToneTable[note]);
 *
 * Or use PSGFrequency::midiToTone() which handles this automatically.
 */
extern const uint16_t psgToneTable[128] GENESIS_PROGMEM;

#endif // GENESIS_PSG_FREQUENCY_H
