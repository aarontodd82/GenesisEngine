/**
 * SerialStreaming - Stream VGM files from PC to FM-90s Genesis Engine Board
 *
 * Pin Connections:
 *   Control Pins (directly to chips):
 *     Pin 2  -> WR_P  (PSG active-low write)
 *     Pin 3  -> WR_Y  (YM2612 active-low write)
 *     Pin 4  -> IC_Y  (YM2612 active-low reset)
 *     Pin 5  -> A0_Y  (YM2612 address/data select)
 *     Pin 6  -> A1_Y  (YM2612 port select)
 *
 *   Shift Register (hardware SPI pins):
 *     Board        MOSI (SDI)    SCK
 *     ----------   ----------    ---
 *     Uno          11            13
 *     Mega         51            52
 *     Teensy 4.x   11            13
 *     ESP32        23            18
 *
 * Usage:
 *   1. Upload this sketch to your board
 *   2. Run the Python streaming script:
 *        python stream_vgm.py song.vgm
 *        python stream_vgm.py song.vgm --loop
 *        python stream_vgm.py --help
 *
 * Supported boards: Arduino Uno, Arduino Mega, Teensy 4.0/4.1, ESP32
 */

#include <GenesisBoard.h>
#include "StreamingProtocol.h"

// =============================================================================
// Board Configuration (auto-detected)
// =============================================================================

#define SERIAL_BAUD 1000000

#if defined(__AVR_ATmega328P__)
  #define BUFFER_SIZE 512
  #define BUFFER_MASK 0x1FF
  #define CHUNK_SIZE 64
  #define BUFFER_FILL_BEFORE_PLAY 384
  #define CHUNKS_IN_FLIGHT 1
  #define BOARD_TYPE 1  // Uno
#elif defined(__AVR_ATmega2560__)
  #define BUFFER_SIZE 2048
  #define BUFFER_MASK 0x7FF
  #define CHUNK_SIZE 128
  #define BUFFER_FILL_BEFORE_PLAY 1536
  #define CHUNKS_IN_FLIGHT 1
  #define BOARD_TYPE 2  // Mega
#elif defined(__IMXRT1062__)
  #define BUFFER_SIZE 4096
  #define BUFFER_MASK 0xFFF
  #define CHUNK_SIZE 128
  #define BUFFER_FILL_BEFORE_PLAY 3072
  #define CHUNKS_IN_FLIGHT 1
  #define BOARD_TYPE 4  // Teensy 4.x
#elif defined(ARDUINO_ARCH_ESP32)
  #define BUFFER_SIZE 4096
  #define BUFFER_MASK 0xFFF
  #define CHUNK_SIZE 128  // Larger chunks now that serial overhead is fixed
  #define BUFFER_FILL_BEFORE_PLAY 3072
  #define CHUNKS_IN_FLIGHT 1
  #define BOARD_TYPE 5  // ESP32
#else
  #define BUFFER_SIZE 4096
  #define BUFFER_MASK 0xFFF
  #define CHUNK_SIZE 128
  #define BUFFER_FILL_BEFORE_PLAY 3072
  #define CHUNKS_IN_FLIGHT 1
  #define BOARD_TYPE 3  // Other
#endif

#define CHUNK_HEADER 0x01
#define CHUNK_END    0x02

// =============================================================================
// Pin Configuration
// =============================================================================

#if defined(ARDUINO_ARCH_ESP32)
  // ESP32: Avoid GPIO 0-3 (boot/serial), 6-11 (flash), 12/15 (boot strapping)
  const uint8_t PIN_WR_P = 16;
  const uint8_t PIN_WR_Y = 17;
  const uint8_t PIN_IC_Y = 25;
  const uint8_t PIN_A0_Y = 26;
  const uint8_t PIN_A1_Y = 27;
  const uint8_t PIN_SCK  = 18;  // Hardware SPI (fixed)
  const uint8_t PIN_SDI  = 23;  // Hardware SPI (fixed)
#else
  const uint8_t PIN_WR_P = 2;
  const uint8_t PIN_WR_Y = 3;
  const uint8_t PIN_IC_Y = 4;
  const uint8_t PIN_A0_Y = 5;
  const uint8_t PIN_A1_Y = 6;
  // Software SPI pins (only used if USE_HARDWARE_SPI is disabled)
  const uint8_t PIN_SCK  = 7;
  const uint8_t PIN_SDI  = 8;
#endif

// =============================================================================
// Ring Buffer
// =============================================================================

volatile uint8_t buffer[BUFFER_SIZE];
volatile uint16_t bufferHead = 0;
volatile uint16_t bufferTail = 0;

inline uint16_t bufferAvailable() {
  // Branchless version using bitmask (works because BUFFER_SIZE is power of 2)
  return (bufferHead - bufferTail) & BUFFER_MASK;
}

inline uint16_t bufferFree() {
  return BUFFER_SIZE - 1 - bufferAvailable();
}

inline bool bufferEmpty() {
  return bufferHead == bufferTail;
}

