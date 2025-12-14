#include "SDSource.h"

// Only compile if SD support is enabled
#if GENESIS_ENGINE_USE_SD

// =============================================================================
// Constructor / Destructor
// =============================================================================

SDSource::SDSource()
  : fileSize_(0),
    dataStartOffset_(0),
    isOpen_(false),
    isVGZ_(false)
{
  filename_[0] = '\0';
}

SDSource::~SDSource() {
  close();
}

// =============================================================================
// File Operations
// =============================================================================

bool SDSource::openFile(const char* path) {
  // Close any previously open file
  close();

  // Try to open the file
  file_ = SD.open(path, FILE_READ);
  if (!file_) {
    return false;
  }

  // Store file info
  fileSize_ = file_.size();
  extractFilename(path);

  // Check if it's a VGZ file (gzip compressed)
  // VGZ files start with gzip magic bytes 0x1F 0x8B
  if (fileSize_ >= 2) {
    uint8_t magic[2];
    file_.read(magic, 2);
    file_.seek(0);  // Reset to beginning
    isVGZ_ = (magic[0] == 0x1F && magic[1] == 0x8B);
  } else {
    isVGZ_ = false;
  }

  isOpen_ = true;
  return true;
}

void SDSource::extractFilename(const char* path) {
  // Find the last '/' or '\' in the path
  const char* lastSlash = path;
  for (const char* p = path; *p; p++) {
    if (*p == '/' || *p == '\\') {
      lastSlash = p + 1;
    }
  }

  // If no slash found, lastSlash points to the beginning
  if (lastSlash == path && *path != '/' && *path != '\\') {
    lastSlash = path;
  }

  // Copy filename (truncate to 12 chars + null)
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

bool SDSource::open() {
  // File must already be opened via openFile()
  if (!isOpen_) {
    return false;
  }

  // Seek to beginning
  file_.seek(0);
  return true;
}

void SDSource::close() {
  if (isOpen_) {
    file_.close();
    isOpen_ = false;
    fileSize_ = 0;
    dataStartOffset_ = 0;
    filename_[0] = '\0';
    isVGZ_ = false;
  }
}

bool SDSource::isOpen() const {
  return isOpen_;
}

int SDSource::read() {
  if (!isOpen_) {
    return -1;
  }
  return file_.read();
}

size_t SDSource::read(uint8_t* buffer, size_t length) {
  if (!isOpen_) {
    return 0;
  }
  return file_.read(buffer, length);
}

int SDSource::peek() {
  if (!isOpen_) {
    return -1;
  }
  return file_.peek();
}

bool SDSource::available() {
  if (!isOpen_) {
    return false;
  }
  return file_.available() > 0;
}

bool SDSource::seek(uint32_t pos) {
  if (!isOpen_) {
    return false;
  }
  // If dataStartOffset_ is set, seek positions are relative to data start
  // Convert to absolute file position
  uint32_t absolutePos = dataStartOffset_ + pos;
  return file_.seek(absolutePos);
}

uint32_t SDSource::position() const {
  if (!isOpen_) {
    return 0;
  }
  // Return position relative to data start (consistent with seek())
  uint32_t absPos = file_.position();
  if (absPos >= dataStartOffset_) {
    return absPos - dataStartOffset_;
  }
  return 0;
}

uint32_t SDSource::size() const {
  return fileSize_;
}

#endif // GENESIS_ENGINE_USE_SD
