/**
 * BasicPlayback - Simple VGM playback example
 *
 * This example plays a VGM file from PROGMEM (flash memory).
 * Works on all Arduino-compatible boards.
 *
 * Hardware:
 *   FM-90s Genesis Engine board connected to the pins defined below
 *
 * Usage:
 *   1. Convert your VGM file to a header using vgm2header.py:
 *      python tools/vgm2header.py your_song.vgm
 *
 *   2. Copy the generated .h file to this sketch folder
 *
 *   3. Update the #include below to match your file name
 *
 *   4. Upload and enjoy!
 */

#include <GenesisEngine.h>

// Include your converted VGM file here
#include "greenhill.h"

// =============================================================================
// Pin Configuration
// Adjust these to match your wiring to the FM-90s Genesis Engine board
//
// NOTE: SCK/SDI are ignored when using hardware SPI (default).
//       Hardware SPI pins: Uno=13/11, Mega=52/51, Teensy4=13/11, ESP32=18/23
// =============================================================================

#if defined(ARDUINO_ARCH_ESP32)
  // ESP32: Avoid GPIO 0-3 (boot/serial), 6-11 (flash), 12/15 (boot strapping)
  const uint8_t PIN_WR_P = 16;  // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 17;  // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 25;  // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 26;  // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 27;  // A1_Y - YM2612 address bit 1 (port select)
  const uint8_t PIN_SCK  = 18;  // SCK  - Hardware SPI (fixed on ESP32)
  const uint8_t PIN_SDI  = 23;  // SDI  - Hardware SPI (fixed on ESP32)
#else
  // Arduino/Teensy defaults
  const uint8_t PIN_WR_P = 2;   // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 3;   // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 4;   // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 5;   // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 6;   // A1_Y - YM2612 address bit 1 (port select)
  const uint8_t PIN_SCK  = 7;   // SCK  - Shift register clock (ignored w/ HW SPI)
  const uint8_t PIN_SDI  = 8;   // SDI  - Shift register data (ignored w/ HW SPI)
#endif

// =============================================================================
// Create player objects
// =============================================================================

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);
GenesisEngine player(board);

// =============================================================================
// Setup
// =============================================================================

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
    // Wait for serial on USB-based boards (timeout after 3 seconds)
  }

  Serial.println(F("FM-90s Genesis Engine - Basic Playback Example"));
  Serial.println(F("=============================================="));

  // Initialize the board
  board.begin();
  Serial.println(F("Board initialized"));

  // Enable looping (song will repeat forever)
  player.setLooping(true);

  // Start playback (using chunked API for large files)
  if (player.playChunked(greenhill_chunks, greenhill_chunk_sizes,
                         GREENHILL_NUM_CHUNKS, GREENHILL_TOTAL_LEN)) {
    Serial.println(F("Playback started!"));
    Serial.print(F("Duration: "));
    Serial.print(player.getDurationSeconds());
    Serial.println(F(" seconds"));

    if (player.hasYM2612()) {
      Serial.println(F("  - YM2612 (FM) data present"));
    }
    if (player.hasSN76489()) {
      Serial.println(F("  - SN76489 (PSG) data present"));
    }
    if (player.hasLoop()) {
      Serial.println(F("  - Loop point defined"));
    }
  } else {
    Serial.println(F("Failed to start playback!"));
  }
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
  // IMPORTANT: Must call update() as frequently as possible!
  player.update();

  // Optional: Print position every second
  static uint32_t lastPrint = 0;
  if (player.isPlaying() && millis() - lastPrint >= 1000) {
    lastPrint = millis();
    Serial.print(F("Position: "));
    Serial.print(player.getPositionSeconds(), 1);
    Serial.print(F(" / "));
    Serial.print(player.getDurationSeconds(), 1);
    Serial.println(F(" sec"));
  }

  // Check if playback finished (only relevant if looping is disabled)
  if (player.isFinished()) {
    Serial.println(F("Playback finished!"));
    delay(2000);

    // Restart
    player.playChunked(greenhill_chunks, greenhill_chunk_sizes,
                       GREENHILL_NUM_CHUNKS, GREENHILL_TOTAL_LEN);
  }
}
