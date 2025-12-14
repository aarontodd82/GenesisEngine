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
 * Pan/Stereo output modes
 */
enum PanMode : uint8_t {
    PAN_CENTER = 0,  // Both speakers (0xC0)
    PAN_LEFT = 1,    // Left only (0x80)
    PAN_RIGHT = 2    // Right only (0x40)
};

/**
 * FM Patch - Complete channel voice definition
 * Extended format: 45 bytes (TFI 42 bytes + pan, ams, pms)
 *
 * Operator order: S1, S3, S2, S4 (TFI format, matches file storage)
 * Register order: S1(+0), S3(+8), S2(+4), S4(+12)
 *
 * Includes all parameters needed to fully define a sound:
 * - Algorithm and feedback (tone character)
 * - 4 operators with envelope and tuning parameters
 * - Pan (stereo placement)
 * - AMS/PMS (LFO sensitivity for vibrato/tremolo)
 */
struct FMPatch {
    // Core FM parameters (TFI-compatible, 42 bytes)
    uint8_t algorithm;  // Algorithm (0-7)
    uint8_t feedback;   // Feedback (0-7)
    FMOperator op[4];   // Operators in TFI order: S1, S3, S2, S4

    // Channel voice parameters (extended format, 3 bytes)
    uint8_t pan;        // PanMode: 0=center, 1=left, 2=right
    uint8_t ams;        // Amplitude Modulation Sensitivity (0-3)
    uint8_t pms;        // Phase Modulation Sensitivity (0-7) - vibrato depth

    // Get the raw YM2612 L/R/AMS/PMS register value (0xB4)
    uint8_t getLRAMSPMS() const {
        uint8_t lr;
        switch (pan) {
            case PAN_LEFT:   lr = 0x80; break;
            case PAN_RIGHT:  lr = 0x40; break;
            default:         lr = 0xC0; break;  // Center = both
        }
        return lr | ((ams & 0x03) << 4) | (pms & 0x07);
    }

    // Initialize defaults
    void initDefaults() {
        pan = PAN_CENTER;
        ams = 0;
        pms = 0;
    }
};

// Legacy patch size (TFI format)
#define FM_PATCH_SIZE_LEGACY 42
// Extended patch size (with pan/ams/pms)
#define FM_PATCH_SIZE_EXTENDED 45

#endif // FM_PATCH_H
