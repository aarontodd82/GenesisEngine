/**
 * SerialStreaming - High-performance VGM streaming over serial
 *
 * Binary protocol with flow control:
 *   - Python sends: [0x01][length][data...][checksum]
 *   - Arduino responds: 'A' (ACK) or 'N' (NAK)
 *   - Arduino sends 'R' (READY) when buffer has room for more
 *
 * Features:
 *   - Binary VGM commands (no text parsing)
 *   - Non-blocking timing using micros()
 *   - Ring buffer for continuous data flow
 *   - Checksum for data integrity
 */

#include <GenesisBoard.h>
#include "StreamingProtocol.h"

// =============================================================================
// Configuration - Auto-detect board capabilities
// =============================================================================

#define SERIAL_BAUD 500000

// Ring buffer size - maximize for each board
// Uno has 2KB RAM, Mega has 8KB RAM
#if defined(__AVR_ATmega328P__)
  // Uno: Use most of available RAM for buffer
  #define BUFFER_SIZE 512
  #define CHUNK_SIZE 64
  #define BUFFER_FILL_BEFORE_PLAY 384  // 75% full before starting
  #define BOARD_TYPE 1  // Uno
#elif defined(__AVR_ATmega2560__)
  // Mega: Plenty of RAM
  #define BUFFER_SIZE 2048
  #define CHUNK_SIZE 128
  #define BUFFER_FILL_BEFORE_PLAY 1536  // 75% full before starting
  #define BOARD_TYPE 2  // Mega
#else
  // Other boards (Teensy, etc)
  #define BUFFER_SIZE 4096
  #define CHUNK_SIZE 128
  #define BUFFER_FILL_BEFORE_PLAY 3072
  #define BOARD_TYPE 3  // Other
#endif

// Chunk protocol
#define CHUNK_HEADER 0x01
#define CHUNK_END    0x02

// Flow control - how many chunks can be "in flight" before needing ACK
#define CHUNKS_IN_FLIGHT 3

// =============================================================================
// Pin Configuration
// =============================================================================

const uint8_t PIN_WR_P = 2;
const uint8_t PIN_WR_Y = 3;
const uint8_t PIN_IC_Y = 4;
const uint8_t PIN_A0_Y = 5;
const uint8_t PIN_A1_Y = 6;
const uint8_t PIN_SCK  = 7;
const uint8_t PIN_SDI  = 8;

// =============================================================================
// Ring Buffer
// =============================================================================

volatile uint8_t buffer[BUFFER_SIZE];
volatile uint16_t bufferHead = 0;
volatile uint16_t bufferTail = 0;

inline uint16_t bufferAvailable() {
  int16_t diff = bufferHead - bufferTail;
  if (diff < 0) diff += BUFFER_SIZE;
  return (uint16_t)diff;
}

inline uint16_t bufferFree() {
  return BUFFER_SIZE - 1 - bufferAvailable();
}

inline bool bufferEmpty() {
  return bufferHead == bufferTail;
}

inline void bufferWrite(uint8_t b) {
  buffer[bufferHead] = b;
  bufferHead = (bufferHead + 1) % BUFFER_SIZE;
}

inline uint8_t bufferRead() {
  uint8_t b = buffer[bufferTail];
  bufferTail = (bufferTail + 1) % BUFFER_SIZE;
  return b;
}

inline uint8_t bufferPeek() {
  return buffer[bufferTail];
}

inline uint8_t bufferPeekAt(uint16_t offset) {
  return buffer[(bufferTail + offset) % BUFFER_SIZE];
}

// =============================================================================
// Globals
// =============================================================================

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);

enum State { WAITING, PLAYING, STOPPED };
State state = WAITING;

// Timing - non-blocking using micros()
uint32_t nextCommandTime = 0;

// Flow control
bool streamEnded = false;

// DPCM state
uint8_t lastDacSample = 0x80;  // Start at midpoint

// Stats
uint32_t commandsProcessed = 0;

// =============================================================================
// Setup
// =============================================================================

void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial) { }

  board.begin();

  // Signal ready for initial data
  Serial.write(FLOW_READY);
}

// =============================================================================
// Serial Data Reception - Fully Non-blocking
// =============================================================================

// Receive state machine
enum RxState { RX_IDLE, RX_HAVE_HEADER, RX_HAVE_LENGTH, RX_READING_DATA };
RxState rxState = RX_IDLE;
uint8_t rxLength = 0;
uint8_t rxCount = 0;
uint8_t rxChecksum = 0;
uint8_t rxTempBuf[CHUNK_SIZE];
uint8_t chunksReceived = 0;  // Count chunks for pipelined ACK

