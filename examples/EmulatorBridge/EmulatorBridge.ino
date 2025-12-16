/**
 * EmulatorBridge - Real-time audio streaming from emulators to real hardware
 *
 * This example receives real-time register writes from a Genesis/Mega Drive
 * emulator (like BlastEm) and plays them on actual YM2612 and SN76489 chips.
 *
 * Protocol with delta timing:
 *   - PING (0x00) single byte for connection handshake (before streaming)
 *   - Once connected, all commands have format: [delta_hi] [delta_lo] [cmd] [data...]
 *   - Delta is microseconds to wait BEFORE executing the command
 *   - This preserves exact timing regardless of USB batching
 *
 * Pin Connections:
 *   Control Pins (directly to chips):
 *     Pin 2  -> WR_P  (PSG active-low write)
 *     Pin 3  -> WR_Y  (YM2612 active-low write)
 *     Pin 4  -> IC_Y  (YM2612 active-low reset)
 *     Pin 5  -> A0_Y  (YM2612 address/data select)
 *     Pin 6  -> A1_Y  (YM2612 port select)
 *
 *   Shift Register (directly to board data bus):
 *     Board        MOSI (SDI)    SCK
 *     ----------   ----------    ---
 *     Uno          11            13
 *     Mega         51            52
 *     Teensy 4.x   11            13
 *     ESP32        23            18
 */

#include <GenesisBoard.h>

// =============================================================================
// Configuration
// =============================================================================

#define SERIAL_BAUD 1000000

// Board-specific settings - LARGE buffers for real-time streaming
// Note: BUFFER_FILL_BEFORE_PLAY = 0 means play immediately (like Teensy)
// This works well when DAC is disabled (lower data rate)
#if defined(__AVR_ATmega328P__)
  #define BOARD_TYPE 1  // Uno
  #define BUFFER_SIZE 512
  #define BUFFER_MASK 0x1FF
  #define BUFFER_FILL_BEFORE_PLAY 0  // Play immediately (no DAC = lower data rate)
#elif defined(__AVR_ATmega2560__)
  #define BOARD_TYPE 2  // Mega
  #define BUFFER_SIZE 2048
  #define BUFFER_MASK 0x7FF
  #define BUFFER_FILL_BEFORE_PLAY 0  // Play immediately (no DAC = lower data rate)
#elif defined(__IMXRT1062__)
  #define BOARD_TYPE 4  // Teensy 4.x - 32KB buffer!
  #define BUFFER_SIZE 32768
  #define BUFFER_MASK 0x7FFF
  #define BUFFER_FILL_BEFORE_PLAY 0
#elif defined(ARDUINO_ARCH_ESP32)
  #define BOARD_TYPE 5  // ESP32
  #define BUFFER_SIZE 16384
  #define BUFFER_MASK 0x3FFF
  #define BUFFER_FILL_BEFORE_PLAY 0
#else
  #define BOARD_TYPE 3  // Other
  #define BUFFER_SIZE 4096
  #define BUFFER_MASK 0xFFF
  #define BUFFER_FILL_BEFORE_PLAY 1024
#endif

// =============================================================================
// Protocol Constants - VGM format (must match serial_bridge.c)
// =============================================================================

#define CMD_PING             0xAA  // Chosen to not conflict with VGM commands or data
#define CMD_ACK              0x0F
#define CMD_PSG_WRITE        0x50  // 1 byte:  value
#define CMD_YM2612_PORT0     0x52  // 2 bytes: register, value
#define CMD_YM2612_PORT1     0x53  // 2 bytes: register, value
#define CMD_WAIT             0x61  // 2 bytes: sample count (little-endian, 44100 Hz)
#define CMD_WAIT_60          0x62  // Wait 735 samples (1/60 sec)
#define CMD_WAIT_50          0x63  // Wait 882 samples (1/50 sec)
#define CMD_END_STREAM       0x66  // Reset/silence chips
#define CMD_WAIT_SHORT_BASE  0x70  // 0x70-0x7F: wait 1-16 samples
#define FLOW_READY           0x06  // Ready signal for handshake

// YM2612 DAC register - use fast path for these writes
#define REG_DAC              0x2A

