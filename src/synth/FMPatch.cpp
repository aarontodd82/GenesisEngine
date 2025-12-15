#include "FMPatch.h"
#include "../GenesisBoard.h"

namespace FMPatchUtils {

// Operator register offsets: maps FMPatch::op index to YM2612 register offset
// S1=+0, S3=+8, S2=+4, S4=+12 (TFI order to register order)
const uint8_t OPERATOR_OFFSETS[4] = {0, 8, 4, 12};

void loadToChannel(GenesisBoard& board, uint8_t channel, const struct FMPatch& patch) {
    if (channel > 5) return;

    // Determine port (0 for channels 0-2, 1 for channels 3-5) and channel register offset
    uint8_t port = (channel >= 3) ? 1 : 0;
    uint8_t chReg = channel % 3;

    // Write algorithm and feedback (register 0xB0 + channel)
    board.writeYM2612(port, 0xB0 + chReg, (patch.feedback << 3) | patch.algorithm);

    // Write L/R/AMS/PMS (register 0xB4 + channel)
    board.writeYM2612(port, 0xB4 + chReg, patch.getLRAMSPMS());

    // Write all 4 operators
    for (uint8_t i = 0; i < 4; i++) {
        uint8_t regOff = OPERATOR_OFFSETS[i];
        const FMOperator& op = patch.op[i];

        // DT/MUL (register 0x30)
        board.writeYM2612(port, 0x30 + regOff + chReg, (op.dt << 4) | op.mul);

        // TL (register 0x40) - Total Level (volume)
        board.writeYM2612(port, 0x40 + regOff + chReg, op.tl);

        // RS/AR (register 0x50) - Rate Scaling / Attack Rate
        board.writeYM2612(port, 0x50 + regOff + chReg, (op.rs << 6) | op.ar);

        // AM/DR (register 0x60) - AM enable is bit 7, we don't set it here
        board.writeYM2612(port, 0x60 + regOff + chReg, op.dr);

        // SR (register 0x70) - Sustain Rate (also called "second decay")
        board.writeYM2612(port, 0x70 + regOff + chReg, op.sr);

        // SL/RR (register 0x80) - Sustain Level / Release Rate
        board.writeYM2612(port, 0x80 + regOff + chReg, (op.sl << 4) | op.rr);

        // SSG-EG (register 0x90)
        board.writeYM2612(port, 0x90 + regOff + chReg, op.ssg);
    }
}

void parseFromData(const uint8_t* data, struct FMPatch& patch, bool extended) {
    patch.algorithm = data[0];
    patch.feedback = data[1];

    // Parse 4 operators (10 bytes each, starting at offset 2)
    for (uint8_t op = 0; op < 4; op++) {
        const uint8_t* opData = &data[2 + op * 10];
        patch.op[op].mul = opData[0];
        patch.op[op].dt  = opData[1];
        patch.op[op].tl  = opData[2];
        patch.op[op].rs  = opData[3];
        patch.op[op].ar  = opData[4];
        patch.op[op].dr  = opData[5];
        patch.op[op].sr  = opData[6];
        patch.op[op].rr  = opData[7];
        patch.op[op].sl  = opData[8];
        patch.op[op].ssg = opData[9];
    }

    // Extended format includes pan/ams/pms at end
    if (extended) {
        patch.pan = data[42];
        patch.ams = data[43];
        patch.pms = data[44];
    } else {
        // Default to center pan, no LFO sensitivity
        patch.pan = FM_PAN_CENTER;
        patch.ams = 0;
        patch.pms = 0;
    }
}

void getCarrierMask(uint8_t algorithm, bool* isCarrier) {
    // Initialize all to false (modulators)
    isCarrier[0] = isCarrier[1] = isCarrier[2] = isCarrier[3] = false;

    // Set carriers based on algorithm
    // Remember: indices are S1=0, S3=1, S2=2, S4=3 (TFI order)
    switch (algorithm) {
        case 0: case 1: case 2: case 3:
            // S4 only is carrier
            isCarrier[3] = true;
            break;

        case 4:
            // S2 and S4 are carriers
            isCarrier[2] = true;  // S2 is at index 2 in TFI order
            isCarrier[3] = true;  // S4 is at index 3
            break;

        case 5: case 6:
            // S2, S3, S4 are carriers
            isCarrier[1] = true;  // S3 is at index 1 in TFI order
            isCarrier[2] = true;  // S2 is at index 2
            isCarrier[3] = true;  // S4 is at index 3
            break;

        case 7:
            // All operators are carriers (additive synthesis)
            isCarrier[0] = isCarrier[1] = isCarrier[2] = isCarrier[3] = true;
            break;
    }
}

} // namespace FMPatchUtils
