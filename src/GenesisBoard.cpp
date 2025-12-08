#include "GenesisBoard.h"

#if defined(PLATFORM_ESP32)
#include "soc/gpio_struct.h"
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
  // Configure all pins as outputs
  pinMode(pinWR_P_, OUTPUT);
  pinMode(pinWR_Y_, OUTPUT);
  pinMode(pinIC_Y_, OUTPUT);
  pinMode(pinA0_Y_, OUTPUT);
  pinMode(pinA1_Y_, OUTPUT);
  pinMode(pinSCK_, OUTPUT);
  pinMode(pinSDI_, OUTPUT);

  // Set initial states (active-low signals start HIGH)
  digitalWrite(pinWR_P_, HIGH);
  digitalWrite(pinWR_Y_, HIGH);
  digitalWrite(pinIC_Y_, HIGH);  // Not in reset
  digitalWrite(pinA0_Y_, LOW);
  digitalWrite(pinA1_Y_, LOW);
  digitalWrite(pinSCK_, LOW);
  digitalWrite(pinSDI_, LOW);

  // Initialize fast GPIO for shift register
  initFastGPIO();

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
  delayMicroseconds(100);  // ~768 cycles at 7.67MHz, plenty of margin
  digitalWrite(pinIC_Y_, HIGH);
  delayMicroseconds(100);  // Wait for chip to stabilize

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

  waitIfNeeded(YM_BUSY_US);

  // Set port select (A1)
  digitalWrite(pinA1_Y_, port ? HIGH : LOW);

  // --- Address Phase ---
  digitalWrite(pinA0_Y_, LOW);  // A0 low = address mode
  shiftOut8(reg);
  pulseLow(pinWR_Y_);

  // --- Data Phase ---
  digitalWrite(pinA0_Y_, HIGH);  // A0 high = data mode
  shiftOut8(val);
  pulseLow(pinWR_Y_);

  lastWriteTime_ = micros();
}

void GenesisBoard::setDACEnabled(bool enabled) {
  writeYM2612(0, YM2612_DAC_ENABLE, enabled ? 0x80 : 0x00);
}

void GenesisBoard::beginDACStream() {
  if (dacStreamMode_) return;

  waitIfNeeded(YM_BUSY_US);

  // Latch the DAC data register address (0x2A)
  digitalWrite(pinA1_Y_, LOW);   // Port 0
  digitalWrite(pinA0_Y_, LOW);   // Address mode
  shiftOut8(YM2612_DAC_DATA);
  pulseLow(pinWR_Y_);

  // Switch to data mode and stay there
  digitalWrite(pinA0_Y_, HIGH);

  dacStreamMode_ = true;
  lastWriteTime_ = micros();
}

void GenesisBoard::endDACStream() {
  if (!dacStreamMode_) return;

  // Return to address mode
  digitalWrite(pinA0_Y_, LOW);
  dacStreamMode_ = false;
}

void GenesisBoard::writeDAC(uint8_t sample) {
  // Auto-enter streaming mode if needed
  if (!dacStreamMode_) {
    beginDACStream();
  }

  waitIfNeeded(YM_BUSY_US);

  // In streaming mode, just shift out data and pulse WR
  // Address is already latched to 0x2A
  shiftOut8(sample);
  pulseLow(pinWR_Y_);

  lastWriteTime_ = micros();
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
  digitalWrite(pinWR_P_, LOW);
  delayMicroseconds(8);  // Minimum 8μs pulse width
  digitalWrite(pinWR_P_, HIGH);

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
  maskSCK_ = digitalPinToBitMask(pinSCK_);
  maskSDI_ = digitalPinToBitMask(pinSDI_);

#elif defined(PLATFORM_TEENSY4)
  // Teensy 4.x uses GPIO6/7/8/9 fast registers
  maskSCK_ = digitalPinToBitMask(pinSCK_);
  maskSDI_ = digitalPinToBitMask(pinSDI_);
  portSetSCK_ = portSetRegister(pinSCK_);
  portClearSCK_ = portClearRegister(pinSCK_);
  portSetSDI_ = portSetRegister(pinSDI_);
  portClearSDI_ = portClearRegister(pinSDI_);

#elif defined(PLATFORM_TEENSY3)
  // Teensy 3.x
  maskSCK_ = digitalPinToBitMask(pinSCK_);
  maskSDI_ = digitalPinToBitMask(pinSDI_);
  portSetSCK_ = portSetRegister(pinSCK_);
  portClearSCK_ = portClearRegister(pinSCK_);
  portSetSDI_ = portSetRegister(pinSDI_);
  portClearSDI_ = portClearRegister(pinSDI_);

#elif defined(PLATFORM_ESP32)
  // ESP32 - cache pin numbers (GPIO functions are already fast)
  pinSCK_cached_ = pinSCK_;
  pinSDI_cached_ = pinSDI_;

#endif
  // Other platforms use standard digitalWrite (no caching needed)
}

// -----------------------------------------------------------------------------
// Optimized Shift Out - Platform Specific
// -----------------------------------------------------------------------------
void GenesisBoard::shiftOut8(uint8_t data) {
#if defined(PLATFORM_AVR)
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
