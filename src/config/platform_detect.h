#ifndef GENESIS_ENGINE_PLATFORM_DETECT_H
#define GENESIS_ENGINE_PLATFORM_DETECT_H

// =============================================================================
// Platform Detection
// Automatically detects the target platform based on compiler defines
// =============================================================================

// Teensy 4.x (ARM Cortex-M7, 600MHz)
#if defined(TEENSYDUINO) && defined(__IMXRT1062__)
  #define PLATFORM_TEENSY4
  #define PLATFORM_NAME "Teensy 4.x"
  #define PLATFORM_HAS_NATIVE_USB 1
  #define PLATFORM_HAS_LARGE_RAM 1
  #define PLATFORM_RAM_KB 1024

// Teensy 3.5/3.6 (ARM Cortex-M4)
#elif defined(TEENSYDUINO) && (defined(__MK66FX1M0__) || defined(__MK64FX512__))
  #define PLATFORM_TEENSY3
  #define PLATFORM_NAME "Teensy 3.x"
  #define PLATFORM_HAS_NATIVE_USB 1
  #define PLATFORM_HAS_LARGE_RAM 1
  #define PLATFORM_RAM_KB 256

// ESP32
#elif defined(ARDUINO_ARCH_ESP32)
  #define PLATFORM_ESP32
  #define PLATFORM_NAME "ESP32"
  #define PLATFORM_HAS_NATIVE_USB 0
  #define PLATFORM_HAS_LARGE_RAM 1
  #define PLATFORM_RAM_KB 520

// RP2040 (Raspberry Pi Pico)
#elif defined(ARDUINO_ARCH_RP2040)
  #define PLATFORM_RP2040
  #define PLATFORM_NAME "RP2040"
  #define PLATFORM_HAS_NATIVE_USB 1
  #define PLATFORM_HAS_LARGE_RAM 1
  #define PLATFORM_RAM_KB 264

// Arduino AVR (Uno, Mega, etc.)
#elif defined(ARDUINO_ARCH_AVR)
  #define PLATFORM_AVR
  #define PLATFORM_NAME "Arduino AVR"
  #define PLATFORM_HAS_NATIVE_USB 0
  #define PLATFORM_HAS_LARGE_RAM 0
  #if defined(__AVR_ATmega2560__)
    #define PLATFORM_RAM_KB 8
  #else
    #define PLATFORM_RAM_KB 2
  #endif

// Arduino SAM (Due)
#elif defined(ARDUINO_ARCH_SAM)
  #define PLATFORM_SAM
  #define PLATFORM_NAME "Arduino Due"
  #define PLATFORM_HAS_NATIVE_USB 1
  #define PLATFORM_HAS_LARGE_RAM 1
  #define PLATFORM_RAM_KB 96

// Unknown platform - try to work anyway
#else
  #define PLATFORM_UNKNOWN
  #define PLATFORM_NAME "Unknown"
  #define PLATFORM_HAS_NATIVE_USB 0
  #define PLATFORM_HAS_LARGE_RAM 0
  #define PLATFORM_RAM_KB 2
  #warning "Unknown platform - using conservative defaults"
#endif

// =============================================================================
// Timing Capabilities
// =============================================================================

#if defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  #define PLATFORM_HAS_INTERVAL_TIMER 1
#else
  #define PLATFORM_HAS_INTERVAL_TIMER 0
#endif

// =============================================================================
// PROGMEM Handling
// =============================================================================

#if defined(PLATFORM_AVR)
  // AVR needs special handling for PROGMEM
  #define GENESIS_PROGMEM PROGMEM
  #define GENESIS_READ_BYTE(addr) pgm_read_byte(addr)
  #define GENESIS_READ_WORD(addr) pgm_read_word(addr)
  #define GENESIS_READ_DWORD(addr) pgm_read_dword(addr)
#else
  // Other platforms can read from flash directly
  #define GENESIS_PROGMEM
  #define GENESIS_READ_BYTE(addr) (*(const uint8_t*)(addr))
  #define GENESIS_READ_WORD(addr) (*(const uint16_t*)(addr))
  #define GENESIS_READ_DWORD(addr) (*(const uint32_t*)(addr))
#endif

#endif // GENESIS_ENGINE_PLATFORM_DETECT_H