// Process incoming serial data - completely non-blocking
// Call this frequently from loop()
void receiveData() {
  while (Serial.available() > 0) {
    uint8_t b = Serial.read();

    switch (rxState) {
      case RX_IDLE:
        // Handle PING during WAITING state
        if (b == CMD_PING && state == WAITING) {
          Serial.write(CMD_ACK);
          break;
        }

        // End of stream marker
        if (b == CHUNK_END) {
          streamEnded = true;
          Serial.write(FLOW_READY);
          break;
        }

        // Chunk header
        if (b == CHUNK_HEADER) {
          rxState = RX_HAVE_HEADER;
        }
        break;

      case RX_HAVE_HEADER:
        // This byte is the length
        if (b == 0 || b > CHUNK_SIZE) {
          rxState = RX_IDLE;
          break;
        }
        rxLength = b;
        rxCount = 0;
        rxChecksum = b;  // Start checksum with length
        rxState = RX_HAVE_LENGTH;
        break;

      case RX_HAVE_LENGTH:
        // Reading data bytes
        rxTempBuf[rxCount++] = b;
        rxChecksum ^= b;

        if (rxCount >= rxLength) {
          rxState = RX_READING_DATA;  // Next byte is checksum
        }
        break;

      case RX_READING_DATA:
        // This byte is the checksum
        if (b == rxChecksum && bufferFree() >= rxLength) {
          // Valid chunk - copy to ring buffer
          for (uint8_t i = 0; i < rxLength; i++) {
            bufferWrite(rxTempBuf[i]);
          }
          chunksReceived++;

          // Always send READY immediately during WAITING (need to fill buffer fast)
          // During PLAYING, use pipelined ACK to reduce serial overhead
          if (state == WAITING || chunksReceived >= CHUNKS_IN_FLIGHT) {
            Serial.write(FLOW_READY);
            chunksReceived = 0;
          }
        } else {
          // Bad checksum or no room - send NAK so Python can retry
          Serial.write(FLOW_NAK);
        }

        rxState = RX_IDLE;
        break;
    }
  }

  // If we have pending ACKs and buffer space, send READY
  if (chunksReceived > 0 && bufferFree() >= CHUNK_SIZE) {
    Serial.write(FLOW_READY);
    chunksReceived = 0;
  }
}

// =============================================================================
// Command Size Lookup
// =============================================================================

uint8_t commandSize(uint8_t cmd) {
  switch (cmd) {
    case CMD_PING:
    case CMD_ACK:
      return 1;

    case CMD_PSG_WRITE:       // 0x50: 1 byte data
      return 2;

    case CMD_YM2612_WRITE_A0: // 0x52: 2 bytes (addr, val)
    case CMD_YM2612_WRITE_A1: // 0x53: 2 bytes (addr, val)
      return 3;

    case CMD_WAIT_FRAMES:     // 0x61: 2 bytes (uint16 samples)
      return 3;

    case CMD_WAIT_NTSC:       // 0x62: no args
    case CMD_WAIT_PAL:        // 0x63: no args
    case CMD_END_OF_STREAM:   // 0x66: no args
      return 1;

    case CMD_RLE_WAIT_FRAME_1: // 0xC0: 1 byte count
      return 2;

    case CMD_DPCM_BLOCK:      // 0xC1: variable length
      return 0;  // Special handling needed

    case CMD_PCM_SEEK:        // 0xE0: 4 bytes offset
      return 5;

    default:
      // Short waits 0x70-0x7F
      if (cmd >= 0x70 && cmd <= 0x7F) return 1;
      // DAC + wait 0x80-0x8F (with inlined byte)
      if (cmd >= 0x80 && cmd <= 0x8F) return 2;
      return 1;
  }
}

// =============================================================================
// Command Processing
// =============================================================================

