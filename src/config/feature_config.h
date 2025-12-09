#ifndef GENESIS_ENGINE_FEATURE_CONFIG_H
#define GENESIS_ENGINE_FEATURE_CONFIG_H

#include "platform_detect.h"

// =============================================================================
// Feature Configuration
// Features are auto-enabled based on platform, but can be overridden
// =============================================================================

// -----------------------------------------------------------------------------
// SD Card Support
// Enabled on all platforms (AVR reads byte-by-byte, no large buffers needed)
// Note: AVR platforms (Uno/Mega) require external SD module
// -----------------------------------------------------------------------------
#ifndef GENESIS_ENGINE_DISABLE_SD
  #if defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3) || \
      defined(PLATFORM_ESP32) || defined(PLATFORM_RP2040) || \
      defined(PLATFORM_SAM) || defined(PLATFORM_AVR)
    #define GENESIS_ENGINE_USE_SD 1
  #endif
#endif

// Ensure GENESIS_ENGINE_USE_SD is defined (as 0) if not enabled
#ifndef GENESIS_ENGINE_USE_SD
  #define GENESIS_ENGINE_USE_SD 0
#endif

// SD Card chip select pin (platform-specific defaults)
#ifndef GENESIS_ENGINE_SD_CS_PIN
  #if defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
    #define GENESIS_ENGINE_SD_CS_PIN BUILTIN_SDCARD
  #elif defined(PLATFORM_AVR) && defined(__AVR_ATmega2560__)
    #define GENESIS_ENGINE_SD_CS_PIN 53  // Mega default
  #elif defined(PLATFORM_AVR)
    #define GENESIS_ENGINE_SD_CS_PIN 10  // Uno default
  #elif defined(PLATFORM_ESP32)
    #define GENESIS_ENGINE_SD_CS_PIN 5   // Common ESP32 default
  #else
    #define GENESIS_ENGINE_SD_CS_PIN 10  // Generic default
  #endif
#endif

// -----------------------------------------------------------------------------
// VGZ Decompression (gzip)
// Only on platforms with enough RAM for decompression buffer (~45KB)
// -----------------------------------------------------------------------------
#ifndef GENESIS_ENGINE_DISABLE_VGZ
  #if defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3) || \
      defined(PLATFORM_ESP32) || defined(PLATFORM_RP2040)
    #define GENESIS_ENGINE_USE_VGZ 1
  #endif
#endif

// Ensure GENESIS_ENGINE_USE_VGZ is defined (as 0) if not enabled
#ifndef GENESIS_ENGINE_USE_VGZ
  #define GENESIS_ENGINE_USE_VGZ 0
#endif

// -----------------------------------------------------------------------------
// USB MIDI Support
// Only on Teensy (native USB MIDI)
// -----------------------------------------------------------------------------
#ifndef GENESIS_ENGINE_DISABLE_MIDI
  #if defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
    #define GENESIS_ENGINE_USE_MIDI 1
  #endif
#endif

// -----------------------------------------------------------------------------
// DAC Pre-render Support
// Only on Teensy with Audio Board capability
// -----------------------------------------------------------------------------
#ifndef GENESIS_ENGINE_DISABLE_DAC_PRERENDER
  #if defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
    #define GENESIS_ENGINE_USE_DAC_PRERENDER 1
  #endif
#endif

// -----------------------------------------------------------------------------
// Timer-based Accurate Timing
// Uses IntervalTimer on Teensy for sample-accurate playback
// -----------------------------------------------------------------------------
#if PLATFORM_HAS_INTERVAL_TIMER
  #define GENESIS_ENGINE_USE_TIMER 1
#else
  #define GENESIS_ENGINE_USE_TIMER 0
#endif

// -----------------------------------------------------------------------------
// Buffer Sizes
// Larger buffers on platforms with more RAM
// -----------------------------------------------------------------------------
#ifndef GENESIS_ENGINE_BUFFER_SIZE
  #if defined(PLATFORM_TEENSY4)
    #define GENESIS_ENGINE_BUFFER_SIZE 8192
  #elif defined(PLATFORM_TEENSY3) || defined(PLATFORM_ESP32)
    #define GENESIS_ENGINE_BUFFER_SIZE 4096
  #elif defined(PLATFORM_RP2040) || defined(PLATFORM_SAM)
    #define GENESIS_ENGINE_BUFFER_SIZE 2048
  #elif defined(PLATFORM_AVR) && defined(__AVR_ATmega2560__)
    #define GENESIS_ENGINE_BUFFER_SIZE 512
  #else
    #define GENESIS_ENGINE_BUFFER_SIZE 256
  #endif
#endif

// -----------------------------------------------------------------------------
// Maximum VGM File Size for PROGMEM
// Used by the converter tool to warn about large files
// -----------------------------------------------------------------------------
#if defined(PLATFORM_AVR)
  #if defined(__AVR_ATmega2560__)
    #define GENESIS_ENGINE_MAX_PROGMEM (256UL * 1024UL - 8192UL)  // 248KB
  #else
    #define GENESIS_ENGINE_MAX_PROGMEM (32UL * 1024UL - 4096UL)   // 28KB
  #endif
#else
  #define GENESIS_ENGINE_MAX_PROGMEM (1024UL * 1024UL)  // 1MB (arbitrary limit)
#endif

// -----------------------------------------------------------------------------
// Debug Output
// Define GENESIS_ENGINE_DEBUG to enable serial debug messages
// -----------------------------------------------------------------------------
#ifdef GENESIS_ENGINE_DEBUG
  #define GENESIS_DEBUG_PRINT(...) Serial.print(__VA_ARGS__)
  #define GENESIS_DEBUG_PRINTLN(...) Serial.println(__VA_ARGS__)
  #define GENESIS_DEBUG_PRINTF(...) Serial.printf(__VA_ARGS__)
#else
  #define GENESIS_DEBUG_PRINT(...)
  #define GENESIS_DEBUG_PRINTLN(...)
  #define GENESIS_DEBUG_PRINTF(...)
#endif

#endif // GENESIS_ENGINE_FEATURE_CONFIG_H
