#include "PSGFrequency.h"
#include "../GenesisBoard.h"

// Pre-calculated tone table for MIDI notes 0-127
// Formula: N = 3579545 / (32 * freq)
// Based on NTSC PSG clock (3579545 Hz)
// Values > 1023 are clamped to 1023
const uint16_t psgToneTable[128] GENESIS_PROGMEM = {
    // Octave -1 (MIDI 0-11) - too low, clamp to max
    1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023,
    // Octave 0 (MIDI 12-23)
    1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023, 1023,
    // Octave 1 (MIDI 24-35)
    1023, 1023, 1023, 1023, 967, 912, 861, 813, 767, 724, 683, 645,
    // Octave 2 (MIDI 36-47)
    609, 575, 542, 512, 483, 456, 431, 407, 384, 362, 342, 323,
    // Octave 3 (MIDI 48-59)
    305, 287, 271, 256, 242, 228, 215, 203, 192, 181, 171, 161,
    // Octave 4 (MIDI 60-71) - Middle C at 60
    152, 144, 136, 128, 121, 114, 108, 102, 96, 91, 85, 81,
    // Octave 5 (MIDI 72-83)
    76, 72, 68, 64, 60, 57, 54, 51, 48, 45, 43, 40,
    // Octave 6 (MIDI 84-95)
    38, 36, 34, 32, 30, 28, 27, 25, 24, 23, 21, 20,
    // Octave 7 (MIDI 96-107)
    19, 18, 17, 16, 15, 14, 13, 13, 12, 11, 11, 10,
    // Octave 8+ (MIDI 108-127) - very high, values get small
    9, 9, 8, 8, 8, 7, 7, 6, 6, 6, 5, 5,
    5, 5, 4, 4, 4, 4, 3, 3,
};

namespace PSGFrequency {

uint16_t midiToTone(uint8_t midiNote) {
    if (midiNote > 127) midiNote = 127;
    return GENESIS_READ_WORD(&psgToneTable[midiNote]);
}

void writeToneValue(GenesisBoard& board, uint8_t channel, uint16_t tone) {
    if (channel > 2) return;  // Only tone channels 0-2

    // Clamp tone to valid range
    if (tone > 1023) tone = 1023;
    if (tone < 1) tone = 1;

    // SN76489 tone format:
    // First byte:  1 CC 0 DDDD  (CC=channel, DDDD=low 4 bits of tone)
    // Second byte: 0 0 DD DDDD  (remaining 6 bits of tone)
    board.writePSG(0x80 | (channel << 5) | (tone & 0x0F));
    board.writePSG((tone >> 4) & 0x3F);
}

void writeToChannel(GenesisBoard& board, uint8_t channel, uint8_t midiNote) {
    if (channel > 2) return;

    uint16_t tone = midiToTone(midiNote);
    writeToneValue(board, channel, tone);
}

void setVolume(GenesisBoard& board, uint8_t channel, uint8_t volume) {
    if (channel > 3) return;  // Channels 0-3 (3 is noise)
    if (volume > 15) volume = 15;

    // SN76489 volume format: 1 CC 1 VVVV (CC=channel, VVVV=attenuation)
    board.writePSG(0x90 | (channel << 5) | volume);
}

void setNoise(GenesisBoard& board, bool white, uint8_t shift) {
    if (shift > 3) shift = 3;

    // SN76489 noise format: 1110 0 W SS
    // W = white noise (1) or periodic (0)
    // SS = shift rate (0-3)
    uint8_t noiseByte = 0xE0 | (white ? 0x04 : 0x00) | shift;
    board.writePSG(noiseByte);
}

void playNote(GenesisBoard& board, uint8_t channel, uint8_t midiNote, uint8_t volume) {
    if (channel > 2) return;

    writeToChannel(board, channel, midiNote);
    setVolume(board, channel, volume);
}

void silence(GenesisBoard& board, uint8_t channel) {
    if (channel > 3) return;
    setVolume(board, channel, 15);  // 15 = maximum attenuation = silent
}

} // namespace PSGFrequency
