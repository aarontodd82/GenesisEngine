#ifndef GENESIS_PSG_ENVELOPE_H
#define GENESIS_PSG_ENVELOPE_H

#include <stdint.h>

/**
 * PSG Software Envelope Definition
 *
 * The SN76489 has no hardware envelope generator, so we implement
 * volume envelopes in software by updating at a regular rate (typically 60Hz).
 *
 * Each data byte represents one "tick" of the envelope:
 * - Lower nibble (bits 0-3): Volume attenuation (0=loudest, 15=silent)
 * - Upper nibble (bits 4-7): Reserved (future: pitch modulation)
 *
 * Looping:
 * - Set loopStart to 0xFF for one-shot envelopes (no loop)
 * - Set loopStart to a valid index (0 to length-1) to loop from that point
 *
 * At 60Hz tick rate:
 * - 64 steps = ~1.07 seconds maximum envelope length
 * - 10 steps = ~167ms (good for quick plucks)
 * - 30 steps = 500ms (medium decay)
 */
struct PSGEnvelope {
    uint8_t data[64];    // Envelope data (max 64 steps)
    uint8_t length;      // Actual length used (1-64)
    uint8_t loopStart;   // Loop point (0-63), or 0xFF for no loop
};

/**
 * PSG Envelope Runtime State
 *
 * Tracks the playback state of an envelope on a single PSG channel.
 * Create one instance per PSG channel that needs envelope support.
 *
 * Typical usage:
 *   PSGEnvelopeState state;
 *   state.init();
 *
 *   // On note on:
 *   state.trigger(&myEnvelope);
 *
 *   // In your 60Hz update loop:
 *   uint8_t vol = state.tick();
 *   PSGFrequency::setVolume(board, channel, vol);
 *
 *   // On note off:
 *   state.release();
 */
struct PSGEnvelopeState {
    const PSGEnvelope* envelope;  // Pointer to envelope definition (must remain valid)
    uint8_t position;             // Current position in envelope (0 to length-1)
    bool active;                  // True if envelope is running
    bool gateOn;                  // True if note is held (affects loop behavior)

    /**
     * Initialize state to idle
     * Call once before using the state.
     */
    void init() {
        envelope = nullptr;
        position = 0;
        active = false;
        gateOn = false;
    }

    /**
     * Trigger envelope from the start
     *
     * Call this on note-on to start the envelope.
     *
     * @param env Pointer to envelope definition (must remain valid for duration)
     */
    void trigger(const PSGEnvelope* env) {
        envelope = env;
        position = 0;
        active = (env != nullptr && env->length > 0);
        gateOn = true;
    }

    /**
     * Release the envelope (note off)
     *
     * For looping envelopes, this allows the envelope to complete
     * rather than looping indefinitely. For one-shot envelopes,
     * this has no immediate effect (envelope continues to completion).
     */
    void release() {
        gateOn = false;
    }

    /**
     * Advance envelope by one tick
     *
     * Call this at your envelope update rate (typically 60Hz).
     * Returns the current volume attenuation level.
     *
     * @return Volume attenuation (0=loudest, 15=silent)
     */
    uint8_t tick() {
        // If no envelope or not active, return silent
        if (!active || envelope == nullptr) {
            return 15;
        }

        // Get current volume (lower nibble of data byte)
        uint8_t volume = envelope->data[position] & 0x0F;

        // Advance position
        position++;

        // Check for end of envelope
        if (position >= envelope->length) {
            // Check for loop
            if (envelope->loopStart != 0xFF && gateOn) {
                // Loop back (only while note is held)
                position = envelope->loopStart;
            } else {
                // Envelope finished
                // Stay at last position for final value
                position = envelope->length - 1;

                // If note is released, mark as inactive
                if (!gateOn) {
                    active = false;
                }
            }
        }

        return volume;
    }

    /**
     * Check if envelope is still active
     */
    bool isActive() const {
        return active;
    }

    /**
     * Check if note is still held
     */
    bool isGateOn() const {
        return gateOn;
    }

    /**
     * Force envelope to stop immediately
     * Returns silent volume (15).
     */
    void stop() {
        active = false;
        gateOn = false;
    }
};

#endif // GENESIS_PSG_ENVELOPE_H
