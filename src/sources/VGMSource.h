#ifndef VGM_SOURCE_H
#define VGM_SOURCE_H

#include <Arduino.h>

// =============================================================================
// VGMSource - Abstract base class for VGM data sources
// Allows playback from PROGMEM, SD card, serial, etc.
// =============================================================================

class VGMSource {
public:
  virtual ~VGMSource() {}

  // -------------------------------------------------------------------------
  // Core Operations
  // -------------------------------------------------------------------------

  // Open the source and prepare for reading
  // Returns true on success
  virtual bool open() = 0;

  // Close the source
  virtual void close() = 0;

  // Check if source is open
  virtual bool isOpen() const = 0;

  // -------------------------------------------------------------------------
  // Reading
  // -------------------------------------------------------------------------

  // Read a single byte
  // Returns -1 if no data available
  virtual int read() = 0;

  // Read multiple bytes into buffer
  // Returns number of bytes read
  virtual size_t read(uint8_t* buffer, size_t length) = 0;

  // Peek at next byte without consuming it
  // Returns -1 if no data available
  virtual int peek() = 0;

  // Check if more data is available
  virtual bool available() = 0;

  // -------------------------------------------------------------------------
  // Seeking (optional - not all sources support this)
  // -------------------------------------------------------------------------

  // Seek to absolute position
  // Returns true if seek succeeded
  virtual bool seek(uint32_t position) { return false; }

  // Get current position
  virtual uint32_t position() const { return 0; }

  // Get total size (0 if unknown)
  virtual uint32_t size() const { return 0; }

  // Check if source supports seeking
  virtual bool canSeek() const { return false; }

  // -------------------------------------------------------------------------
  // Utility
  // -------------------------------------------------------------------------

  // Read a little-endian 16-bit value
  uint16_t readUInt16() {
    uint8_t lo = read();
    uint8_t hi = read();
    return (uint16_t)lo | ((uint16_t)hi << 8);
  }

  // Read a little-endian 32-bit value
  uint32_t readUInt32() {
    uint8_t b0 = read();
    uint8_t b1 = read();
    uint8_t b2 = read();
    uint8_t b3 = read();
    return (uint32_t)b0 | ((uint32_t)b1 << 8) |
           ((uint32_t)b2 << 16) | ((uint32_t)b3 << 24);
  }

  // Skip bytes
  void skip(uint32_t count) {
    if (canSeek()) {
      seek(position() + count);
    } else {
      while (count-- > 0 && available()) {
        read();
      }
    }
  }
};

#endif // VGM_SOURCE_H
