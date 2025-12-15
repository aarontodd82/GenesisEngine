#include "FMFrequency.h"
#include "../GenesisBoard.h"

// Pre-calculated frequency table for MIDI notes 0-127
// Formula: F = (144 * freq * 2^(21-block)) / 7670453
// Based on A4 = 440Hz standard tuning, NTSC YM2612 clock
const FMFreqEntry fmFreqTable[128] GENESIS_PROGMEM = {
    // Octave -1 (MIDI 0-11) - below audible range, use block 0
    {617, 0}, {654, 0}, {693, 0}, {734, 0}, {778, 0}, {824, 0},
    {873, 0}, {925, 0}, {980, 0}, {1038, 0}, {1100, 0}, {1165, 0},
    // Octave 0 (MIDI 12-23)
    {617, 1}, {654, 1}, {693, 1}, {734, 1}, {778, 1}, {824, 1},
    {873, 1}, {925, 1}, {980, 1}, {1038, 1}, {1100, 1}, {1165, 1},
    // Octave 1 (MIDI 24-35)
    {617, 2}, {654, 2}, {693, 2}, {734, 2}, {778, 2}, {824, 2},
    {873, 2}, {925, 2}, {980, 2}, {1038, 2}, {1100, 2}, {1165, 2},
    // Octave 2 (MIDI 36-47)
    {617, 3}, {654, 3}, {693, 3}, {734, 3}, {778, 3}, {824, 3},
    {873, 3}, {925, 3}, {980, 3}, {1038, 3}, {1100, 3}, {1165, 3},
    // Octave 3 (MIDI 48-59)
    {617, 4}, {654, 4}, {693, 4}, {734, 4}, {778, 4}, {824, 4},
    {873, 4}, {925, 4}, {980, 4}, {1038, 4}, {1100, 4}, {1165, 4},
    // Octave 4 (MIDI 60-71) - Middle C at 60
    {617, 5}, {654, 5}, {693, 5}, {734, 5}, {778, 5}, {824, 5},
    {873, 5}, {925, 5}, {980, 5}, {1038, 5}, {1100, 5}, {1165, 5},
    // Octave 5 (MIDI 72-83)
    {617, 6}, {654, 6}, {693, 6}, {734, 6}, {778, 6}, {824, 6},
    {873, 6}, {925, 6}, {980, 6}, {1038, 6}, {1100, 6}, {1165, 6},
    // Octave 6 (MIDI 84-95)
    {617, 7}, {654, 7}, {693, 7}, {734, 7}, {778, 7}, {824, 7},
    {873, 7}, {925, 7}, {980, 7}, {1038, 7}, {1100, 7}, {1165, 7},
    // Octave 7+ (MIDI 96-127) - above normal range, clamp to block 7
    {617, 7}, {654, 7}, {693, 7}, {734, 7}, {778, 7}, {824, 7},
    {873, 7}, {925, 7}, {980, 7}, {1038, 7}, {1100, 7}, {1165, 7},
    {617, 7}, {654, 7}, {693, 7}, {734, 7}, {778, 7}, {824, 7},
    {873, 7}, {925, 7}, {980, 7}, {1038, 7}, {1100, 7}, {1165, 7},
    {617, 7}, {654, 7}, {693, 7}, {734, 7}, {778, 7}, {824, 7},
    {873, 7}, {925, 7},
};

namespace FMFrequency {

void midiToFM(uint8_t midiNote, uint16_t* fnum, uint8_t* block) {
    // Clamp to valid range
    if (midiNote > 127) midiNote = 127;

    // Read from PROGMEM
    *fnum = GENESIS_READ_WORD(&fmFreqTable[midiNote].fnum);
    *block = GENESIS_READ_BYTE(&fmFreqTable[midiNote].block);
}

uint16_t applyBend(uint16_t fnum, int16_t bend, uint8_t bendRange) {
    if (bend == 0) return fnum;

    // Calculate bend amount
    // For ±2 semitones (default): full bend = ±12% frequency change
    // Scale factor: (2^(bendRange/12) - 1) ≈ 0.12 for 2 semitones
    // Using integer math: bend * fnum / 68000 gives ~12% at full bend
    int32_t scaleFactor = 68000 / bendRange;  // Adjust for bend range
    int32_t bendAmount = ((int32_t)fnum * bend) / scaleFactor;

    // Apply and clamp
    int32_t result = fnum + bendAmount;
    if (result < 0) result = 0;
    if (result > 2047) result = 2047;

    return (uint16_t)result;
}

void writeToChannel(GenesisBoard& board, uint8_t channel, uint8_t midiNote) {
    if (channel > 5) return;

    // Get frequency data
    uint16_t fnum;
    uint8_t block;
    midiToFM(midiNote, &fnum, &block);

    // Determine port and channel register offset
    uint8_t port = (channel >= 3) ? 1 : 0;
    uint8_t chReg = channel % 3;

    // Write frequency registers (high byte first for proper latching)
    // Register 0xA4: Block (bits 3-5) and F-number high bits (bits 0-2)
    board.writeYM2612(port, 0xA4 + chReg, (block << 3) | (fnum >> 8));
    // Register 0xA0: F-number low byte
    board.writeYM2612(port, 0xA0 + chReg, fnum & 0xFF);
}

void writeToChannelWithBend(GenesisBoard& board, uint8_t channel,
                             uint8_t midiNote, int16_t bend) {
    if (channel > 5) return;

    // Get base frequency
    uint16_t fnum;
    uint8_t block;
    midiToFM(midiNote, &fnum, &block);

    // Apply pitch bend
    if (bend != 0) {
        fnum = applyBend(fnum, bend, 2);
    }

    // Determine port and channel register offset
    uint8_t port = (channel >= 3) ? 1 : 0;
    uint8_t chReg = channel % 3;

    // Write frequency registers
    board.writeYM2612(port, 0xA4 + chReg, (block << 3) | (fnum >> 8));
    board.writeYM2612(port, 0xA0 + chReg, fnum & 0xFF);
}

void keyOn(GenesisBoard& board, uint8_t channel, uint8_t operatorMask) {
    if (channel > 5) return;

    // Key on register (0x28) is always on port 0
    // Bits 4-7: operator enable mask
    // Bits 0-2: channel (0-2 for port 0 channels, 4-6 for port 1 channels)
    uint8_t chBits = (channel >= 3) ? (channel - 3 + 4) : channel;
    board.writeYM2612(0, 0x28, operatorMask | chBits);
}

void keyOff(GenesisBoard& board, uint8_t channel) {
    if (channel > 5) return;

    // Key off = write 0 to operator bits
    uint8_t chBits = (channel >= 3) ? (channel - 3 + 4) : channel;
    board.writeYM2612(0, 0x28, 0x00 | chBits);
}

} // namespace FMFrequency
