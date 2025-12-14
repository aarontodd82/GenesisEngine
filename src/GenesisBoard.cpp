#include "GenesisBoard.h"
#include "config/feature_config.h"
#include <SPI.h>

#if defined(PLATFORM_ESP32)
#include "soc/gpio_struct.h"
#endif

// Use hardware SPI for shift register (much faster than bit-banging)
// Set to 0 to use software bit-banging on custom pins
// Automatically disabled on AVR when SD card support is enabled to avoid
// SPI bus conflict (shift register has no CS pin, would receive garbage
// during SD communication)
#if defined(PLATFORM_AVR) && GENESIS_ENGINE_USE_SD
  #define USE_HARDWARE_SPI 0
#else
  #define USE_HARDWARE_SPI 1
#endif

// =============================================================================
// YM2612 Register Definitions
// =============================================================================
static constexpr uint8_t YM2612_DAC_DATA   = 0x2A;  // DAC sample data register
static constexpr uint8_t YM2612_DAC_ENABLE = 0x2B;  // DAC enable (bit 7)

// =============================================================================
// Constructor
// =============================================================================
GenesisBoard::GenesisBoard(
  uint8_t pinWR_P,
  uint8_t pinWR_Y,
  uint8_t pinIC_Y,
  uint8_t pinA0_Y,
  uint8_t pinA1_Y,
  uint8_t pinSCK,
  uint8_t pinSDI
) :
  pinWR_P_(pinWR_P),
  pinWR_Y_(pinWR_Y),
  pinIC_Y_(pinIC_Y),
  pinA0_Y_(pinA0_Y),
  pinA1_Y_(pinA1_Y),
  pinSCK_(pinSCK),
  pinSDI_(pinSDI),
  lastWriteTime_(0),
  dacStreamMode_(false)
{
}

// =============================================================================
// Initialization
// =============================================================================
void GenesisBoard::begin() {
  // Configure control pins as outputs
  pinMode(pinWR_P_, OUTPUT);
  pinMode(pinWR_Y_, OUTPUT);
  pinMode(pinIC_Y_, OUTPUT);
  pinMode(pinA0_Y_, OUTPUT);
  pinMode(pinA1_Y_, OUTPUT);

  // Set initial states (active-low signals start HIGH)
  digitalWrite(pinWR_P_, HIGH);
  digitalWrite(pinWR_Y_, HIGH);
  digitalWrite(pinIC_Y_, HIGH);  // Not in reset
  digitalWrite(pinA0_Y_, LOW);
  digitalWrite(pinA1_Y_, LOW);

#if USE_HARDWARE_SPI
  // Use hardware SPI for shift register - MUCH faster
  // Mega: MOSI=51, SCK=52. Uno: MOSI=11, SCK=13
  SPI.begin();
  // 8MHz SPI clock - fast but within CD74HCT164E specs
  SPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
#else
  // Software bit-bang on custom pins
  pinMode(pinSCK_, OUTPUT);
  pinMode(pinSDI_, OUTPUT);
  digitalWrite(pinSCK_, LOW);
  digitalWrite(pinSDI_, LOW);
#endif

  // Initialize fast GPIO for control pins
  initFastGPIO();

  // Give chips time to stabilize after power-on before reset
  // Teensy boots very fast, need longer delay for YM2612/PSG to be ready
  delay(200);

  // Reset both chips
  reset();

  lastWriteTime_ = micros();
}

// =============================================================================
// Reset
// =============================================================================
void GenesisBoard::reset() {
  // Reset YM2612 (hold IC low for at least 24 clock cycles)
  digitalWrite(pinIC_Y_, LOW);
  delayMicroseconds(500);  // Extended reset pulse for reliability
  digitalWrite(pinIC_Y_, HIGH);
  delayMicroseconds(500);  // Wait for chip to stabilize

  // Silence PSG
  silencePSG();

  dacStreamMode_ = false;
  lastWriteTime_ = micros();
}

// =============================================================================
// YM2612 Functions
// =============================================================================

