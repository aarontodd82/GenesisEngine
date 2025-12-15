#include "VGZSource.h"

#if GENESIS_ENGINE_USE_VGZ && GENESIS_ENGINE_USE_SD

#include <string.h>

// Global pointer for uzlib callback
VGZSource* g_streamingVGZSource = nullptr;

// =============================================================================
// Constructor / Destructor
// =============================================================================

VGZSource::VGZSource()
  : isOpen_(false)
  , buffer_(nullptr)
  , compressedBuffer_(nullptr)
  , dictBuffer_(nullptr)
  , decompressorActive_(false)
  , bufferPos_(0)
  , bufferSize_(0)
  , currentDataPos_(0)
  , dataStartReached_(false)
  , loopOffsetInData_(0)
{
  filename_[0] = '\0';
  memset(&decompressor_, 0, sizeof(decompressor_));
  memset(&loopSnapshot_, 0, sizeof(loopSnapshot_));
  loopSnapshot_.valid = false;
  loopSnapshot_.dictCopy = nullptr;
  loopSnapshot_.savedBufferData = nullptr;
}

VGZSource::~VGZSource() {
  close();
}

// =============================================================================
// File Operations
// =============================================================================

bool VGZSource::openFile(const char* path) {
  close();

  Serial.print("VGZSource: Opening ");
  Serial.println(path);

  // Set global pointer for callback
  g_streamingVGZSource = this;

  // Open file
  file_ = SD.open(path, FILE_READ);
  if (!file_) {
    Serial.println("VGZSource: Failed to open file");
    return false;
  }

  extractFilename(path);

  size_t compressedSize = file_.size();
  Serial.print("VGZSource: File size = ");
  Serial.println(compressedSize);

  if (compressedSize < 18) {
    Serial.println("VGZSource: File too small");
    file_.close();
    return false;
  }

  // Allocate buffers
  buffer_ = new uint8_t[BUFFER_SIZE];
  compressedBuffer_ = new uint8_t[COMPRESSED_BUFFER_SIZE];
  dictBuffer_ = new uint8_t[DICT_SIZE];

  if (!buffer_ || !compressedBuffer_ || !dictBuffer_) {
    Serial.println("VGZSource: Failed to allocate buffers");
    close();
    return false;
  }

  Serial.println("VGZSource: Buffers allocated");

  // Initialize decompressor with dictionary
  memset(&decompressor_, 0, sizeof(decompressor_));
  uzlib_uncompress_init(&decompressor_, dictBuffer_, DICT_SIZE);

  // Read initial compressed chunk
  size_t bytesRead = file_.read(compressedBuffer_, COMPRESSED_BUFFER_SIZE);
  Serial.print("VGZSource: Read ");
  Serial.print(bytesRead);
  Serial.println(" bytes");

  if (bytesRead < 18) {
    Serial.println("VGZSource: Initial read too small");
    close();
    return false;
  }

  // Set up source for decompressor with callback
  decompressor_.source = compressedBuffer_;
  decompressor_.source_limit = compressedBuffer_ + bytesRead;
  decompressor_.source_read_cb = streamingReadCallback;

  // Set up destination
  decompressor_.dest_start = buffer_;
  decompressor_.dest = buffer_;
  decompressor_.dest_limit = buffer_ + BUFFER_SIZE;

  // Parse gzip header
  int res = uzlib_gzip_parse_header(&decompressor_);
  if (res != TINF_OK) {
    Serial.print("VGZSource: Failed to parse gzip header, res=");
    Serial.println(res);
    close();
    return false;
  }

  Serial.println("VGZSource: Gzip header parsed OK");

  decompressorActive_ = true;

  // Decompress initial data to fill buffer
  while ((size_t)(decompressor_.dest - buffer_) < BUFFER_SIZE / 2) {
    res = uzlib_uncompress(&decompressor_);
    if (res == TINF_DONE) break;
    if (res != TINF_OK) {
      Serial.print("VGZSource: Decompression failed, res=");
      Serial.println(res);
      close();
      return false;
    }
  }

  bufferSize_ = decompressor_.dest - buffer_;
  bufferPos_ = 0;
  currentDataPos_ = 0;
  isOpen_ = true;

  Serial.print("VGZSource: Decompressed ");
  Serial.print(bufferSize_);
  Serial.println(" bytes initially");

  return true;
}

