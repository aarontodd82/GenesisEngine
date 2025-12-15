#ifndef GENESIS_FM_OPERATOR_H
#define GENESIS_FM_OPERATOR_H

#include <stdint.h>

/**
 * YM2612 FM Operator Parameters
 *
 * Each FM channel has 4 operators that combine to create sound.
 * The algorithm setting determines how operators connect:
 * - Carriers produce audible output
 * - Modulators only affect other operators
 *
 * This structure is 10 bytes and matches TFI file format order.
 *
 * Operator order in FMPatch::op[]: S1, S3, S2, S4 (indices 0, 1, 2, 3)
 * Register offsets for these:      +0,  +8, +4, +12
 */
struct FMOperator {
    uint8_t mul;    // Multiplier (0-15): 0=0.5x, 1=1x, 2=2x, etc.
    uint8_t dt;     // Detune (0-7): 0-3=down, 4=none, 5-7=up. TFI uses 0-7 with 3=center.
    uint8_t tl;     // Total Level (0-127): 0=loudest, 127=silent. Main volume control.
    uint8_t rs;     // Rate Scaling (0-3): Higher values = faster decay at high notes.
    uint8_t ar;     // Attack Rate (0-31): 31=instant attack, lower=slower.
    uint8_t dr;     // Decay Rate (0-31): Rate of decay from peak to sustain level.
    uint8_t sr;     // Sustain Rate (0-31): "Second decay" rate after sustain level reached.
    uint8_t rr;     // Release Rate (0-15): Rate of decay after key-off.
    uint8_t sl;     // Sustain Level (0-15): 0=max volume, 15=silent. Level held during sustain.
    uint8_t ssg;    // SSG-EG mode (0-15): 0=off, 1-15=various looping/inverting envelopes.
};

#endif // GENESIS_FM_OPERATOR_H
