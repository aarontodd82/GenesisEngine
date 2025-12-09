#ifndef PSG_ENVELOPE_H
#define PSG_ENVELOPE_H

#include <stdint.h>

/**
 * PSG Software Envelope (EEF-compatible)
 *
 * The SN76489 has no hardware envelope, so we implement it in software
 * by updating volume at 60Hz.
 *
 * Each data byte:
 *   Lower nibble (bits 0-3): Volume (0=loudest, 15=silent)
 *   Upper nibble (bits 4-7): Pitch shift (0=none, 1-7=up, 8-E=down semitones)
 *
 * loopStart = 0xFF means no loop (play once and hold last value)
 */
struct PSGEnvelope {
    uint8_t data[64];    // Envelope data (max 64 steps)
    uint8_t length;      // Actual length (1-64)
    uint8_t loopStart;   // Loop start position (0xFF = no loop)
};

#endif // PSG_ENVELOPE_H
