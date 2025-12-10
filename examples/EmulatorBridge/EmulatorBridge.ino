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
#if defined(__AVR_ATmega328P__)
  #define BOARD_TYPE 1  // Uno
  #define BUFFER_SIZE 512
  #define BUFFER_MASK 0x1FF
#elif defined(__AVR_ATmega2560__)
  #define BOARD_TYPE 2  // Mega
  #define BUFFER_SIZE 2048
  #define BUFFER_MASK 0x7FF
#elif defined(__IMXRT1062__)
  #define BOARD_TYPE 4  // Teensy 4.x - 32KB buffer!
  #define BUFFER_SIZE 32768
  #define BUFFER_MASK 0x7FFF
#elif defined(ESP32)
  #define BOARD_TYPE 5  // ESP32
  #define BUFFER_SIZE 16384
  #define BUFFER_MASK 0x3FFF
#else
  #define BOARD_TYPE 3  // Other
  #define BUFFER_SIZE 4096
  #define BUFFER_MASK 0xFFF
#endif

// =============================================================================
// Protocol Constants - VGM format (must match serial_bridge.c)
// =============================================================================

#define CMD_PING             0x00
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
// We'll use integer math: micros = samples * 1000000 / 44100
#define SAMPLES_TO_MICROS(s) ((uint32_t)(s) * 1000000UL / 44100UL)

// =============================================================================
// Pin Configuration
// =============================================================================

const uint8_t PIN_WR_P = 2;
const uint8_t PIN_WR_Y = 3;
const uint8_t PIN_IC_Y = 4;
const uint8_t PIN_A0_Y = 5;
const uint8_t PIN_A1_Y = 6;

// Software SPI pins (only used if hardware SPI disabled)
const uint8_t PIN_SCK  = 7;
const uint8_t PIN_SDI  = 8;

// =============================================================================
// Ring Buffer - large buffer absorbs timing variations
// =============================================================================

volatile uint8_t ringBuffer[BUFFER_SIZE];
volatile uint32_t ringHead = 0;
volatile uint32_t ringTail = 0;

inline uint32_t ringAvailable() {
  return (ringHead - ringTail) & BUFFER_MASK;
}

inline uint32_t ringFree() {
  return BUFFER_SIZE - 1 - ringAvailable();
}

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

inline uint8_t ringPeekAt(uint32_t offset) {
  return ringBuffer[(ringTail + offset) & BUFFER_MASK];
}

// =============================================================================
// Globals
// =============================================================================

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);

bool connected = false;
uint32_t lastActivityTime = 0;

// Timing state for VGM-style wait commands
// We track accumulated wait time and execute commands when ready
uint32_t targetTime = 0;        // When we should next allow command execution (micros)
bool waitPending = false;       // True when we're waiting for targetTime

// Timeout: silence chips if no data received (emulator paused/closed)
#define ACTIVITY_TIMEOUT_MS 1000

// =============================================================================
// Setup
// =============================================================================

