#ifndef VGZ_SOURCE_H
#define VGZ_SOURCE_H

#include "../config/feature_config.h"

// Only compile if BOTH VGZ and SD support are enabled
// VGZSource streams compressed VGZ files from SD card
#if GENESIS_ENGINE_USE_VGZ && GENESIS_ENGINE_USE_SD

#include "VGMSource.h"
#include <SD.h>
#include "../../lib/uzlib/uzlib.h"

// =============================================================================
// VGZSource - Streaming decompression of VGZ (gzipped VGM) files
// Supports looping by capturing/restoring decompressor state at loop point
// =============================================================================

class VGZSource : public VGMSource {
public:
  VGZSource();
  ~VGZSource();

  // Open a VGZ file for streaming decompression
  bool openFile(const char* path);

  // Get the filename for display
  const char* getFilename() const { return filename_; }

  // Set the loop point offset (relative to VGM data start)
  // Must be called after parsing VGM header but before reaching loop point
  void setLoopOffset(uint32_t offset) { loopOffsetInData_ = offset; }

  // Notify that we've reached the VGM data start position
  // This resets currentDataPos_ to 0 so loop offsets are relative to data start
  void setDataStart() { dataStartReached_ = true; currentDataPos_ = 0; }

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

  // Seeking only supported to loop point (via snapshot restore)
  bool seek(uint32_t position) override;
  uint32_t position() const override { return currentDataPos_; }
  uint32_t size() const override { return 0xFFFFFFFF; }  // Unknown for streaming
  bool canSeek() const override { return true; }  // Only to loop point

private:
  // Buffer sizes
  static const size_t BUFFER_SIZE = 8192;           // 8KB decompressed buffer
  static const size_t COMPRESSED_BUFFER_SIZE = 4096; // 4KB compressed input
  static const size_t DICT_SIZE = 32768;            // 32KB LZ77 dictionary

  // File state
  File file_;
  char filename_[64];
  bool isOpen_;

  // Decompression state
  uint8_t* buffer_;              // Decompressed output buffer
  uint8_t* compressedBuffer_;    // Compressed input buffer
  uint8_t* dictBuffer_;          // LZ77 sliding window dictionary
  uzlib_uncomp decompressor_;
  bool decompressorActive_;
  size_t bufferPos_;
  size_t bufferSize_;
  uint32_t currentDataPos_;      // Position in decompressed stream (relative to data start after setDataStart())
  bool dataStartReached_;        // True after setDataStart() is called

  // Loop support - snapshot of decompressor state at loop point
  struct LoopSnapshot {
    uint32_t compressedFilePos;      // Position in compressed file
    uint32_t decompressedDataPos;    // Position in decompressed stream
    uzlib_uncomp decompressorState;  // Full decompressor state
    uint8_t* dictCopy;               // Copy of LZ77 dictionary
    size_t dictSize;
    uint8_t* savedBufferData;        // Saved decompressed buffer data
    size_t savedBufferSize;
    bool valid;
  };
  LoopSnapshot loopSnapshot_;
  uint32_t loopOffsetInData_;    // Where to capture snapshot

  // Helper methods
  bool refillBuffer();
  void captureLoopSnapshot();
  bool restoreLoopSnapshot();
  void extractFilename(const char* path);

  // Static callback for uzlib streaming
  static int streamingReadCallback(uzlib_uncomp* uncomp);
};

// Global pointer for uzlib callback (uzlib doesn't support user data)
extern VGZSource* g_streamingVGZSource;

#endif // GENESIS_ENGINE_USE_VGZ && GENESIS_ENGINE_USE_SD
#endif // VGZ_SOURCE_H