// Returns: >0 = wait samples, 0 = continue immediately, -1 = end, -2 = need more data
int32_t processCommand() {
  if (bufferEmpty()) return -2;

  uint8_t cmd = bufferPeek();

  // Handle variable-length commands
  if (cmd == CMD_DPCM_BLOCK) {
    // Need at least 2 bytes to know length
    if (bufferAvailable() < 2) return -2;
    uint8_t len = bufferPeekAt(1);
    if (bufferAvailable() < (uint16_t)(2 + len)) return -2;

    // Consume command and length
    bufferRead();
    bufferRead();

    // Process DPCM data
    for (uint8_t i = 0; i < len; i++) {
      uint8_t packed = bufferRead();

      // High nibble: first delta (-8 to +7)
      int8_t delta1 = (int8_t)((packed >> 4) & 0x0F) - 8;
      lastDacSample += delta1;
      board.writeDAC(lastDacSample);

      // Low nibble: second delta (-8 to +7)
      int8_t delta2 = (int8_t)(packed & 0x0F) - 8;
      lastDacSample += delta2;
      board.writeDAC(lastDacSample);
    }
    return 0;
  }

  // Fixed-length commands
  uint8_t needed = commandSize(cmd);
  if (bufferAvailable() < needed) return -2;

  // Consume command byte
  bufferRead();
  commandsProcessed++;

  switch (cmd) {
    // === Chip Writes ===

    case CMD_PSG_WRITE: {
      uint8_t data = bufferRead();
      board.writePSG(data);
      return 0;
    }

    case CMD_YM2612_WRITE_A0: {
      uint8_t reg = bufferRead();
      uint8_t val = bufferRead();
      board.writeYM2612(0, reg, val);
      return 0;
    }

    case CMD_YM2612_WRITE_A1: {
      uint8_t reg = bufferRead();
      uint8_t val = bufferRead();
      board.writeYM2612(1, reg, val);
      return 0;
    }

    // === Wait Commands ===

    case CMD_WAIT_FRAMES: {
      uint8_t lo = bufferRead();
      uint8_t hi = bufferRead();
      return (uint16_t)(lo | (hi << 8));
    }

    case CMD_WAIT_NTSC:
      return FRAME_SAMPLES_NTSC;

    case CMD_WAIT_PAL:
      return FRAME_SAMPLES_PAL;

    // === RLE Compression ===

    case CMD_RLE_WAIT_FRAME_1: {
      uint8_t count = bufferRead();
      return (uint32_t)count * FRAME_SAMPLES_NTSC;
    }

    // === Stream Control ===

    case CMD_END_OF_STREAM:
      streamEnded = true;
      state = STOPPED;
      return -1;

    case CMD_PCM_SEEK: {
      // Skip 4 bytes (we don't use PCM offset, data is inlined)
      bufferRead();
      bufferRead();
      bufferRead();
      bufferRead();
      return 0;
    }

    default:
      // Short waits 0x70-0x7F
      if (cmd >= 0x70 && cmd <= 0x7F) {
        return (cmd & 0x0F) + 1;
      }

      // DAC + wait 0x80-0x8F (inlined DAC byte follows)
      if (cmd >= 0x80 && cmd <= 0x8F) {
        uint8_t dacByte = bufferRead();
        board.writeDAC(dacByte);
        return cmd & 0x0F;
      }

      // Unknown command - skip it
      return 0;
  }
}

// =============================================================================
// Playback (Non-blocking)
// =============================================================================

void updatePlayback() {
  // Check if it's time to process the next command
  uint32_t now = micros();
  if ((int32_t)(now - nextCommandTime) < 0) {
    return;  // Not time yet
  }

  // Process one command
  int32_t result = processCommand();

  if (result == -2) {
    // Need more data - will get it on next receiveData() call
    return;
  }

  if (result == -1) {
    // End of stream
    return;
  }

  if (result > 0) {
    // Wait command: schedule next command
    // Convert samples to microseconds: samples * 1000000 / 44100
    // Optimized: samples * 10000 / 441
    uint32_t waitUs = ((uint32_t)result * 10000UL) / 441UL;
    nextCommandTime = now + waitUs;
  }
  // result == 0: chip write, continue immediately (don't update nextCommandTime)
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
  // Always receive data - it's non-blocking now
  receiveData();

  switch (state) {
    case WAITING:
      // Start playing once buffer is well-filled
      if (bufferAvailable() >= BUFFER_FILL_BEFORE_PLAY && !streamEnded) {
        state = PLAYING;
        nextCommandTime = micros();
        lastDacSample = 0x80;
      }
      break;

    case PLAYING:
      // Process playback (timing critical)
      updatePlayback();

      // Check if stream ended
      if (streamEnded && bufferEmpty()) {
        state = STOPPED;
      }
      break;

    case STOPPED:
      board.muteAll();

      // Reset state for next song
      bufferHead = bufferTail = 0;
      streamEnded = false;
      rxState = RX_IDLE;
      chunksReceived = 0;
      nextCommandTime = 0;
      commandsProcessed = 0;
      lastDacSample = 0x80;
      state = WAITING;

      // Signal ready for new stream
      Serial.write(FLOW_READY);
      break;
  }
}
