/**
 * SerialStreaming - Stream VGM data over serial with error correction
 *
 * Uses a simple chunked protocol with checksums to prevent corruption:
 *   - Python sends: [0x01][length][data...][checksum]
 *   - Arduino responds: 'A' (ACK) or 'N' (NAK)
 *   - On NAK, Python retransmits the chunk
 */

#include <GenesisBoard.h>

// =============================================================================
// Configuration
// =============================================================================

// Serial baud rate - 115200 is most reliable
// Higher rates often have corruption issues on AVR boards
#define SERIAL_BAUD 115200

// Ring buffer size
#if defined(__AVR_ATmega328P__)
  #define BUFFER_SIZE 256
#elif defined(__AVR_ATmega2560__)
  #define BUFFER_SIZE 512
#else
  #define BUFFER_SIZE 1024
#endif

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
// Protocol
// =============================================================================

#define PROTO_CHUNK       0x01  // PC -> Arduino: Chunk with checksum follows
#define PROTO_END         0x02  // PC -> Arduino: End of stream
#define PROTO_ACK         'A'   // Arduino -> PC: Chunk received OK
#define PROTO_NAK         'N'   // Arduino -> PC: Chunk corrupted, resend
#define PROTO_READY       'R'   // Arduino -> PC: Ready for more data

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

// =============================================================================
// Globals
// =============================================================================

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);

enum State { WAITING, PLAYING, STOPPED };
State state = WAITING;

bool streamEnded = false;
bool waitingForChunk = false;  // True if we've sent READY and are waiting

// Stats for debugging
uint32_t chunksReceived = 0;
uint32_t chunksCorrupted = 0;

// =============================================================================
// Setup
// =============================================================================

void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial) { }

  board.begin();


  // Signal ready for first chunk
  Serial.write(PROTO_READY);
}

// =============================================================================
// Receive a chunk with checksum verification
// Returns: true if chunk received OK, false if corrupted or timeout
// =============================================================================

bool receiveChunk() {
  // Wait for chunk header (with timeout)
  unsigned long startWait = millis();
  while (!Serial.available()) {
    if (millis() - startWait > 1000) {
      return false;  // Timeout
    }
  }

  uint8_t header = Serial.read();

  if (header == PROTO_END) {
    streamEnded = true;
    Serial.write(PROTO_ACK);
    return true;
  }

  if (header != PROTO_CHUNK) {
    // Not a chunk - might be garbage, NAK it
    Serial.write(PROTO_NAK);
    return false;
  }

  // Wait for length byte
  startWait = millis();
  while (!Serial.available()) {
    if (millis() - startWait > 100) {
      Serial.write(PROTO_NAK);
      return false;
    }
  }

  uint8_t length = Serial.read();
  if (length == 0 || length > 128) {
    Serial.write(PROTO_NAK);
    return false;
  }

  // Read data bytes and compute checksum
  uint8_t checksum = length;  // Include length in checksum
  uint8_t tempBuf[128];

  for (uint8_t i = 0; i < length; i++) {
    startWait = millis();
    while (!Serial.available()) {
      if (millis() - startWait > 100) {
        Serial.write(PROTO_NAK);
        return false;
      }
    }
    tempBuf[i] = Serial.read();
    checksum ^= tempBuf[i];
  }

  // Read checksum byte
  startWait = millis();
  while (!Serial.available()) {
    if (millis() - startWait > 100) {
      Serial.write(PROTO_NAK);
      return false;
    }
  }

  uint8_t receivedChecksum = Serial.read();

  // Verify checksum
  if (checksum != receivedChecksum) {
    chunksCorrupted++;
    Serial.write(PROTO_NAK);
    return false;
  }

  // Checksum OK - but only ACK if we have room for the whole chunk
  if (bufferFree() < length) {
    Serial.write(PROTO_NAK);
    return false;
  }

  for (uint8_t i = 0; i < length; i++) {
    bufferWrite(tempBuf[i]);
  }

  chunksReceived++;
  Serial.write(PROTO_ACK);
  return true;
}

// =============================================================================
// VGM Command Processing
// =============================================================================

uint8_t commandSize(uint8_t cmd) {
  switch (cmd) {
    case 0x50: return 2;  // PSG
    case 0x52:
    case 0x53: return 3;  // YM2612
    case 0x61: return 3;  // Wait N
    case 0x62:
    case 0x63: return 1;  // Wait frame
    case 0x66: return 1;  // End
    case 0xE0: return 5;  // PCM seek
    default:
      if (cmd >= 0x70 && cmd <= 0x7F) return 1;
      if (cmd >= 0x80 && cmd <= 0x8F) return 2;  // DAC + wait (with inlined byte)
      return 1;
  }
}

