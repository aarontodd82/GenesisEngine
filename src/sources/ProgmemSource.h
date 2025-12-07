#ifndef PROGMEM_SOURCE_H
#define PROGMEM_SOURCE_H

#include "VGMSource.h"
#include "../config/platform_detect.h"

// =============================================================================
// ProgmemSource - Read VGM data from PROGMEM (flash memory)
// Works on all platforms, uses platform-specific PROGMEM access
// =============================================================================

class ProgmemSource : public VGMSource {
public:
  ProgmemSource() : data_(nullptr), length_(0), pos_(0), isOpen_(false) {}

  // Set the PROGMEM data to read from
  void setData(const uint8_t* data, size_t length) {
    data_ = data;
    length_ = length;
    pos_ = 0;
  }

  // -------------------------------------------------------------------------
  // VGMSource Interface
  // -------------------------------------------------------------------------

  bool open() override {
    if (data_ == nullptr || length_ == 0) {
      return false;
    }
    pos_ = 0;
    isOpen_ = true;
    return true;
  }

  void close() override {
    isOpen_ = false;
    pos_ = 0;
  }

  bool isOpen() const override {
    return isOpen_;
  }

  int read() override {
    if (!isOpen_ || pos_ >= length_) {
      return -1;
    }
    return GENESIS_READ_BYTE(data_ + pos_++);
  }

  size_t read(uint8_t* buffer, size_t length) override {
    if (!isOpen_) return 0;

    size_t toRead = length;
    if (pos_ + toRead > length_) {
      toRead = length_ - pos_;
    }

    for (size_t i = 0; i < toRead; i++) {
      buffer[i] = GENESIS_READ_BYTE(data_ + pos_++);
    }

    return toRead;
  }

  int peek() override {
    if (!isOpen_ || pos_ >= length_) {
      return -1;
    }
    return GENESIS_READ_BYTE(data_ + pos_);
  }

  bool available() override {
    return isOpen_ && pos_ < length_;
  }

  bool seek(uint32_t position) override {
    if (position > length_) {
      return false;
    }
    pos_ = position;
    return true;
  }

  uint32_t position() const override {
    return pos_;
  }

  uint32_t size() const override {
    return length_;
  }

  bool canSeek() const override {
    return true;
  }

private:
  const uint8_t* data_;
  size_t length_;
  size_t pos_;
  bool isOpen_;
};

#endif // PROGMEM_SOURCE_H