void GenesisBoard::writeYM2612(uint8_t port, uint8_t reg, uint8_t val) {
  // Exit DAC stream mode if active
  if (dacStreamMode_) {
    endDACStream();
  }

#if defined(PLATFORM_AVR)
  // AVR: GPIO is slow enough, no need for busy wait or time tracking
  // Set port select (A1)
  if (port) *portA1_Y_ |= maskA1_Y_; else *portA1_Y_ &= ~maskA1_Y_;

  // Address phase: A0 low
  *portA0_Y_ &= ~maskA0_Y_;
  shiftOut8(reg);
  // Pulse WR low
  *portWR_Y_ &= ~maskWR_Y_;
  *portWR_Y_ |= maskWR_Y_;

  // Data phase: A0 high
  *portA0_Y_ |= maskA0_Y_;
  shiftOut8(val);
  // Pulse WR low
  *portWR_Y_ &= ~maskWR_Y_;
  *portWR_Y_ |= maskWR_Y_;

#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  waitIfNeeded(YM_BUSY_US);
  if (port) *portSetA1_Y_ = maskA1_Y_; else *portClearA1_Y_ = maskA1_Y_;

  *portClearA0_Y_ = maskA0_Y_;
  shiftOut8(reg);
  delayMicroseconds(4);  // Data setup time before WR
  *portClearWR_Y_ = maskWR_Y_;
  delayNanoseconds(200);  // YM2612 needs minimum WR pulse width
  *portSetWR_Y_ = maskWR_Y_;

  *portSetA0_Y_ = maskA0_Y_;
  shiftOut8(val);
  delayMicroseconds(4);  // Data setup time before WR
  *portClearWR_Y_ = maskWR_Y_;
  delayNanoseconds(200);
  *portSetWR_Y_ = maskWR_Y_;
  lastWriteTime_ = micros();

#elif defined(PLATFORM_ESP32)
  waitIfNeeded(YM_BUSY_US);
  if (port) GPIO.out_w1ts = (1 << pinA1_Y_cached_); else GPIO.out_w1tc = (1 << pinA1_Y_cached_);

  GPIO.out_w1tc = (1 << pinA0_Y_cached_);
  shiftOut8(reg);
  delayMicroseconds(4);  // Data setup time before WR
  GPIO.out_w1tc = (1 << pinWR_Y_cached_);
  delayNanoseconds(200);  // YM2612 needs minimum WR pulse width
  GPIO.out_w1ts = (1 << pinWR_Y_cached_);

  GPIO.out_w1ts = (1 << pinA0_Y_cached_);
  shiftOut8(val);
  delayMicroseconds(4);  // Data setup time before WR
  GPIO.out_w1tc = (1 << pinWR_Y_cached_);
  delayNanoseconds(200);
  GPIO.out_w1ts = (1 << pinWR_Y_cached_);
  lastWriteTime_ = micros();

#else
  waitIfNeeded(YM_BUSY_US);
  digitalWrite(pinA1_Y_, port ? HIGH : LOW);
  digitalWrite(pinA0_Y_, LOW);
  shiftOut8(reg);
  pulseLow(pinWR_Y_);
  digitalWrite(pinA0_Y_, HIGH);
  shiftOut8(val);
  pulseLow(pinWR_Y_);
  lastWriteTime_ = micros();
#endif
}

void GenesisBoard::setDACEnabled(bool enabled) {
  writeYM2612(0, YM2612_DAC_ENABLE, enabled ? 0x80 : 0x00);
}