void VGZSource::extractFilename(const char* path) {
  const char* lastSlash = path;
  for (const char* p = path; *p; p++) {
    if (*p == '/' || *p == '\\') {
      lastSlash = p + 1;
    }
  }
  if (lastSlash == path && *path != '/' && *path != '\\') {
    lastSlash = path;
  }

  size_t i = 0;
  while (lastSlash[i] && i < sizeof(filename_) - 1) {
    filename_[i] = lastSlash[i];
    i++;
  }
  filename_[i] = '\0';
}

// =============================================================================
// VGMSource Interface
// =============================================================================

bool VGZSource::open() {
  // File must already be opened via openFile()
  return isOpen_;
}

void VGZSource::close() {
  // Clear global pointer
  if (g_streamingVGZSource == this) {
    g_streamingVGZSource = nullptr;
  }

  if (file_) {
    file_.close();
  }

  // Free buffers
  if (buffer_) {
    delete[] buffer_;
    buffer_ = nullptr;
  }
  if (compressedBuffer_) {
    delete[] compressedBuffer_;
    compressedBuffer_ = nullptr;
  }
  if (dictBuffer_) {
    delete[] dictBuffer_;
    dictBuffer_ = nullptr;
  }

  // Free loop snapshot
  if (loopSnapshot_.dictCopy) {
    delete[] loopSnapshot_.dictCopy;
    loopSnapshot_.dictCopy = nullptr;
  }
  if (loopSnapshot_.savedBufferData) {
    delete[] loopSnapshot_.savedBufferData;
    loopSnapshot_.savedBufferData = nullptr;
  }

  isOpen_ = false;
  decompressorActive_ = false;
  bufferPos_ = 0;
  bufferSize_ = 0;
  currentDataPos_ = 0;
  dataStartReached_ = false;
  loopSnapshot_.valid = false;
  filename_[0] = '\0';
  memset(&decompressor_, 0, sizeof(decompressor_));
}

bool VGZSource::isOpen() const {
  return isOpen_;
}

int VGZSource::read() {
  if (!isOpen_) return -1;

  // If we're reading from within the initial buffer (which includes header)
  // and our position is valid, read directly from buffer
  if (bufferPos_ < bufferSize_) {
    // Capture loop snapshot BEFORE reading at loop point
    if (loopOffsetInData_ > 0 &&
        !loopSnapshot_.valid &&
        currentDataPos_ == loopOffsetInData_) {
      captureLoopSnapshot();
    }

    currentDataPos_++;
    return buffer_[bufferPos_++];
  }

  // Refill buffer if needed
  if (!refillBuffer()) {
    return -1;
  }

  // Capture loop snapshot BEFORE reading at loop point
  if (loopOffsetInData_ > 0 &&
      !loopSnapshot_.valid &&
      currentDataPos_ == loopOffsetInData_) {
    captureLoopSnapshot();
  }

  currentDataPos_++;
  return buffer_[bufferPos_++];
}

size_t VGZSource::read(uint8_t* buf, size_t length) {
  size_t totalRead = 0;
  while (totalRead < length) {
    int b = read();
    if (b < 0) break;
    buf[totalRead++] = (uint8_t)b;
  }
  return totalRead;
}

int VGZSource::peek() {
  if (!isOpen_) return -1;

  if (bufferPos_ < bufferSize_) {
    return buffer_[bufferPos_];
  }

  if (!refillBuffer()) {
    return -1;
  }

  return buffer_[bufferPos_];
}