// Microseconds per sample at 44100 Hz (fixed-point: 22.68us ≈ 23us)
// For better accuracy: 1000000 / 44100 = 22.6757...
// AVR/ESP32: Use fast multiply approximation (division causes timing jitter on ESP32)
// Teensy: Use accurate division (fast 32-bit math)
#if defined(__AVR__) || defined(ARDUINO_ARCH_ESP32)
  #define SAMPLES_TO_MICROS(s) ((uint32_t)(s) * 23UL)
#else
  #define SAMPLES_TO_MICROS(s) ((uint32_t)(s) * 1000000UL / 44100UL)
#endif

// =============================================================================
// Pin Configuration (platform specific)
// =============================================================================

#ifdef ARDUINO_ARCH_ESP32
  const uint8_t PIN_WR_P = 16;  // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 17;  // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 25;  // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 26;  // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 27;  // A1_Y - YM2612 address bit 1 (port select)
  const uint8_t PIN_SCK  = 18;  // SCK  - Hardware SPI (fixed on ESP32)
  const uint8_t PIN_SDI  = 23;  // SDI  - Hardware SPI (fixed on ESP32)
#else
  // Teensy / Arduino defaults
  const uint8_t PIN_WR_P = 2;   // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 3;   // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 4;   // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 5;   // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 6;   // A1_Y - YM2612 address bit 1 (port select)
  // Software SPI pins (only used if hardware SPI disabled)
  const uint8_t PIN_SCK  = 7;   // SCK  - Shift register clock
  const uint8_t PIN_SDI  = 8;   // SDI  - Shift register data
#endif

// =============================================================================
// Ring Buffer - large buffer absorbs timing variations
// =============================================================================

volatile uint8_t ringBuffer[BUFFER_SIZE];

// AVR: Use 16-bit indices (faster, atomic operations, 2KB max buffer anyway)
// Teensy: Use 32-bit indices (32KB buffer)
#if defined(__AVR__)
  volatile uint16_t ringHead = 0;
  volatile uint16_t ringTail = 0;
  inline uint16_t ringAvailable() {
    return (ringHead - ringTail) & BUFFER_MASK;
  }
  inline uint16_t ringFree() {
    return BUFFER_SIZE - 1 - ringAvailable();
  }
#else
  volatile uint32_t ringHead = 0;
  volatile uint32_t ringTail = 0;
  inline uint32_t ringAvailable() {
    return (ringHead - ringTail) & BUFFER_MASK;
  }
  inline uint32_t ringFree() {
    return BUFFER_SIZE - 1 - ringAvailable();
  }
#endif

inline bool ringEmpty() {
  return ringHead == ringTail;
}

inline bool ringFull() {
  return ringAvailable() >= (BUFFER_SIZE - 1);
}

inline void ringWrite(uint8_t b) {
  ringBuffer[ringHead] = b;
  ringHead = (ringHead + 1) & BUFFER_MASK;
}

inline uint8_t ringRead() {
  uint8_t b = ringBuffer[ringTail];
  ringTail = (ringTail + 1) & BUFFER_MASK;
  return b;
}

inline uint8_t ringPeek() {
  return ringBuffer[ringTail];
}

inline uint8_t ringPeekAt(uint16_t offset) {
  return ringBuffer[(ringTail + offset) & BUFFER_MASK];
}

// =============================================================================
// Globals
// =============================================================================

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);

// State machine (AVR needs pre-buffering, Teensy doesn't)
#if BUFFER_FILL_BEFORE_PLAY > 0
  enum State { WAITING, BUFFERING, PLAYING };
  State state = WAITING;
#endif

bool connected = false;
uint32_t lastActivityTime = 0;

// Timing - non-blocking using micros() (same approach as SerialStreaming)
uint32_t nextCommandTime = 0;

// Timeout: silence chips if no data received (emulator paused/closed)
#define ACTIVITY_TIMEOUT_MS 1000

// =============================================================================
// Setup
// =============================================================================

