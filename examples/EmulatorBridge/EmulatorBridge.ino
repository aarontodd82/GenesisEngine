/**
 * EmulatorBridge - Real-time audio streaming from emulators to real hardware
 *
 * This example receives real-time register writes from a Genesis/Mega Drive
 * emulator (like BlastEm) and plays them on actual YM2612 and SN76489 chips.
 *
 * Unlike VGM streaming, there are NO timing commands - the emulator handles
 * all timing internally. Register writes arrive exactly when they should
 * be executed, so we just write them immediately to the hardware.
 *
 * Simple protocol (no chunking - relies on USB buffering):
 *   - Raw command bytes streamed directly
 *   - PING (0x00) for connection handshake
 *   - Large ring buffer absorbs timing variations
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
// Protocol Constants (must match serial_bridge.c)
// =============================================================================

#define CMD_PING             0x00
#define CMD_ACK              0x0F
#define CMD_PSG_WRITE        0x50  // 1 byte:  value
#define CMD_YM2612_PORT0     0x52  // 2 bytes: register, value
#define CMD_YM2612_PORT1     0x53  // 2 bytes: register, value
#define CMD_END_STREAM       0x66  // Reset/silence chips
#define FLOW_READY           0x06  // Ready signal for handshake

// YM2612 DAC register - use fast path for these writes
#define REG_DAC              0x2A

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

// =============================================================================
// Globals
// =============================================================================

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);

bool connected = false;
uint32_t lastActivityTime = 0;

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

// Forward declaration
void processOneCommand();

void receiveData() {
  // Read all available bytes directly into ring buffer
  while (Serial.available() > 0) {
    // If buffer is getting full, process some commands first
    // This creates backpressure - we process before accepting more
    while (ringFree() < 64 && !ringEmpty()) {
      processOneCommand();
    }

    uint8_t b = Serial.read();
    lastActivityTime = millis();

    // Handle PING specially - it's a connection request
    if (!connected && b == CMD_PING) {
      board.reset();
      ringHead = ringTail = 0;
      connected = true;
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
// Command Processing - reads from ring buffer
// =============================================================================

// Get command size based on command byte
uint8_t commandSize(uint8_t cmd) {
  switch (cmd) {
    case CMD_PING:
    case CMD_END_STREAM:
      return 1;
    case CMD_PSG_WRITE:
      return 2;  // cmd + 1 byte
    case CMD_YM2612_PORT0:
    case CMD_YM2612_PORT1:
      return 3;  // cmd + 2 bytes
    default:
      return 1;  // Unknown - skip
  }
}

// Process a single command from ring buffer
// Returns true if a command was processed, false if not enough data
void processOneCommand() {
  if (ringEmpty()) return;

  uint8_t cmd = ringPeek();
  uint8_t needed = commandSize(cmd);

  // Wait until we have the full command
  if (ringAvailable() < needed) {
    return;
  }

  // Consume command byte
  ringRead();

  switch (cmd) {
    case CMD_PING:
      // Only handle PING if not connected - ignore stray 0x00 bytes in stream
      if (!connected) {
        board.reset();
        ringHead = ringTail = 0;
        connected = true;
        Serial.write(CMD_ACK);
        Serial.write(BOARD_TYPE);
        Serial.write(FLOW_READY);
        return;
      }
      // If connected, this is likely a stray 0x00 data byte - ignore it
      break;

    case CMD_END_STREAM:
      // Only process if we receive it twice in a row (makes it unlikely to be accidental)
      // For now, just reset - the emulator will reconnect if needed
      board.reset();
      connected = false;
      break;

    case CMD_PSG_WRITE: {
      uint8_t value = ringRead();
      board.writePSG(value);
      break;
    }

    case CMD_YM2612_PORT0: {
      uint8_t reg = ringRead();
      uint8_t value = ringRead();
      // Use fast DAC path for register 0x2A
      if (reg == REG_DAC) {
        // Pace DAC writes to ~22kHz (45µs) - prevents sample loss from batched data
        static uint32_t lastDacTime = 0;
        uint32_t now = micros();
        uint32_t elapsed = now - lastDacTime;
        if (elapsed < 45) {
          delayMicroseconds(45 - elapsed);
        }
        board.writeDAC(value);
        lastDacTime = micros();
      } else {
        board.writeYM2612(0, reg, value);
      }
      break;
    }

    case CMD_YM2612_PORT1: {
      uint8_t reg = ringRead();
      uint8_t value = ringRead();
      board.writeYM2612(1, reg, value);
      break;
    }

    default:
      // Unknown command - skip
      break;
  }
}

// Process all available commands from ring buffer
void processCommands() {
  uint16_t cmdCount = 0;

  while (!ringEmpty()) {
    processOneCommand();

    // Check for more incoming data very frequently
    // USB buffer is only 1KB, can overflow in ~10ms at our data rate
    if (++cmdCount >= 8) {
      receiveData();
      cmdCount = 0;
    }
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
