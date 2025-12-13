#ifndef CHUNKED_PROGMEM_SOURCE_H
#define CHUNKED_PROGMEM_SOURCE_H

#include "VGMSource.h"
#include "../config/platform_detect.h"

// =============================================================================
// ChunkedProgmemSource - Read VGM data from multiple PROGMEM chunks
//
// Used on AVR (Mega) to work around the 32KB per-array PROGMEM limit.
// Allows storing up to 200KB+ of VGM data across multiple arrays.
// =============================================================================

class ChunkedProgmemSource : public VGMSource {
public:
  ChunkedProgmemSource()
    : chunks_(nullptr), chunkSizes_(nullptr), numChunks_(0), totalLength_(0),
      pos_(0), currentChunk_(0), posInChunk_(0), dataStartOffset_(0), isOpen_(false) {}

  // Set the chunked PROGMEM data to read from
  // chunks: PROGMEM array of pointers to chunk arrays
  // chunkSizes: PROGMEM array of chunk sizes
  // numChunks: number of chunks
  // totalLength: total size across all chunks
  void setData(const uint8_t* const* chunks, const uint16_t* chunkSizes,
               uint8_t numChunks, uint32_t totalLength) {
    chunks_ = chunks;
    chunkSizes_ = chunkSizes;
    numChunks_ = numChunks;
    totalLength_ = totalLength;
    pos_ = 0;
    currentChunk_ = 0;
    posInChunk_ = 0;
    dataStartOffset_ = 0;
  }

  // Set the data start offset (called after parsing VGM header)
  // After this, seek positions are relative to data start
  void setDataStart(uint32_t dataOffset) {
    dataStartOffset_ = dataOffset;
  }

  // -------------------------------------------------------------------------
  // VGMSource Interface
  // -------------------------------------------------------------------------

  bool open() override {
    if (chunks_ == nullptr || numChunks_ == 0) {
      return false;
    }
    pos_ = 0;
    currentChunk_ = 0;
    posInChunk_ = 0;
    isOpen_ = true;
    return true;
  }

  void close() override {
    isOpen_ = false;
    pos_ = 0;
    currentChunk_ = 0;
    posInChunk_ = 0;
  }

  bool isOpen() const override {
    return isOpen_;
  }

  int read() override {
    if (!isOpen_ || pos_ >= totalLength_) {
      return -1;
    }

    // Get current chunk pointer and size from PROGMEM
    const uint8_t* chunkPtr = (const uint8_t*)pgm_read_ptr(&chunks_[currentChunk_]);
    uint16_t chunkSize = pgm_read_word(&chunkSizes_[currentChunk_]);

    // Read byte from current position in chunk
    uint8_t value = GENESIS_READ_BYTE(chunkPtr + posInChunk_);

    // Advance position
    pos_++;
    posInChunk_++;

    // Move to next chunk if needed
    if (posInChunk_ >= chunkSize && currentChunk_ < numChunks_ - 1) {
      currentChunk_++;
      posInChunk_ = 0;
    }

    return value;
  }

  size_t read(uint8_t* buffer, size_t length) override {
    if (!isOpen_) return 0;

    size_t bytesRead = 0;
    while (bytesRead < length && pos_ < totalLength_) {
      int b = read();
      if (b < 0) break;
      buffer[bytesRead++] = (uint8_t)b;
    }
    return bytesRead;
  }

  int peek() override {
    if (!isOpen_ || pos_ >= totalLength_) {
      return -1;
    }
    const uint8_t* chunkPtr = (const uint8_t*)pgm_read_ptr(&chunks_[currentChunk_]);
    return GENESIS_READ_BYTE(chunkPtr + posInChunk_);
  }

  bool available() override {
    return isOpen_ && pos_ < totalLength_;
  }

  bool seek(uint32_t position) override {
    // If dataStartOffset_ is set, seek positions are relative to data start
    uint32_t absolutePos = dataStartOffset_ + position;

    if (absolutePos > totalLength_) {
      return false;
    }

    // Find which chunk contains this position
    uint32_t offset = 0;
    for (uint8_t i = 0; i < numChunks_; i++) {
      uint16_t chunkSize = pgm_read_word(&chunkSizes_[i]);
      if (absolutePos < offset + chunkSize) {
        currentChunk_ = i;
        posInChunk_ = absolutePos - offset;
        pos_ = absolutePos;
        return true;
      }
      offset += chunkSize;
    }

    // Position is at the very end
    if (absolutePos == totalLength_) {
      currentChunk_ = numChunks_ - 1;
      posInChunk_ = pgm_read_word(&chunkSizes_[currentChunk_]);
      pos_ = absolutePos;
      return true;
    }

    return false;
  }

  uint32_t position() const override {
    return pos_;
  }

  uint32_t size() const override {
    return totalLength_;
  }

  bool canSeek() const override {
    return true;
  }

private:
  const uint8_t* const* chunks_;  // PROGMEM array of chunk pointers
  const uint16_t* chunkSizes_;    // PROGMEM array of chunk sizes
  uint8_t numChunks_;
  uint32_t totalLength_;

  uint32_t pos_;                  // Absolute position in data
  uint8_t currentChunk_;          // Current chunk index
  uint16_t posInChunk_;           // Position within current chunk
  uint32_t dataStartOffset_;      // Offset to VGM data start (for relative seeking)
  bool isOpen_;
};

#endif // CHUNKED_PROGMEM_SOURCE_H