void setup() {
  // Initialize board FIRST - silence chips immediately
  board.begin();

#if defined(ARDUINO_ARCH_ESP32)
  Serial.setRxBufferSize(4096);  // Must be called before begin()
#endif
  Serial.begin(SERIAL_BAUD);
#if defined(ARDUINO_ARCH_ESP32)
  Serial.setTimeout(0);  // Non-blocking for readBytes()
#endif
#if !defined(ARDUINO_ARCH_ESP32)
  while (!Serial) { }
#endif

  // Brief delay for serial to stabilize, then drain any garbage
  delay(100);
  while (Serial.available()) {
    Serial.read();
  }

  lastActivityTime = millis();
}

// =============================================================================
// Data Reception - With backpressure handling
// =============================================================================

// Forward declarations
uint8_t getCommandSize(uint8_t cmd);
int32_t processCommand();

#if defined(ARDUINO_ARCH_ESP32)
// ESP32: Bulk read buffer to reduce per-byte Serial.read() overhead
#define ESP32_SERIAL_BUF_SIZE 256
uint8_t esp32SerialBuf[ESP32_SERIAL_BUF_SIZE];
uint8_t esp32SerialBufPos = 0;
uint8_t esp32SerialBufLen = 0;

inline int16_t esp32GetByte() {
  if (esp32SerialBufPos >= esp32SerialBufLen) {
    int avail = Serial.available();
    if (avail <= 0) return -1;
    esp32SerialBufLen = Serial.readBytes(esp32SerialBuf,
                          min(avail, (int)ESP32_SERIAL_BUF_SIZE));
    esp32SerialBufPos = 0;
    if (esp32SerialBufLen == 0) return -1;
  }
  return esp32SerialBuf[esp32SerialBufPos++];
}

inline bool esp32DataAvailable() {
  return (esp32SerialBufPos < esp32SerialBufLen) || (Serial.available() > 0);
}
#endif