inline void bufferWrite(uint8_t b) {
  buffer[bufferHead] = b;
  bufferHead = (bufferHead + 1) & BUFFER_MASK;  // Fast modulo for power-of-2
}

inline uint8_t bufferRead() {
  uint8_t b = buffer[bufferTail];
  bufferTail = (bufferTail + 1) & BUFFER_MASK;  // Fast modulo for power-of-2
  return b;
}

inline uint8_t bufferPeek() {
  return buffer[bufferTail];
}

inline uint8_t bufferPeekAt(uint16_t offset) {
  return buffer[(bufferTail + offset) & BUFFER_MASK];  // Fast modulo
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

// Timeout detection
uint32_t lastDataTime = 0;
#define DISCONNECT_TIMEOUT_MS 500

// =============================================================================
// Setup
// =============================================================================

void setup() {
  // Initialize board FIRST - silence chips immediately on power-up
  board.begin();

#if defined(ARDUINO_ARCH_ESP32)
  Serial.setRxBufferSize(4096);  // Must be called before begin()
#endif
  Serial.begin(SERIAL_BAUD);
#if defined(ARDUINO_ARCH_ESP32)
  Serial.setTimeout(0);  // Non-blocking for readBytes()
#endif
#if !defined(ARDUINO_ARCH_ESP32)
  // Wait for USB serial on native USB boards (not needed on ESP32)
  while (!Serial) { }
#endif

  // Wait for serial to stabilize, then drain garbage
  delay(100);
  while (Serial.available()) {
    Serial.read();
  }

  // Wait for PING before signaling ready
  while (true) {
    if (Serial.available()) {
      uint8_t b = Serial.read();
      if (b == CMD_PING) {
        Serial.write(CMD_ACK);
        Serial.write(BOARD_TYPE);
        Serial.write(FLOW_READY);
        break;
      }
    }
#if defined(ARDUINO_ARCH_ESP32)
    yield();  // Feed watchdog on ESP32
#endif
  }
}

// =============================================================================
// Serial Data Reception - Fully Non-blocking
// =============================================================================

// Receive state machine
enum RxState { RX_IDLE, RX_HAVE_HEADER, RX_HAVE_LENGTH, RX_AWAITING_CHECKSUM };
RxState rxState = RX_IDLE;
uint8_t rxLength = 0;
uint8_t rxCount = 0;
uint8_t rxChecksum = 0;
uint8_t rxTempBuf[CHUNK_SIZE];
uint8_t chunksReceived = 0;  // Count chunks for pipelined ACK

#if defined(ARDUINO_ARCH_ESP32)
// ESP32: Bulk read buffer to reduce per-byte Serial.read() overhead
// Each Serial.read() call on ESP32 involves FreeRTOS queue operations
// which add significant overhead when called thousands of times per second
#define ESP32_SERIAL_BUF_SIZE 256
uint8_t esp32SerialBuf[ESP32_SERIAL_BUF_SIZE];
uint8_t esp32SerialBufPos = 0;
uint8_t esp32SerialBufLen = 0;

// ESP32: Disabled yield - testing if watchdog triggers
// yield() triggers a task switch which causes timing jitter
inline void esp32ThrottledYield() {
  // Intentionally empty - testing without yield
  // If watchdog resets occur, we'll need to re-enable with throttling
}

// Get next byte from ESP32 bulk buffer (refills automatically)
// Returns -1 if no data available
inline int16_t esp32GetByte() {
  if (esp32SerialBufPos >= esp32SerialBufLen) {
    // Buffer empty - try to refill
    int avail = Serial.available();
    if (avail <= 0) return -1;

    // Read up to buffer size
    esp32SerialBufLen = Serial.readBytes(esp32SerialBuf,
                          min(avail, (int)ESP32_SERIAL_BUF_SIZE));
    esp32SerialBufPos = 0;

    if (esp32SerialBufLen == 0) return -1;
  }
  return esp32SerialBuf[esp32SerialBufPos++];
}

// Check if data is available (either in buffer or serial)
inline bool esp32DataAvailable() {
  return (esp32SerialBufPos < esp32SerialBufLen) || (Serial.available() > 0);
}
#endif

// Process incoming serial data - completely non-blocking
// Call this frequently from loop()
void receiveData() {
#if defined(ARDUINO_ARCH_ESP32)
  // ESP32: Use bulk reading to reduce FreeRTOS queue overhead
  // This is critical for maintaining correct playback timing
  while (esp32DataAvailable()) {
    int16_t result = esp32GetByte();
    if (result < 0) break;
    uint8_t b = (uint8_t)result;
#else
  while (Serial.available() > 0) {
    uint8_t b = Serial.read();
#endif
    lastDataTime = millis();  // Track when we last received data

    switch (rxState) {
      case RX_IDLE:
        // Handle PING - reset everything and go back to WAITING
        // This allows reconnection after Ctrl+C or disconnect
        if (b == CMD_PING) {
          board.reset();
          bufferHead = bufferTail = 0;
          streamEnded = false;
          chunksReceived = 0;
          nextCommandTime = 0;
          state = WAITING;
#if defined(ARDUINO_ARCH_ESP32)
          esp32SerialBufPos = esp32SerialBufLen = 0;  // Clear bulk buffer
#endif
          Serial.write(CMD_ACK);
          Serial.write(BOARD_TYPE);
          Serial.write(FLOW_READY);
          break;
        }

        // End of stream marker
        if (b == CHUNK_END) {
          streamEnded = true;
          // ACK any pending chunks plus this end marker
          for (uint8_t i = 0; i <= chunksReceived; i++) {
            Serial.write(FLOW_READY);
          }
          chunksReceived = 0;
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
          Serial.write(FLOW_NAK);  // Invalid length - tell Python to retry
          rxState = RX_IDLE;
          break;
        }
        rxLength = b;
        rxCount = 0;
        rxChecksum = b;  // Start checksum with length
        rxState = RX_HAVE_LENGTH;
        break;

      case RX_HAVE_LENGTH: {
        // Reading data bytes - grab as many as available
        rxTempBuf[rxCount++] = b;
        rxChecksum ^= b;

        // Bulk read remaining bytes if available
#if defined(ARDUINO_ARCH_ESP32)
        while (rxCount < rxLength && esp32DataAvailable()) {
          int16_t result = esp32GetByte();
          if (result < 0) break;
          rxTempBuf[rxCount] = (uint8_t)result;
          rxChecksum ^= rxTempBuf[rxCount];
          rxCount++;
        }
#else
        while (rxCount < rxLength && Serial.available() > 0) {
          uint8_t d = Serial.read();
          rxTempBuf[rxCount++] = d;
          rxChecksum ^= d;
        }
#endif

        if (rxCount >= rxLength) {
          rxState = RX_AWAITING_CHECKSUM;
        }
        break;
      }

      case RX_AWAITING_CHECKSUM:
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

  // Fixed-length commands
  uint8_t needed = commandSize(cmd);
  if (bufferAvailable() < needed) return -2;

  // Consume command byte
  bufferRead();

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
  // Process commands until we hit a wait or run out of data
  // Interleave receiveData() to prevent serial buffer overflow
  uint8_t cmdCount = 0;

  while (true) {
    // Check if it's time to process the next command
    uint32_t now = micros();
    if ((int32_t)(now - nextCommandTime) < 0) {
      return;  // Not time yet
    }

    // Process one command
    int32_t result = processCommand();

    if (result == -2) {
      // Need more data
      return;
    }

    if (result == -1) {
      // End of stream
      return;
    }

    if (result > 0) {
      // Wait command: schedule next command
      // Convert samples to microseconds: samples * 1000000 / 44100 = samples * 22.676us
#if defined(__IMXRT1062__)
      // Teensy 4.x: Use accurate division (fast 32-bit math)
      uint32_t waitUs = (uint32_t)result * 1000000UL / 44100UL;
#else
      // AVR/ESP32: Use fast approximation, samples * 23 ≈ samples * 22.676 (error < 1.5%)
      uint32_t waitUs = (uint32_t)result * 23UL;
#endif

      // Smart catch-up: add wait to scheduled time, not current time
      nextCommandTime += waitUs;

      // If we're behind at all, snap to now (don't try to catch up)
      if ((int32_t)(now - nextCommandTime) > 0) {
        nextCommandTime = now;
      }

      return;  // Exit and let loop() call receiveData()
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
  // Always receive data - it's non-blocking now
  receiveData();

#if defined(ARDUINO_ARCH_ESP32)
  esp32ThrottledYield();  // Occasional yield to prevent watchdog reset
#endif

  switch (state) {
    case WAITING:
      // Start playing once buffer is well-filled
      if (bufferAvailable() >= BUFFER_FILL_BEFORE_PLAY && !streamEnded) {
        state = PLAYING;
        nextCommandTime = micros();
        lastDataTime = millis();  // Reset timeout when starting playback
      }
      break;

    case PLAYING:
      // Process playback (timing critical)
      updatePlayback();

      // Check if stream ended
      if (streamEnded && bufferEmpty()) {
        state = STOPPED;
      }

      // Check for disconnect timeout - silence chips if no data received
      if (millis() - lastDataTime > DISCONNECT_TIMEOUT_MS) {
        board.reset();  // Silence chips
        state = STOPPED;
      }
      break;

    case STOPPED:
      board.reset();  // Full hardware reset to clear any hanging notes

      // Reset state for next song
      bufferHead = bufferTail = 0;
      streamEnded = false;
      rxState = RX_IDLE;
      chunksReceived = 0;
      nextCommandTime = 0;
#if defined(ARDUINO_ARCH_ESP32)
      esp32SerialBufPos = esp32SerialBufLen = 0;  // Clear bulk buffer
#endif
      state = WAITING;

      // Signal ready for new stream
      Serial.write(FLOW_READY);
      break;
  }
}