void GenesisBoard::beginDACStream() {
  if (dacStreamMode_) return;

  waitIfNeeded(YM_BUSY_US);

#if defined(PLATFORM_AVR)
  *portA1_Y_ &= ~maskA1_Y_;  // Port 0
  *portA0_Y_ &= ~maskA0_Y_;  // Address mode
  shiftOut8(YM2612_DAC_DATA);
  *portWR_Y_ &= ~maskWR_Y_;
  *portWR_Y_ |= maskWR_Y_;
  *portA0_Y_ |= maskA0_Y_;   // Data mode

#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  *portClearA1_Y_ = maskA1_Y_;
  *portClearA0_Y_ = maskA0_Y_;
  shiftOut8(YM2612_DAC_DATA);
  delayNanoseconds(100);  // Data setup time before WR
  *portClearWR_Y_ = maskWR_Y_;
  delayNanoseconds(200);  // YM2612 needs minimum WR pulse width
  *portSetWR_Y_ = maskWR_Y_;
  *portSetA0_Y_ = maskA0_Y_;

#elif defined(PLATFORM_ESP32)
  GPIO.out_w1tc = (1 << pinA1_Y_cached_);
  GPIO.out_w1tc = (1 << pinA0_Y_cached_);
  shiftOut8(YM2612_DAC_DATA);
  delayNanoseconds(100);  // Data setup time before WR
  GPIO.out_w1tc = (1 << pinWR_Y_cached_);
  delayNanoseconds(200);  // YM2612 needs minimum WR pulse width
  GPIO.out_w1ts = (1 << pinWR_Y_cached_);
  GPIO.out_w1ts = (1 << pinA0_Y_cached_);

#else
  digitalWrite(pinA1_Y_, LOW);
  digitalWrite(pinA0_Y_, LOW);
  shiftOut8(YM2612_DAC_DATA);
  pulseLow(pinWR_Y_);
  digitalWrite(pinA0_Y_, HIGH);
#endif

  dacStreamMode_ = true;
  lastWriteTime_ = micros();
}

void GenesisBoard::endDACStream() {
  if (!dacStreamMode_) return;

#if defined(PLATFORM_AVR)
  *portA0_Y_ &= ~maskA0_Y_;
#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  *portClearA0_Y_ = maskA0_Y_;
#elif defined(PLATFORM_ESP32)
  GPIO.out_w1tc = (1 << pinA0_Y_cached_);
#else
  digitalWrite(pinA0_Y_, LOW);
#endif
  dacStreamMode_ = false;
}

void GenesisBoard::writeDAC(uint8_t sample) {
  // Auto-enter streaming mode if needed
  if (!dacStreamMode_) {
    beginDACStream();
  }

#if defined(PLATFORM_AVR)
  // AVR: GPIO is slow enough, no need for busy wait or time tracking
  // In streaming mode, just shift out data and pulse WR
  shiftOut8(sample);
  *portWR_Y_ &= ~maskWR_Y_;
  *portWR_Y_ |= maskWR_Y_;

#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  waitIfNeeded(YM_BUSY_US);
  shiftOut8(sample);
  delayNanoseconds(100);  // Data setup time before WR
  *portClearWR_Y_ = maskWR_Y_;
  delayNanoseconds(200);  // YM2612 needs minimum WR pulse width
  *portSetWR_Y_ = maskWR_Y_;
  lastWriteTime_ = micros();

#elif defined(PLATFORM_ESP32)
  waitIfNeeded(YM_BUSY_US);
  shiftOut8(sample);
  delayNanoseconds(100);  // Data setup time before WR
  GPIO.out_w1tc = (1 << pinWR_Y_cached_);
  delayNanoseconds(200);  // YM2612 needs minimum WR pulse width
  GPIO.out_w1ts = (1 << pinWR_Y_cached_);
  lastWriteTime_ = micros();

#else
  waitIfNeeded(YM_BUSY_US);
  shiftOut8(sample);
  pulseLow(pinWR_Y_);
  lastWriteTime_ = micros();
#endif
}

// =============================================================================
// SN76489 Functions
// =============================================================================