void receiveData() {
  // Read all available bytes directly into ring buffer
#if defined(ARDUINO_ARCH_ESP32)
  while (esp32DataAvailable()) {
#else
  while (Serial.available() > 0) {
#endif
#if BUFFER_FILL_BEFORE_PLAY == 0
    // Teensy: If buffer is getting full, try to process commands first
    while (ringFree() < 64 && !ringEmpty()) {
      // Check timing first
      uint32_t now = micros();
      if ((int32_t)(now - nextCommandTime) < 0) break;

      int32_t result = processCommand();
      if (result < 0) break;  // End or need data
      if (result > 0) {
        // Wait command - update timing
        nextCommandTime += SAMPLES_TO_MICROS(result);
        if ((int32_t)(now - nextCommandTime) > 0) nextCommandTime = now;
        break;
      }
    }
#endif

    // CRITICAL: If buffer is still full, STOP reading to prevent overflow!
    // This creates backpressure on the serial port.
    if (ringFree() == 0) {
      return;  // Buffer full - stop reading until we process more
    }

#if defined(ARDUINO_ARCH_ESP32)
    int16_t result = esp32GetByte();
    if (result < 0) break;
    uint8_t b = (uint8_t)result;
#else
    uint8_t b = Serial.read();
#endif
    lastActivityTime = millis();

    // Handle PING - 0xAA is not a valid VGM command, so safe to check anytime
    // Only respond when not connected (prevents accidental reconnect mid-stream)
    if (b == CMD_PING && !connected) {
      board.reset();
      ringHead = ringTail = 0;
#if defined(ARDUINO_ARCH_ESP32)
      esp32SerialBufPos = esp32SerialBufLen = 0;  // Clear bulk buffer
#endif
      connected = true;
      nextCommandTime = 0;      // Will be set when playback starts
#if BUFFER_FILL_BEFORE_PLAY > 0
      state = BUFFERING;        // AVR: wait for buffer to fill before playing
#endif
      Serial.write(CMD_ACK);
      Serial.write(BOARD_TYPE);
      Serial.write(FLOW_READY);
      continue;
    }

    // Buffer the byte for processing
    ringWrite(b);
  }
}

// =============================================================================
// Command Processing - VGM format from ring buffer
// Structured like SerialStreaming for proven AVR compatibility
// =============================================================================

// Get total command size including the command byte itself
uint8_t getCommandSize(uint8_t cmd) {
  if (cmd >= CMD_WAIT_SHORT_BASE && cmd <= 0x7F) {
    return 1;  // 0x70-0x7F: just the command byte
  }
  switch (cmd) {
    case CMD_WAIT_60:
    case CMD_WAIT_50:
    case CMD_END_STREAM:
      return 1;
    case CMD_PSG_WRITE:
      return 2;
    case CMD_WAIT:
    case CMD_YM2612_PORT0:
    case CMD_YM2612_PORT1:
      return 3;
    default:
      return 1;
  }
}

// Process one command - returns wait samples (>0), 0 (continue), -1 (end), -2 (need data)
int32_t processCommand() {
  if (ringEmpty()) return -2;

  uint8_t cmd = ringPeek();
  uint8_t needed = getCommandSize(cmd);
  if (ringAvailable() < needed) return -2;

  // Consume command byte
  ringRead();

  // Short waits 0x70-0x7F
  if (cmd >= CMD_WAIT_SHORT_BASE && cmd <= 0x7F) {
    return (cmd & 0x0F) + 1;
  }

  switch (cmd) {
    case CMD_PSG_WRITE: {
      uint8_t value = ringRead();
      board.writePSG(value);
      return 0;
    }

    case CMD_YM2612_PORT0: {
      uint8_t reg = ringRead();
      uint8_t value = ringRead();
      if (reg == REG_DAC) {
        board.writeDAC(value);
      } else {
        board.writeYM2612(0, reg, value);
      }
      return 0;
    }

    case CMD_YM2612_PORT1: {
      uint8_t reg = ringRead();
      uint8_t value = ringRead();
      board.writeYM2612(1, reg, value);
      return 0;
    }

    case CMD_WAIT: {
      uint8_t lo = ringRead();
      uint8_t hi = ringRead();
      return (uint16_t)(lo | (hi << 8));
    }

    case CMD_WAIT_60:
      return 735;

    case CMD_WAIT_50:
      return 882;

    case CMD_END_STREAM:
      board.reset();
      nextCommandTime = micros();
      return -1;

    default:
      return 0;
  }
}

// Process commands until we hit a wait or run out of data
// Matches SerialStreaming's updatePlayback() structure exactly
void updatePlayback() {
  uint8_t cmdCount = 0;

  while (true) {
    // Check timing FIRST - if not time yet, exit immediately
    uint32_t now = micros();
    if ((int32_t)(now - nextCommandTime) < 0) {
      return;  // Not time yet
    }

    // Process one command
    int32_t result = processCommand();

    if (result == -2) {
      return;  // Need more data
    }

    if (result == -1) {
      return;  // End of stream
    }

    if (result > 0) {
      // Wait command - schedule next command time
      uint32_t waitUs = SAMPLES_TO_MICROS(result);
      nextCommandTime += waitUs;

      // If behind, snap to now (don't try to catch up)
      if ((int32_t)(now - nextCommandTime) > 0) {
        nextCommandTime = now;
      }

      return;  // Exit to let main loop receive more data
    }

    // result == 0: chip write, continue processing
    // Check serial periodically to prevent overflow
    if (++cmdCount >= 16) {
      receiveData();
      cmdCount = 0;
    }
  }
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
  // Detect USB disconnect - reset state so next connection works
  // Note: Serial.dtr() only available on Teensy with native USB
#if defined(__IMXRT1062__) || defined(CORE_TEENSY)
  if (connected && !Serial.dtr()) {
    board.reset();
    ringHead = ringTail = 0;
    connected = false;
  }
#endif

  // Receive data into ring buffer
  receiveData();

#if BUFFER_FILL_BEFORE_PLAY > 0
  // AVR: State machine with pre-buffering
  switch (state) {
    case WAITING:
      // Just receive data, waiting for connection
      break;

    case BUFFERING:
      // Wait for buffer to fill before starting playback
      if (ringAvailable() >= BUFFER_FILL_BEFORE_PLAY) {
        state = PLAYING;
        nextCommandTime = micros();  // Start timing fresh
        lastActivityTime = millis();
      }
      break;

    case PLAYING:
      // Process playback (timing critical)
      updatePlayback();
      break;
  }
#else
  // Teensy: Process immediately (fast enough)
  updatePlayback();
#endif
}
