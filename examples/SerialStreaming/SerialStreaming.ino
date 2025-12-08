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
// Configuration
// =============================================================================

#define SERIAL_BAUD 500000

// Ring buffer size (bytes)
#if defined(__AVR_ATmega328P__)
  #define BUFFER_SIZE 256
#elif defined(__AVR_ATmega2560__)
  #define BUFFER_SIZE 512
#else
  #define BUFFER_SIZE 1024
#endif

// Chunk protocol
#define CHUNK_HEADER 0x01
#define CHUNK_END    0x02
#define CHUNK_SIZE   48  // Max bytes per chunk

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
// Serial Data Reception
// =============================================================================

// Try to receive a chunk. Non-blocking - returns immediately if no complete chunk available.
// Returns: true if chunk received, false otherwise
bool receiveChunk() {
  if (Serial.available() < 2) return false;

  uint8_t header = Serial.peek();

  // Handle PING during WAITING state
  if (header == CMD_PING && state == WAITING) {
    Serial.read();  // consume it
    Serial.write(CMD_ACK);
    return false;
  }

  // End of stream marker
  if (header == CHUNK_END) {
    Serial.read();  // consume it
    streamEnded = true;
    Serial.write(FLOW_READY);  // ACK the end
    return true;
  }

  // Must be chunk header
  if (header != CHUNK_HEADER) {
    Serial.read();  // consume bad byte
    return false;
  }

  // Peek at length (don't consume header yet)
  Serial.read();  // consume header
  uint8_t length = Serial.read();

  if (length == 0 || length > CHUNK_SIZE) {
    Serial.write(FLOW_NAK);
    return false;
  }

  // Wait for complete chunk (data + checksum)
  uint8_t needed = length + 1;
  unsigned long startWait = millis();
  while (Serial.available() < needed) {
    if (millis() - startWait > 10) {
      return false;  // Timeout - will retry next loop
    }
  }

  // Check if we have room
  if (bufferFree() < length) {
    // No room - NAK and discard the data
    for (uint8_t i = 0; i <= length; i++) Serial.read();
    Serial.write(FLOW_NAK);
    return false;
  }

  // Read and verify checksum
  uint8_t checksum = length;
  uint8_t tempBuf[CHUNK_SIZE];

  for (uint8_t i = 0; i < length; i++) {
    tempBuf[i] = Serial.read();
    checksum ^= tempBuf[i];
  }

  uint8_t receivedChecksum = Serial.read();

  if (checksum != receivedChecksum) {
    Serial.write(FLOW_NAK);
    return false;
  }

  // Checksum OK - copy to ring buffer
  for (uint8_t i = 0; i < length; i++) {
    bufferWrite(tempBuf[i]);
  }

  // Send ACK, and READY if we have room for another chunk
  Serial.write(FLOW_READY);  // ACK + READY combined (just use READY as ACK)

  return true;
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

  // Handle DAC data block (CMD_DAC_DATA_BLOCK = 0x80 with length)
  if (cmd == CMD_DAC_DATA_BLOCK) {
    if (bufferAvailable() < 2) return -2;
    uint8_t len = bufferPeekAt(1);
    if (bufferAvailable() < (uint16_t)(2 + len)) return -2;

    bufferRead();  // cmd
    bufferRead();  // len

    for (uint8_t i = 0; i < len; i++) {
      board.writeDAC(bufferRead());
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
  switch (state) {
    case WAITING:
      // Try to receive chunks
      receiveChunk();

      // Start playing once we have enough data
      if (bufferAvailable() >= BUFFER_SIZE / 4 && !streamEnded) {
        state = PLAYING;
        nextCommandTime = micros();
        lastDacSample = 0x80;
      }
      break;

    case PLAYING:
      // Process playback first (timing critical)
      updatePlayback();

      // Then try to receive more data (non-blocking)
      // Only check for chunks if buffer is getting low
      if (bufferFree() >= CHUNK_SIZE && Serial.available() >= 2) {
        receiveChunk();
      }

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
      nextCommandTime = 0;
      commandsProcessed = 0;
      lastDacSample = 0x80;
      state = WAITING;

      // Signal ready for new stream
      Serial.write(FLOW_READY);
      break;
  }
}