bool VGZSource::available() {
  if (!isOpen_) return false;

  if (bufferPos_ < bufferSize_) return true;

  // Try to refill
  return refillBuffer() && bufferSize_ > 0;
}

bool VGZSource::seek(uint32_t position) {
  if (!isOpen_) return false;

  // Seeking within current buffer (backward or forward within buffer bounds)
  if (position < bufferSize_ && currentDataPos_ <= bufferSize_) {
    // Simple case: position is within the initial buffer we still have
    currentDataPos_ = position;
    bufferPos_ = position;
    return true;
  }

  // Forward seeking by reading and discarding bytes
  // This is used when seeking to dataOffset_ which may be past the initial buffer
  // (e.g., files with large PCM data blocks in the header)
  if (position > currentDataPos_) {
    Serial.print("VGZSource: Seeking forward from ");
    Serial.print(currentDataPos_);
    Serial.print(" to ");
    Serial.println(position);

    while (currentDataPos_ < position) {
      // Read and discard bytes
      if (bufferPos_ >= bufferSize_) {
        if (!refillBuffer()) {
          Serial.println("VGZSource: Failed to refill during forward seek");
          return false;
        }
      }

      // Skip as many bytes as possible in current buffer
      uint32_t toSkip = position - currentDataPos_;
      uint32_t available = bufferSize_ - bufferPos_;
      if (toSkip > available) {
        toSkip = available;
      }

      bufferPos_ += toSkip;
      currentDataPos_ += toSkip;
    }

    return true;
  }

  // Seeking to loop point via snapshot (for looping playback)
  if (loopOffsetInData_ > 0 && position == loopOffsetInData_ && loopSnapshot_.valid) {
    return restoreLoopSnapshot();
  }

  // Cannot seek backwards to arbitrary positions in compressed stream
  Serial.print("VGZSource: Cannot seek backward to position ");
  Serial.print(position);
  Serial.print(" (current=");
  Serial.print(currentDataPos_);
  Serial.println(")");
  return false;
}

// =============================================================================
// Buffer Management
// =============================================================================

bool VGZSource::refillBuffer() {
  if (!decompressorActive_) {
    return false;
  }

  // Reset output buffer
  decompressor_.dest = buffer_;
  decompressor_.dest_limit = buffer_ + BUFFER_SIZE;

  // Decompress to fill buffer
  while (decompressor_.dest < decompressor_.dest_limit) {
    int res = uzlib_uncompress(&decompressor_);
    if (res == TINF_DONE) break;
    if (res != TINF_OK) {
      bufferSize_ = 0;
      return false;
    }
  }

  bufferSize_ = decompressor_.dest - buffer_;
  bufferPos_ = 0;

  return bufferSize_ > 0;
}

// =============================================================================
// Loop Snapshot
// =============================================================================

void VGZSource::captureLoopSnapshot() {
  if (!decompressorActive_) return;

  // Calculate compressed file position
  size_t offsetIntoBuffer = decompressor_.source - compressedBuffer_;
  uint32_t currentFilePos = file_.position();
  size_t bytesInBuffer = decompressor_.source_limit - compressedBuffer_;
  uint32_t bufferStartPos = currentFilePos - bytesInBuffer;

  loopSnapshot_.compressedFilePos = bufferStartPos + offsetIntoBuffer;
  loopSnapshot_.decompressedDataPos = currentDataPos_;

  // Copy decompressor state
  memcpy(&loopSnapshot_.decompressorState, &decompressor_, sizeof(decompressor_));

  // Copy dictionary
  if (decompressor_.dict_ring && decompressor_.dict_size > 0) {
    if (!loopSnapshot_.dictCopy) {
      loopSnapshot_.dictCopy = new uint8_t[decompressor_.dict_size];
    }
    if (loopSnapshot_.dictCopy) {
      memcpy(loopSnapshot_.dictCopy, decompressor_.dict_ring, decompressor_.dict_size);
      loopSnapshot_.dictSize = decompressor_.dict_size;
    }
  }

  // Save remaining buffer data
  size_t bytesRemaining = bufferSize_ - bufferPos_;
  if (bytesRemaining > 0) {
    if (!loopSnapshot_.savedBufferData) {
      loopSnapshot_.savedBufferData = new uint8_t[bytesRemaining];
    }
    if (loopSnapshot_.savedBufferData) {
      memcpy(loopSnapshot_.savedBufferData, buffer_ + bufferPos_, bytesRemaining);
      loopSnapshot_.savedBufferSize = bytesRemaining;
    }
  } else {
    loopSnapshot_.savedBufferSize = 0;
  }

  loopSnapshot_.valid = true;
}

