#ifndef GENESIS_FM_PATCH_H
#define GENESIS_FM_PATCH_H

#include <stdint.h>
#include "FMOperator.h"

// Forward declaration - avoids circular include with GenesisBoard.h
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
 * Complete FM Voice/Patch Definition
 *
 * Contains all parameters needed to fully define an FM sound:
 * - Algorithm: How the 4 operators connect (0-7)
 * - Feedback: Self-modulation amount for operator 1 (0-7)
 * - 4 operators with envelope and tuning parameters
 * - Pan: Stereo placement
 * - AMS/PMS: LFO sensitivity for tremolo/vibrato
 *
 * Size: 45 bytes (42 bytes TFI-compatible + 3 bytes extended)
 *
 * Operator order matches TFI format: S1, S3, S2, S4 (indices 0, 1, 2, 3)
 * YM2612 register offsets for these: +0, +8, +4, +12
 */
struct FMPatch {
    // Core FM parameters (TFI-compatible, 42 bytes)
    uint8_t algorithm;  // Algorithm (0-7)
    uint8_t feedback;   // Feedback (0-7)
    FMOperator op[4];   // Four operators in TFI order: S1, S3, S2, S4

    // Extended parameters (3 bytes)
    uint8_t pan;        // FMPanMode: 0=center, 1=left, 2=right
    uint8_t ams;        // Amplitude Modulation Sensitivity (0-3)
    uint8_t pms;        // Phase Modulation Sensitivity (0-7, vibrato depth)

    /**
     * Get the raw YM2612 L/R/AMS/PMS register value
     * Used for writing to register 0xB4 + channel offset
     */
    uint8_t getLRAMSPMS() const {
        uint8_t lr;
        switch (pan) {
            case FM_PAN_LEFT:   lr = 0x80; break;
            case FM_PAN_RIGHT:  lr = 0x40; break;
            default:            lr = 0xC0; break;  // Center = both speakers
        }
        return lr | ((ams & 0x03) << 4) | (pms & 0x07);
    }

    /**
     * Initialize to sensible defaults
     * Call this on a new patch before setting parameters
     */
    void initDefaults() {
        algorithm = 0;
        feedback = 0;
        pan = FM_PAN_CENTER;
        ams = 0;
        pms = 0;
        // Note: operators should be initialized separately
    }
};

// Patch size constants
#define FM_PATCH_SIZE_LEGACY   42  // TFI format (algorithm, feedback, 4 operators)
#define FM_PATCH_SIZE_EXTENDED 45  // Full format (adds pan, ams, pms)

/**
 * FM Patch Utility Functions
 *
 * Helper functions for loading patches to hardware and parsing patch data.
 */
namespace FMPatchUtils {
    /**
     * Load a patch to an FM channel
     *
     * Writes all operator parameters, algorithm, feedback, and panning
     * to the YM2612. Does not affect frequency or key state.
     *
     * @param board GenesisBoard instance
     * @param channel FM channel (0-5)
     * @param patch Patch to load
     */
    void loadToChannel(GenesisBoard& board, uint8_t channel, const struct FMPatch& patch);

    /**
     * Parse patch data from raw bytes
     *
     * Converts raw patch data (from SysEx, files, etc.) into an FMPatch structure.
     * Supports both TFI format (42 bytes) and extended format (45 bytes).
     *
     * @param data Raw patch bytes (42 or 45 bytes)
     * @param patch Output patch structure
     * @param extended If true, expects 45 bytes with pan/ams/pms; otherwise 42 bytes
     */
    void parseFromData(const uint8_t* data, struct FMPatch& patch, bool extended = false);

    /**
     * Get which operators are carriers for a given algorithm
     *
     * Carriers produce audible output; modulators only affect other operators.
     * This is essential for velocity scaling (only scale carrier volumes).
     *
     * @param algorithm Algorithm number (0-7)
     * @param isCarrier Output array of 4 bools (indices match FMPatch::op order)
     *
     * Carrier patterns by algorithm (in FMPatch::op index order S1,S3,S2,S4):
     *   ALG 0-3: S4 only           -> isCarrier[3]
     *   ALG 4:   S2, S4            -> isCarrier[2], isCarrier[3]
     *   ALG 5-6: S2, S3, S4        -> isCarrier[1], isCarrier[2], isCarrier[3]
     *   ALG 7:   All carriers      -> all true
     */
    void getCarrierMask(uint8_t algorithm, bool* isCarrier);

    /**
     * YM2612 operator register offsets
     *
     * Maps FMPatch::op index to register offset within a channel.
     * Usage: baseReg + OPERATOR_OFFSETS[opIndex] + channelOffset
     *
     * Index 0 (S1) -> offset 0
     * Index 1 (S3) -> offset 8
     * Index 2 (S2) -> offset 4
     * Index 3 (S4) -> offset 12
     */
    extern const uint8_t OPERATOR_OFFSETS[4];
}

#endif // GENESIS_FM_PATCH_H
