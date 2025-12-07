#ifndef VGM_COMMANDS_H
#define VGM_COMMANDS_H

#include <Arduino.h>

// =============================================================================
// VGM File Format Constants
// Based on VGM specification v1.71
// =============================================================================

// -----------------------------------------------------------------------------
// File Header
// -----------------------------------------------------------------------------
static constexpr uint32_t VGM_MAGIC = 0x206D6756;  // "Vgm " in little-endian
static constexpr uint32_t VGM_HEADER_SIZE = 0x100;  // Modern VGM header size

// Header offsets (all little-endian)
static constexpr uint8_t VGM_OFF_EOF        = 0x04;  // End of file offset
static constexpr uint8_t VGM_OFF_VERSION    = 0x08;  // Version number (BCD)
static constexpr uint8_t VGM_OFF_SN76489    = 0x0C;  // SN76489 clock
static constexpr uint8_t VGM_OFF_YM2413     = 0x10;  // YM2413 clock
static constexpr uint8_t VGM_OFF_GD3        = 0x14;  // GD3 tag offset
static constexpr uint8_t VGM_OFF_SAMPLES    = 0x18;  // Total samples
static constexpr uint8_t VGM_OFF_LOOP       = 0x1C;  // Loop offset
static constexpr uint8_t VGM_OFF_LOOP_SAMP  = 0x20;  // Loop samples
static constexpr uint8_t VGM_OFF_RATE       = 0x24;  // Rate (v1.01+)
static constexpr uint8_t VGM_OFF_YM2612     = 0x2C;  // YM2612 clock (v1.10+)
static constexpr uint8_t VGM_OFF_DATA       = 0x34;  // VGM data offset (v1.50+)

// -----------------------------------------------------------------------------
// VGM Commands
// -----------------------------------------------------------------------------

// PSG (SN76489) commands
static constexpr uint8_t VGM_CMD_PSG        = 0x50;  // Write to SN76489

// YM2612 commands (Genesis/Mega Drive)
static constexpr uint8_t VGM_CMD_YM2612_P0  = 0x52;  // YM2612 port 0 write
static constexpr uint8_t VGM_CMD_YM2612_P1  = 0x53;  // YM2612 port 1 write

// Other FM chips (for reference - not directly supported on this hardware)
static constexpr uint8_t VGM_CMD_YM2413     = 0x51;  // YM2413 (Master System FM)
static constexpr uint8_t VGM_CMD_YM2151     = 0x54;  // YM2151
static constexpr uint8_t VGM_CMD_YM2203     = 0x55;  // YM2203

// Wait commands
static constexpr uint8_t VGM_CMD_WAIT       = 0x61;  // Wait N samples (16-bit)
static constexpr uint8_t VGM_CMD_WAIT_735   = 0x62;  // Wait 735 samples (1/60 sec NTSC)
static constexpr uint8_t VGM_CMD_WAIT_882   = 0x63;  // Wait 882 samples (1/50 sec PAL)

// Control commands
static constexpr uint8_t VGM_CMD_END        = 0x66;  // End of VGM data

// Data block
static constexpr uint8_t VGM_CMD_DATA_BLOCK = 0x67;  // Data block (PCM, etc.)

// Short waits (0x70-0x7F = wait 1-16 samples)
static constexpr uint8_t VGM_CMD_WAIT_SHORT_BASE = 0x70;

// YM2612 DAC + wait combined (0x80-0x8F)
// 0x80 = write DAC + wait 0 samples
// 0x8F = write DAC + wait 15 samples
static constexpr uint8_t VGM_CMD_DAC_WAIT_BASE = 0x80;

// DAC stream commands (0x90-0x95)
static constexpr uint8_t VGM_CMD_DAC_SETUP    = 0x90;  // Setup stream
static constexpr uint8_t VGM_CMD_DAC_DATA     = 0x91;  // Set stream data
static constexpr uint8_t VGM_CMD_DAC_FREQ     = 0x92;  // Set stream frequency
static constexpr uint8_t VGM_CMD_DAC_START    = 0x93;  // Start stream
static constexpr uint8_t VGM_CMD_DAC_STOP     = 0x94;  // Stop stream
static constexpr uint8_t VGM_CMD_DAC_START_FAST = 0x95; // Start stream (fast)

// PCM data seek
static constexpr uint8_t VGM_CMD_PCM_SEEK   = 0xE0;  // Seek in PCM data bank

// -----------------------------------------------------------------------------
// Timing Constants
// -----------------------------------------------------------------------------
static constexpr uint32_t VGM_SAMPLE_RATE = 44100;
static constexpr float VGM_MICROS_PER_SAMPLE = 1000000.0f / 44100.0f;  // ~22.6757

// Common wait values in samples
static constexpr uint16_t VGM_WAIT_NTSC = 735;   // 60 Hz frame
static constexpr uint16_t VGM_WAIT_PAL  = 882;   // 50 Hz frame

// -----------------------------------------------------------------------------
// Chip Clock Frequencies
// -----------------------------------------------------------------------------
static constexpr uint32_t YM2612_CLOCK_NTSC = 7670453;   // 7.67 MHz (NTSC Genesis)
static constexpr uint32_t YM2612_CLOCK_PAL  = 7600489;   // 7.60 MHz (PAL Genesis)
static constexpr uint32_t SN76489_CLOCK_NTSC = 3579545;  // 3.58 MHz (NTSC)
static constexpr uint32_t SN76489_CLOCK_PAL  = 3546895;  // 3.55 MHz (PAL)

// -----------------------------------------------------------------------------
// Data Block Types
// -----------------------------------------------------------------------------
static constexpr uint8_t VGM_DATA_YM2612_PCM = 0x00;  // YM2612 PCM data

// -----------------------------------------------------------------------------
// VGM Header Structure (for reference)
// -----------------------------------------------------------------------------
struct VGMHeader {
  uint32_t magic;           // 0x00: "Vgm "
  uint32_t eofOffset;       // 0x04: Relative offset to end of file
  uint32_t version;         // 0x08: Version number (BCD, e.g., 0x00000171)
  uint32_t sn76489Clock;    // 0x0C: SN76489 clock (0 = not used)
  uint32_t ym2413Clock;     // 0x10: YM2413 clock (0 = not used)
  uint32_t gd3Offset;       // 0x14: Relative offset to GD3 tag
  uint32_t totalSamples;    // 0x18: Total samples (at 44100 Hz)
  uint32_t loopOffset;      // 0x1C: Relative offset to loop point
  uint32_t loopSamples;     // 0x20: Number of samples in loop
  uint32_t rate;            // 0x24: Recording rate (v1.01+)
  // 0x28: SN76489 flags
  // 0x2A: Reserved
  // 0x2C: YM2612 clock (v1.10+)
  // 0x30: YM2151 clock
  // 0x34: VGM data offset (v1.50+, relative to 0x34)
  // ... more fields in later versions
};

#endif // VGM_COMMANDS_H