void GenesisBoard::writePSG(uint8_t val) {
  // Exit DAC stream mode if active (shares shift register)
  if (dacStreamMode_) {
    endDACStream();
  }

  waitIfNeeded(PSG_BUSY_US);

  // SN76489 needs bit reversal due to board wiring (QA→D7 reversed)
  shiftOut8(reverseBits(val));

  // PSG write strobe - needs longer pulse than YM2612
  // Original: 8µs minimum pulse width
#if defined(PLATFORM_AVR)
  *portWR_P_ &= ~maskWR_P_;
  delayMicroseconds(8);  // PSG needs full pulse width
  *portWR_P_ |= maskWR_P_;
#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  *portClearWR_P_ = maskWR_P_;
  delayMicroseconds(8);  // Teensy is fast, needs real delay
  *portSetWR_P_ = maskWR_P_;
#elif defined(PLATFORM_ESP32)
  GPIO.out_w1tc = (1 << pinWR_P_cached_);
  delayMicroseconds(4);  // ESP32 needs some delay
  GPIO.out_w1ts = (1 << pinWR_P_cached_);
#else
  digitalWrite(pinWR_P_, LOW);
  delayMicroseconds(8);
  digitalWrite(pinWR_P_, HIGH);
#endif

  lastWriteTime_ = micros();
}

void GenesisBoard::silencePSG() {
  // Maximum attenuation on all 4 channels
  // Channel 0 (tone 1): 0x9F
  // Channel 1 (tone 2): 0xBF
  // Channel 2 (tone 3): 0xDF
  // Channel 3 (noise):  0xFF
  writePSG(0x9F);
  writePSG(0xBF);
  writePSG(0xDF);
  writePSG(0xFF);
}

// =============================================================================
// Utility
// =============================================================================

void GenesisBoard::muteAll() {
  silencePSG();

  // Key off all YM2612 channels
  for (uint8_t ch = 0; ch < 6; ch++) {
    writeYM2612(0, 0x28, ch);  // Key off (no operators enabled)
  }

  // Disable DAC
  setDACEnabled(false);
}

// =============================================================================
// Internal Functions
// =============================================================================

// -----------------------------------------------------------------------------
// Fast GPIO Initialization
// -----------------------------------------------------------------------------
void GenesisBoard::initFastGPIO() {
#if defined(PLATFORM_AVR)
  // Cache port addresses and bitmasks for AVR
  portSCK_ = portOutputRegister(digitalPinToPort(pinSCK_));
  portSDI_ = portOutputRegister(digitalPinToPort(pinSDI_));
  portWR_Y_ = portOutputRegister(digitalPinToPort(pinWR_Y_));
  portWR_P_ = portOutputRegister(digitalPinToPort(pinWR_P_));
  portA0_Y_ = portOutputRegister(digitalPinToPort(pinA0_Y_));
  portA1_Y_ = portOutputRegister(digitalPinToPort(pinA1_Y_));
  maskSCK_ = digitalPinToBitMask(pinSCK_);
  maskSDI_ = digitalPinToBitMask(pinSDI_);
  maskWR_Y_ = digitalPinToBitMask(pinWR_Y_);
  maskWR_P_ = digitalPinToBitMask(pinWR_P_);
  maskA0_Y_ = digitalPinToBitMask(pinA0_Y_);
  maskA1_Y_ = digitalPinToBitMask(pinA1_Y_);

#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  // Teensy: cache set/clear registers
  maskSCK_ = digitalPinToBitMask(pinSCK_);
  maskSDI_ = digitalPinToBitMask(pinSDI_);
  maskWR_Y_ = digitalPinToBitMask(pinWR_Y_);
  maskWR_P_ = digitalPinToBitMask(pinWR_P_);
  maskA0_Y_ = digitalPinToBitMask(pinA0_Y_);
  maskA1_Y_ = digitalPinToBitMask(pinA1_Y_);
  portSetSCK_ = portSetRegister(pinSCK_);
  portClearSCK_ = portClearRegister(pinSCK_);
  portSetSDI_ = portSetRegister(pinSDI_);
  portClearSDI_ = portClearRegister(pinSDI_);
  portSetWR_Y_ = portSetRegister(pinWR_Y_);
  portClearWR_Y_ = portClearRegister(pinWR_Y_);
  portSetWR_P_ = portSetRegister(pinWR_P_);
  portClearWR_P_ = portClearRegister(pinWR_P_);
  portSetA0_Y_ = portSetRegister(pinA0_Y_);
  portClearA0_Y_ = portClearRegister(pinA0_Y_);
  portSetA1_Y_ = portSetRegister(pinA1_Y_);
  portClearA1_Y_ = portClearRegister(pinA1_Y_);

#elif defined(PLATFORM_ESP32)
  // ESP32 - cache pin numbers
  pinSCK_cached_ = pinSCK_;
  pinSDI_cached_ = pinSDI_;
  pinWR_Y_cached_ = pinWR_Y_;
  pinWR_P_cached_ = pinWR_P_;
  pinA0_Y_cached_ = pinA0_Y_;
  pinA1_Y_cached_ = pinA1_Y_;

#endif
  // Other platforms use standard digitalWrite (no caching needed)
}