void setup() {
  // Initialize board FIRST - silence chips immediately
  board.begin();

  Serial.begin(SERIAL_BAUD);
  while (!Serial) { }

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
bool processOneCommand();

void receiveData() {
  // Read all available bytes directly into ring buffer
  while (Serial.available() > 0) {
    // If buffer is getting full, try to process commands first
    while (ringFree() < 64 && !ringEmpty()) {
      if (!processOneCommand()) break;  // Waiting for time
    }

    // CRITICAL: If buffer is still full, STOP reading to prevent overflow!
    // This creates backpressure on the serial port.
    if (ringFree() == 0) {
      return;  // Buffer full - stop reading until we process more
    }

    uint8_t b = Serial.read();
    lastActivityTime = millis();

    // Handle PING specially - only when NOT connected or buffer is empty
    // (0x00 can appear as data bytes, so we can't check every byte)
    if (b == CMD_PING && (!connected || ringEmpty())) {
      board.reset();
      ringHead = ringTail = 0;
      connected = true;
      targetTime = micros();    // Reset timing for new connection
      waitPending = false;      // No wait pending
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
// =============================================================================

// Get total command size including the command byte itself
// Returns 0 for unknown commands (we'll skip 1 byte)
uint8_t getCommandSize(uint8_t cmd) {
  if (cmd >= CMD_WAIT_SHORT_BASE && cmd <= 0x7F) {
    return 1;  // 0x70-0x7F: just the command byte
  }
  switch (cmd) {
    case CMD_WAIT_60:
    case CMD_WAIT_50:
    case CMD_END_STREAM:
      return 1;  // Just command byte
    case CMD_PSG_WRITE:
      return 2;  // cmd + value
    case CMD_WAIT:
      return 3;  // cmd + 2 bytes sample count
    case CMD_YM2612_PORT0:
    case CMD_YM2612_PORT1:
      return 3;  // cmd + reg + value
    default:
      return 1;  // Unknown - skip 1 byte
  }
}

// Process a single command from ring buffer (NON-BLOCKING)
// VGM format: commands are variable length, wait commands add delays
// Returns: true if command was processed, false if waiting or no data
bool processOneCommand() {
  // If we're waiting for a target time, check if it's time yet
  if (waitPending) {
    uint32_t now = micros();
    int32_t timeRemaining = (int32_t)(targetTime - now);
    if (timeRemaining > 0) {
      // Not time yet - keep waiting
      return false;
    }
    waitPending = false;
  }

  // Need at least 1 byte to peek at command
  if (ringAvailable() < 1) return false;

  uint8_t cmd = ringPeek();
  uint8_t cmdSize = getCommandSize(cmd);

  // Wait until we have the full command
  if (ringAvailable() < cmdSize) return false;

  // Consume the command byte
  ringRead();
  lastActivityTime = millis();

  // Handle wait commands - they ACCUMULATE onto targetTime (not reset!)
  // This prevents drift - timing is based on when commands SHOULD execute,
  // not when they actually do.
  if (cmd >= CMD_WAIT_SHORT_BASE && cmd <= 0x7F) {
    // Short wait: 0x70 = 1 sample, 0x7F = 16 samples
    uint8_t samples = (cmd - CMD_WAIT_SHORT_BASE) + 1;
    targetTime += SAMPLES_TO_MICROS(samples);
    // If we're behind, snap to now (don't try to catch up - that would play too fast)
    uint32_t now = micros();
    if ((int32_t)(now - targetTime) > 0) {
      targetTime = now;
    }
    waitPending = true;
    return true;  // Command processed (wait started)
  }

  switch (cmd) {
    case CMD_WAIT: {
      // 16-bit wait: little-endian sample count
      uint8_t lo = ringRead();
      uint8_t hi = ringRead();
      uint16_t samples = ((uint16_t)hi << 8) | lo;
      targetTime += SAMPLES_TO_MICROS(samples);
      // Snap to now if behind
      uint32_t now = micros();
      if ((int32_t)(now - targetTime) > 0) {
        targetTime = now;
      }
      waitPending = true;
      return true;
    }

    case CMD_WAIT_60: {
      // Wait 735 samples (1/60 sec)
      targetTime += SAMPLES_TO_MICROS(735);
      uint32_t now = micros();
      if ((int32_t)(now - targetTime) > 0) {
        targetTime = now;
      }
      waitPending = true;
      return true;
    }

    case CMD_WAIT_50: {
      // Wait 882 samples (1/50 sec)
      targetTime += SAMPLES_TO_MICROS(882);
      uint32_t now = micros();
      if ((int32_t)(now - targetTime) > 0) {
        targetTime = now;
      }
      waitPending = true;
      return true;
    }

    case CMD_END_STREAM:
      board.reset();
      connected = false;
      waitPending = false;
      return true;

    case CMD_PSG_WRITE: {
      uint8_t value = ringRead();
      board.writePSG(value);
      return true;
    }

    case CMD_YM2612_PORT0: {
      uint8_t reg = ringRead();
      uint8_t value = ringRead();
      if (reg == REG_DAC) {
        board.writeDAC(value);
      } else {
        board.writeYM2612(0, reg, value);
      }
      return true;
    }

    case CMD_YM2612_PORT1: {
      uint8_t reg = ringRead();
      uint8_t value = ringRead();
      board.writeYM2612(1, reg, value);
      return true;
    }

    default:
      // Unknown command - already consumed 1 byte, continue
      return true;
  }
}

// Process commands that are ready to execute (non-blocking)
void processCommands() {
  // Keep processing while commands are ready (returns true)
  // Stop when waiting for time or no data
  while (processOneCommand()) {
    // After each command, check for incoming data
    receiveData();
  }
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
  // Receive data into ring buffer
  receiveData();

  // Process commands from ring buffer
  processCommands();

  // Check for timeout (emulator closed/paused)
  if (connected && (millis() - lastActivityTime > ACTIVITY_TIMEOUT_MS)) {
    board.reset();
    connected = false;
  }
}
