#ifndef FM_PATCH_H
#define FM_PATCH_H

#include <stdint.h>

/**
 * FM Operator parameters (10 bytes, TFI-compatible order)
 */
struct FMOperator {
    uint8_t mul;    // Multiplier (0-15, 0=0.5x)
    uint8_t dt;     // Detune (0-7, 3=center)
    uint8_t tl;     // Total Level (0-127, 0=loudest)
    uint8_t rs;     // Rate Scaling (0-3)
    uint8_t ar;     // Attack Rate (0-31)
    uint8_t dr;     // Decay Rate (0-31)
    uint8_t sr;     // Sustain Rate (0-31)
    uint8_t rr;     // Release Rate (0-15)
    uint8_t sl;     // Sustain Level (0-15)
    uint8_t ssg;    // SSG-EG mode (0-15, 0=off)
};

/**
 * FM Patch (42 bytes total, TFI-compatible)
 *
 * Operator order: S1, S3, S2, S4 (TFI format, matches file storage)
 * Register order: S1(+0), S3(+8), S2(+4), S4(+12)
 */
struct FMPatch {
    uint8_t algorithm;  // Algorithm (0-7)
    uint8_t feedback;   // Feedback (0-7)
    FMOperator op[4];   // Operators in TFI order: S1, S3, S2, S4
};

#endif // FM_PATCH_H
