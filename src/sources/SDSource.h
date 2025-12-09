#ifndef SD_SOURCE_H
#define SD_SOURCE_H

#include "../config/feature_config.h"

// Only compile if SD support is enabled
#if GENESIS_ENGINE_USE_SD

#include "VGMSource.h"
#include <SD.h>

// =============================================================================
// SDSource - Read VGM data from SD card file
// Works on all platforms with SD support
// =============================================================================

class SDSource : public VGMSource {
public:
  SDSource();
  ~SDSource();

  // Open a file by path
  // Returns true if file opened successfully
  bool openFile(const char* path);

  // Get the filename (without path) for display
  const char* getFilename() const { return filename_; }

  // Check if file is VGZ (compressed)
  bool isVGZ() const { return isVGZ_; }

  // Set the data start offset (called after parsing VGM header)
  // After this, seek positions are relative to data start
  void setDataStart(uint32_t dataOffset) {
    dataStartOffset_ = dataOffset;
  }

  // -------------------------------------------------------------------------
  // VGMSource Interface
  // -------------------------------------------------------------------------

  bool open() override;
  void close() override;
  bool isOpen() const override;

  int read() override;
  size_t read(uint8_t* buffer, size_t length) override;
  int peek() override;
  bool available() override;

  bool seek(uint32_t position) override;
  uint32_t position() const override;
  uint32_t size() const override;
  bool canSeek() const override { return true; }

private:
  File file_;
  // AVR: Use short buffer to save RAM (8.3 names only)
  // Other platforms: Support long filenames
#if defined(PLATFORM_AVR)
  char filename_[13];  // 8.3 filename + null
#else
  char filename_[64];  // Long filename support
#endif
  uint32_t fileSize_;
  uint32_t dataStartOffset_;  // Offset to VGM data start (for relative seeking)
  bool isOpen_;
  bool isVGZ_;

  // Extract filename from path
  void extractFilename(const char* path);
};

#endif // GENESIS_ENGINE_USE_SD
#endif // SD_SOURCE_H
