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
 * Uses chunked protocol with flow control (same as SerialStreaming):
 *   - Emulator sends: [0x01][length][data...][checksum]
 *   - Arduino ACKs with 0x06 (FLOW_READY) when chunk processed
 *   - This prevents data loss from buffer overflows
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

// Board-specific settings
#if defined(__AVR_ATmega328P__)
  #define BOARD_TYPE 1  // Uno
  #define BUFFER_SIZE 512
  #define BUFFER_MASK 0x1FF
  #define CHUNK_SIZE 64
#elif defined(__AVR_ATmega2560__)
  #define BOARD_TYPE 2  // Mega
  #define BUFFER_SIZE 2048
  #define BUFFER_MASK 0x7FF
  #define CHUNK_SIZE 64
#elif defined(__IMXRT1062__)
  #define BOARD_TYPE 4  // Teensy 4.x
  #define BUFFER_SIZE 4096
  #define BUFFER_MASK 0xFFF
  #define CHUNK_SIZE 64
#elif defined(ESP32)
  #define BOARD_TYPE 5  // ESP32
  #define BUFFER_SIZE 4096
  #define BUFFER_MASK 0xFFF
  #define CHUNK_SIZE 64
#else
  #define BOARD_TYPE 3  // Other
  #define BUFFER_SIZE 2048
  #define BUFFER_MASK 0x7FF
  #define CHUNK_SIZE 64
#endif

// =============================================================================
// Protocol Constants (must match serial_bridge.c)
// =============================================================================

// Control commands
#define CMD_PING             0x00
#define CMD_ACK              0x0F

// Chip write commands (VGM-compatible)
#define CMD_PSG_WRITE        0x50  // 1 byte:  value
#define CMD_YM2612_PORT0     0x52  // 2 bytes: register, value
#define CMD_YM2612_PORT1     0x53  // 2 bytes: register, value

// Stream control
#define CMD_END_STREAM       0x66  // Reset/silence chips

// Chunk protocol
#define CHUNK_HEADER         0x01

// Flow control responses
#define FLOW_READY           0x06  // ASCII ACK - ready for more data
#define FLOW_NAK             0x15  // ASCII NAK - bad checksum, retry

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
// Ring Buffer - decouple chunk reception from command processing
// =============================================================================

volatile uint8_t ringBuffer[BUFFER_SIZE];
volatile uint16_t ringHead = 0;
volatile uint16_t ringTail = 0;

inline uint16_t ringAvailable() {
  return (ringHead - ringTail) & BUFFER_MASK;
}

inline uint16_t ringFree() {
  return BUFFER_SIZE - 1 - ringAvailable();
}

inline bool ringEmpty() {
  return ringHead == ringTail;
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

// Connection state
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
// Chunk Reception State Machine (like SerialStreaming.ino)
// =============================================================================

enum RxState { RX_IDLE, RX_HAVE_HEADER, RX_HAVE_LENGTH, RX_READING_DATA };
RxState rxState = RX_IDLE;
uint8_t rxLength = 0;
uint8_t rxCount = 0;
uint8_t rxChecksum = 0;
uint8_t rxTempBuf[CHUNK_SIZE];

// Process incoming serial data - receives chunks with flow control
void receiveData() {
  while (Serial.available() > 0) {
    uint8_t b = Serial.read();
    lastActivityTime = millis();

    switch (rxState) {
      case RX_IDLE:
        // Handle PING - reset everything
        if (b == CMD_PING) {
          board.reset();
          ringHead = ringTail = 0;
          connected = true;
          Serial.write(CMD_ACK);
          Serial.write(BOARD_TYPE);
          Serial.write(FLOW_READY);
          break;
        }

        // Handle direct END_STREAM (not in a chunk)
        if (b == CMD_END_STREAM) {
          board.reset();
          connected = false;
          Serial.write(FLOW_READY);
          break;
        }

        // Chunk header - start receiving chunk
        if (b == CHUNK_HEADER) {
          rxState = RX_HAVE_HEADER;
        }
        break;

      case RX_HAVE_HEADER:
        // This byte is the length
        if (b == 0 || b > CHUNK_SIZE) {
          // Invalid length, go back to idle
          rxState = RX_IDLE;
          break;
        }
        rxLength = b;
        rxCount = 0;
        rxChecksum = b;  // Start checksum with length
        rxState = RX_HAVE_LENGTH;
        break;

      case RX_HAVE_LENGTH: {
        // Reading data bytes
        rxTempBuf[rxCount++] = b;
        rxChecksum ^= b;

        // Bulk read remaining bytes if available
        while (rxCount < rxLength && Serial.available() > 0) {
          uint8_t d = Serial.read();
          rxTempBuf[rxCount++] = d;
          rxChecksum ^= d;
        }

        if (rxCount >= rxLength) {
          rxState = RX_READING_DATA;  // Next byte is checksum
        }
        break;
      }

      case RX_READING_DATA:
        // This byte is the checksum
        if (b == rxChecksum && ringFree() >= rxLength) {
          // Valid chunk - copy to ring buffer
          for (uint8_t i = 0; i < rxLength; i++) {
            ringWrite(rxTempBuf[i]);
          }
          // ACK the chunk immediately
          Serial.write(FLOW_READY);
        } else {
          // Bad checksum or no room - NAK
          Serial.write(FLOW_NAK);
        }
        rxState = RX_IDLE;
        break;
    }
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

// Process commands from ring buffer
void processCommands() {
  while (!ringEmpty()) {
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
        // Should be handled in receiveData, but just in case
        board.reset();
        ringHead = ringTail = 0;
        connected = true;
        Serial.write(CMD_ACK);
        Serial.write(BOARD_TYPE);
        Serial.write(FLOW_READY);
        return;

      case CMD_END_STREAM:
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
        board.writeYM2612(0, reg, value);
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

    // Check for more incoming data periodically
    receiveData();
  }
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
  // Receive chunks (with flow control)
  receiveData();

  // Process commands from ring buffer
  processCommands();

  // Check for timeout (emulator closed/paused)
  if (connected && (millis() - lastActivityTime > ACTIVITY_TIMEOUT_MS)) {
    board.reset();
    connected = false;
  }
}