// Returns: >0 = wait samples, 0 = continue, -1 = end, -2 = need more data
int32_t processCommand() {
  if (bufferEmpty()) return -2;

  uint8_t cmd = bufferPeek();
  uint8_t needed = commandSize(cmd);

  if (bufferAvailable() < needed) return -2;

  bufferRead();  // Consume command byte

  switch (cmd) {
    case 0x50: {
      uint8_t data = bufferRead();
      board.writePSG(data);
      return 0;
    }

    case 0x52: {
      uint8_t reg = bufferRead();
      uint8_t val = bufferRead();
      board.writeYM2612(0, reg, val);
      return 0;
    }

    case 0x53: {
      uint8_t reg = bufferRead();
      uint8_t val = bufferRead();
      board.writeYM2612(1, reg, val);
      return 0;
    }

    case 0x61: {
      uint8_t lo = bufferRead();
      uint8_t hi = bufferRead();
      return lo | (hi << 8);
    }

    case 0x62: return 735;
    case 0x63: return 882;

    case 0x66:
      streamEnded = true;
      state = STOPPED;
      return -1;

    case 0x70: case 0x71: case 0x72: case 0x73:
    case 0x74: case 0x75: case 0x76: case 0x77:
    case 0x78: case 0x79: case 0x7A: case 0x7B:
    case 0x7C: case 0x7D: case 0x7E: case 0x7F:
      return (cmd & 0x0F) + 1;

    case 0x80: case 0x81: case 0x82: case 0x83:
    case 0x84: case 0x85: case 0x86: case 0x87:
    case 0x88: case 0x89: case 0x8A: case 0x8B:
    case 0x8C: case 0x8D: case 0x8E: case 0x8F: {
      uint8_t dacByte = bufferRead();
      board.writeDAC(dacByte);
      return cmd & 0x0F;
    }

    case 0xE0: {
      // PCM seek - skip 4 bytes
      bufferRead(); bufferRead(); bufferRead(); bufferRead();
      return 0;
    }

    default:
      return 0;
  }
}

// =============================================================================
// Playback - simple scheduler
// =============================================================================

uint32_t nextCommandTime = 0;  // micros() when next command can run

void updatePlayback() {
  // Not time yet? Do nothing.
  if ((int32_t)(micros() - nextCommandTime) < 0) {
    return;
  }

  // Time to run. Process ONE command.
  int32_t result = processCommand();

  if (result == -2) return;  // Need more data
  if (result == -1) return;  // End of stream

  if (result > 0) {
    // Wait command: schedule next command from NOW (fresh read)
    // micros = samples * 1000000 / 44100 = samples * 10000 / 441
    uint32_t waitUs = ((uint32_t)result * 10000UL) / 441UL;
    nextCommandTime = micros() + waitUs;
  }
  // result == 0: chip write, next command can run immediately
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
  switch (state) {
    case WAITING:
      // Try to receive chunks and buffer data
      if (Serial.available()) {
        waitingForChunk = false;  // Got data
        if (receiveChunk()) {
          // If we have enough data buffered, start playing
          if (bufferAvailable() >= 128 && !streamEnded) {
            state = PLAYING;
            nextCommandTime = micros();
          }
        }
      }
      // Request more data (only once, then wait)
      if (!waitingForChunk && !streamEnded && bufferFree() >= 128) {
        Serial.write(PROTO_READY);
        waitingForChunk = true;
      }
      break;

    case PLAYING:
      // Process playback first (timing critical)
      updatePlayback();

      // Then try to receive more data (don't touch timing!)
      if (Serial.available()) {
        waitingForChunk = false;
        receiveChunk();
      }

      // Request more data if buffer is getting low
      if (!waitingForChunk && !streamEnded && bufferAvailable() < (BUFFER_SIZE / 2)) {
        Serial.write(PROTO_READY);
        waitingForChunk = true;
      }
      break;

    case STOPPED:
      board.muteAll();

      // Reset for next song
      bufferHead = bufferTail = 0;
      streamEnded = false;
      waitingForChunk = false;
      nextCommandTime = 0;
      chunksReceived = 0;
      chunksCorrupted = 0;
      state = WAITING;
      break;
  }
}
