#ifndef GENESIS_BOARD_H
#define GENESIS_BOARD_H

#include <Arduino.h>
#include "config/platform_detect.h"

// =============================================================================
// GenesisBoard - Hardware driver for FM-90s Genesis Engine
// Supports YM2612 (FM) + SN76489 (PSG) via CD74HCT164E shift register
// =============================================================================

class GenesisBoard {
public:
  // -------------------------------------------------------------------------
  // Constructor
  // Pin assignments for your board connection
  // -------------------------------------------------------------------------
  GenesisBoard(
    uint8_t pinWR_P,    // WR_P - SN76489 (PSG) write strobe (active low)
    uint8_t pinWR_Y,    // WR_Y - YM2612 write strobe (active low)
    uint8_t pinIC_Y,    // IC_Y - YM2612 reset (active low)
    uint8_t pinA0_Y,    // A0_Y - YM2612 address bit 0
    uint8_t pinA1_Y,    // A1_Y - YM2612 address bit 1 (port select)
    uint8_t pinSCK,     // SCK  - Shift register clock (CD74HCT164E)
    uint8_t pinSDI      // SDI  - Shift register data in
  );

  // -------------------------------------------------------------------------
  // Initialization
  // Call once in setup() before any playback
  // -------------------------------------------------------------------------
  void begin();

  // -------------------------------------------------------------------------
  // Chip Reset
  // Resets YM2612 and silences PSG
  // -------------------------------------------------------------------------
  void reset();

  // -------------------------------------------------------------------------
  // YM2612 (FM Chip) Functions
  // -------------------------------------------------------------------------

  // Write to YM2612 register
  // port: 0 or 1 (selects register bank via A1)
  // reg: register address (0x00-0xB6)
  // val: value to write
  void writeYM2612(uint8_t port, uint8_t reg, uint8_t val);

  // Write DAC sample (for PCM playback on channel 6)
  // Optimized for streaming - latches address once
  void writeDAC(uint8_t sample);

  // Enable/disable DAC mode on channel 6
  void setDACEnabled(bool enabled);

  // Begin DAC streaming mode (optimization)
  // After calling this, use writeDAC() for fast sample writes
  void beginDACStream();

  // End DAC streaming mode
  void endDACStream();

  // -------------------------------------------------------------------------
  // SN76489 (PSG Chip) Functions
  // -------------------------------------------------------------------------

  // Write to SN76489
  // Handles bit reversal automatically (board wiring quirk)
  void writePSG(uint8_t val);

  // Silence all PSG channels
  void silencePSG();

  // -------------------------------------------------------------------------
  // Utility
  // -------------------------------------------------------------------------

  // Mute all sound (both chips)
  void muteAll();

private:
  // Pin assignments
  uint8_t pinWR_P_;   // WR_P - PSG write
  uint8_t pinWR_Y_;   // WR_Y - YM2612 write
  uint8_t pinIC_Y_;   // IC_Y - YM2612 reset
  uint8_t pinA0_Y_;   // A0_Y - YM2612 address bit 0
  uint8_t pinA1_Y_;   // A1_Y - YM2612 address bit 1
  uint8_t pinSCK_;    // SCK  - Shift register clock
  uint8_t pinSDI_;    // SDI  - Shift register data

  // Timing tracking (smart timing pattern)
  uint32_t lastWriteTime_;

  // DAC streaming state
  bool dacStreamMode_;

  // Timing constants (microseconds)
  // Note: Teensy needs these full values, AVR is slow enough it doesn't
#if defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  static constexpr uint32_t YM_BUSY_US = 5;    // YM2612 busy flag duration
  static constexpr uint32_t PSG_BUSY_US = 9;   // SN76489 write delay
#else
  static constexpr uint32_t YM_BUSY_US = 0;    // AVR GPIO is slow enough
  static constexpr uint32_t PSG_BUSY_US = 0;   // AVR GPIO is slow enough
#endif

  // Fast GPIO - cached port/bitmask for direct port manipulation
#if defined(PLATFORM_AVR)
  volatile uint8_t* portSCK_;
  volatile uint8_t* portSDI_;
  volatile uint8_t* portWR_Y_;
  volatile uint8_t* portWR_P_;
  volatile uint8_t* portA0_Y_;
  volatile uint8_t* portA1_Y_;
  uint8_t maskSCK_;
  uint8_t maskSDI_;
  uint8_t maskWR_Y_;
  uint8_t maskWR_P_;
  uint8_t maskA0_Y_;
  uint8_t maskA1_Y_;
#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  volatile uint32_t* portSetSCK_;
  volatile uint32_t* portClearSCK_;
  volatile uint32_t* portSetSDI_;
  volatile uint32_t* portClearSDI_;
  volatile uint32_t* portSetWR_Y_;
  volatile uint32_t* portClearWR_Y_;
  volatile uint32_t* portSetWR_P_;
  volatile uint32_t* portClearWR_P_;
  volatile uint32_t* portSetA0_Y_;
  volatile uint32_t* portClearA0_Y_;
  volatile uint32_t* portSetA1_Y_;
  volatile uint32_t* portClearA1_Y_;
  uint32_t maskSCK_;
  uint32_t maskSDI_;
  uint32_t maskWR_Y_;
  uint32_t maskWR_P_;
  uint32_t maskA0_Y_;
  uint32_t maskA1_Y_;
#elif defined(PLATFORM_ESP32)
  uint8_t pinSCK_cached_;
  uint8_t pinSDI_cached_;
  uint8_t pinWR_Y_cached_;
  uint8_t pinWR_P_cached_;
  uint8_t pinA0_Y_cached_;
  uint8_t pinA1_Y_cached_;
#endif

  // -------------------------------------------------------------------------
  // Internal Functions
  // -------------------------------------------------------------------------

  // Shift out 8 bits to the CD74HCT164E (optimized per platform)
  void shiftOut8(uint8_t data);

  // Initialize fast GPIO (called from begin())
  void initFastGPIO();

  // Bit reversal for SN76489 (board wiring)
  uint8_t reverseBits(uint8_t b);

  // Smart timing - only waits if needed
  inline void waitIfNeeded(uint32_t minMicros);

  // Pulse a pin low for a short duration
  inline void pulseLow(uint8_t pin);
};

#endif // GENESIS_BOARD_H