// -----------------------------------------------------------------------------
// Optimized Shift Out - Platform Specific
// -----------------------------------------------------------------------------
void GenesisBoard::shiftOut8(uint8_t data) {
#if USE_HARDWARE_SPI
  // Hardware SPI - blazing fast (~1µs per byte at 8MHz)
  SPI.transfer(data);

#elif defined(PLATFORM_AVR)
  // AVR: Direct port manipulation (~20x faster than digitalWrite)
  // Unrolled loop for maximum speed
  uint8_t oldSREG = SREG;
  cli();  // Disable interrupts for consistent timing

  // Bit 7
  if (data & 0x80) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;
  // Bit 6
  if (data & 0x40) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;
  // Bit 5
  if (data & 0x20) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;
  // Bit 4
  if (data & 0x10) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;
  // Bit 3
  if (data & 0x08) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;
  // Bit 2
  if (data & 0x04) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;
  // Bit 1
  if (data & 0x02) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;
  // Bit 0
  if (data & 0x01) *portSDI_ |= maskSDI_; else *portSDI_ &= ~maskSDI_;
  *portSCK_ |= maskSCK_; *portSCK_ &= ~maskSCK_;

  SREG = oldSREG;  // Restore interrupt state

#elif defined(PLATFORM_TEENSY4) || defined(PLATFORM_TEENSY3)
  // Teensy: Use set/clear registers for atomic operations
  for (uint8_t i = 0; i < 8; i++) {
    if (data & 0x80) {
      *portSetSDI_ = maskSDI_;
    } else {
      *portClearSDI_ = maskSDI_;
    }
    data <<= 1;
    *portSetSCK_ = maskSCK_;
    *portClearSCK_ = maskSCK_;
  }

#elif defined(PLATFORM_ESP32)
  // ESP32: Use GPIO matrix functions (already optimized)
  for (uint8_t i = 0; i < 8; i++) {
    GPIO.out_w1tc = (1 << pinSDI_cached_);  // Clear first
    if (data & 0x80) {
      GPIO.out_w1ts = (1 << pinSDI_cached_);  // Set if bit is 1
    }
    data <<= 1;
    GPIO.out_w1ts = (1 << pinSCK_cached_);  // Clock high
    GPIO.out_w1tc = (1 << pinSCK_cached_);  // Clock low
  }

#else
  // Fallback: Standard digitalWrite for unknown platforms
  for (uint8_t i = 0; i < 8; i++) {
    digitalWrite(pinSDI_, (data & 0x80) ? HIGH : LOW);
    data <<= 1;
    digitalWrite(pinSCK_, HIGH);
    digitalWrite(pinSCK_, LOW);
  }
#endif
}

uint8_t GenesisBoard::reverseBits(uint8_t b) {
  // Fast bit reversal using parallel swaps
  b = (b & 0xF0) >> 4 | (b & 0x0F) << 4;
  b = (b & 0xCC) >> 2 | (b & 0x33) << 2;
  b = (b & 0xAA) >> 1 | (b & 0x55) << 1;
  return b;
}

inline void GenesisBoard::waitIfNeeded(uint32_t minMicros) {
  uint32_t elapsed = micros() - lastWriteTime_;
  if (elapsed < minMicros) {
    delayMicroseconds(minMicros - elapsed);
  }
}

inline void GenesisBoard::pulseLow(uint8_t pin) {
  digitalWrite(pin, LOW);
  delayMicroseconds(1);  // Minimum pulse width
  digitalWrite(pin, HIGH);
}