bool VGZSource::restoreLoopSnapshot() {
  if (!loopSnapshot_.valid) {
    return false;
  }

  // Seek to compressed position
  if (!file_.seek(loopSnapshot_.compressedFilePos)) {
    return false;
  }

  // Read fresh compressed data
  int bytesRead = file_.read(compressedBuffer_, COMPRESSED_BUFFER_SIZE);
  if (bytesRead <= 0) {
    return false;
  }

  // Save dict pointer before restoring state
  uint8_t* savedDictPtr = decompressor_.dict_ring;

  // Restore decompressor state
  memcpy(&decompressor_, &loopSnapshot_.decompressorState, sizeof(decompressor_));

  // Fix up pointers
  decompressor_.dict_ring = savedDictPtr;
  decompressor_.dest_start = buffer_;
  decompressor_.dest = buffer_;
  decompressor_.dest_limit = buffer_ + BUFFER_SIZE;
  decompressor_.source = compressedBuffer_;
  decompressor_.source_limit = compressedBuffer_ + bytesRead;

  // Restore dictionary contents
  if (loopSnapshot_.dictCopy && loopSnapshot_.dictSize > 0 && decompressor_.dict_ring) {
    memcpy(decompressor_.dict_ring, loopSnapshot_.dictCopy, loopSnapshot_.dictSize);
    decompressor_.dict_size = loopSnapshot_.dictSize;
  }

  // Restore position
  currentDataPos_ = loopSnapshot_.decompressedDataPos;

  // Use saved buffer data if available
  if (loopSnapshot_.savedBufferData && loopSnapshot_.savedBufferSize > 0) {
    memcpy(buffer_, loopSnapshot_.savedBufferData, loopSnapshot_.savedBufferSize);
    bufferSize_ = loopSnapshot_.savedBufferSize;
    bufferPos_ = 0;
  } else {
    // Decompress fresh
    bufferPos_ = 0;
    bufferSize_ = 0;
    decompressor_.dest = buffer_;

    while (decompressor_.dest < decompressor_.dest_limit) {
      int res = uzlib_uncompress(&decompressor_);
      if (res == TINF_DONE || res != TINF_OK) break;
    }

    bufferSize_ = decompressor_.dest - buffer_;
    if (bufferSize_ == 0) {
      return false;
    }
  }

  return true;
}

// =============================================================================
// Static Callback
// =============================================================================

int VGZSource::streamingReadCallback(uzlib_uncomp* uncomp) {
  if (!g_streamingVGZSource || !g_streamingVGZSource->file_) {
    return -1;
  }

  // Check if we have buffered data
  if (uncomp->source < uncomp->source_limit) {
    return *uncomp->source++;
  }

  // Read more compressed data
  if (!g_streamingVGZSource->file_.available()) {
    return -1;
  }

  int bytesRead = g_streamingVGZSource->file_.read(
    g_streamingVGZSource->compressedBuffer_,
    COMPRESSED_BUFFER_SIZE
  );

  if (bytesRead <= 0) {
    return -1;
  }

  uncomp->source = g_streamingVGZSource->compressedBuffer_;
  uncomp->source_limit = g_streamingVGZSource->compressedBuffer_ + bytesRead;

  return *uncomp->source++;
}

#endif // GENESIS_ENGINE_USE_VGZ && GENESIS_ENGINE_USE_SD
