#include "PCMDataBank.h"
#include "config/feature_config.h"

// =============================================================================
// Free Memory Detection (cross-platform)
// =============================================================================

#ifdef __arm__
// ARM platforms (Teensy, Due, etc.)
extern "C" char* sbrk(int incr);
#else
// AVR platforms
extern char *__brkval;
extern char *__malloc_heap_start;
#endif

// =============================================================================
// PSRAM Support (Teensy 4.1)
// =============================================================================

#if defined(PLATFORM_TEENSY4) && !defined(PCM_DISABLE_PSRAM)
extern "C" void* extmem_malloc(size_t size);
extern "C" void extmem_free(void* ptr);
#define PCM_USE_PSRAM 1
#endif

int PCMDataBank::getFreeMemory() {
  char top;
#ifdef __arm__
  return &top - reinterpret_cast<char*>(sbrk(0));
#elif defined(CORE_TEENSY) || (ARDUINO > 103 && ARDUINO != 151)
  return &top - __brkval;
#else
  return __brkval ? &top - __brkval : &top - __malloc_heap_start;
#endif
}

// =============================================================================
// Constructor / Destructor
// =============================================================================

PCMDataBank::PCMDataBank()
  : dataBank_(nullptr)
  , allocatedSize_(0)
  , dataSize_(0)
  , originalSize_(0)
  , position_(0)
  , downsampleRatio_(1)
  , readCount_(0)
  , usingPSRAM_(false)
  , dacDisabled_(false)
{
}

PCMDataBank::~PCMDataBank() {
  clear();
}

// =============================================================================
// Memory Allocation
// =============================================================================

uint8_t* PCMDataBank::tryAllocate(uint32_t size, bool& isPSRAM) {
  isPSRAM = false;

  // Try PSRAM first (Teensy 4.1 with PSRAM chip)
#if defined(PCM_USE_PSRAM)
  // extmem_malloc returns nullptr if no PSRAM installed
  uint8_t* ptr = (uint8_t*)extmem_malloc(size);
  if (ptr) {
    // Verify PSRAM is working by writing and reading a test value
    ptr[0] = 0xAA;
    ptr[size - 1] = 0x55;
    if (ptr[0] == 0xAA && ptr[size - 1] == 0x55) {
      isPSRAM = true;
      return ptr;
    }
    // PSRAM not working, free and try RAM
    extmem_free(ptr);
  }
#endif

  // Fall back to regular RAM
  // Leave some headroom for stack and other allocations
  int freeRam = getFreeMemory();

#if defined(PCM_SIMULATE_MAX_RAM)
  // Simulate limited RAM for testing
  Serial.print("PCM: [TEST MODE] Simulating max RAM: ");
  Serial.print(PCM_SIMULATE_MAX_RAM);
  Serial.print(" bytes (actual free: ");
  Serial.print(freeRam);
  Serial.println(")");
  freeRam = PCM_SIMULATE_MAX_RAM;
#endif

  int safeSize = freeRam - 1024;  // Keep 1KB for stack/other allocations

  if ((int)size > safeSize || safeSize < 0) {
    // Can't allocate this much
    return nullptr;
  }

#if defined(ARDUINO_ARCH_AVR)
  // AVR doesn't have std::nothrow, use malloc instead
  return (uint8_t*)malloc(size);
#else
  return new (std::nothrow) uint8_t[size];
#endif
}

// =============================================================================
// Data Loading
// =============================================================================

bool PCMDataBank::loadDataBlock(uint32_t originalSize,
                                 int (*readFunc)(void* context),
                                 void* context) {
  // Skip empty data blocks (some VGM files have these)
  if (originalSize == 0) {
    return true;
  }

  // Store original size
  originalSize_ = originalSize;

  // If we already have a buffer and data, skip (only load first data block)
  if (dataBank_ && dataSize_ > 0) {
    // Skip this block - just read and discard
    for (uint32_t i = 0; i < originalSize; i++) {
      readFunc(context);
    }
    Serial.println("PCM: Skipping additional data block (already have data)");
    return true;
  }

  // Try allocation sizes: full, half (2x downsample), quarter (4x downsample)
  uint32_t trySizes[] = { originalSize, originalSize / 2, originalSize / 4 };
  uint8_t ratios[] = { 1, 2, 4 };

  for (int attempt = 0; attempt < 3; attempt++) {
    uint32_t trySize = trySizes[attempt];
    if (trySize == 0) continue;

    bool isPSRAM = false;
    uint8_t* buffer = tryAllocate(trySize, isPSRAM);

    if (buffer) {
      dataBank_ = buffer;
      allocatedSize_ = trySize;
      usingPSRAM_ = isPSRAM;
      downsampleRatio_ = ratios[attempt];
      dacDisabled_ = false;  // Clear in case a previous empty block set it

      // Read and possibly downsample the data
      uint32_t stored = 0;
      for (uint32_t i = 0; i < originalSize; i++) {
        int byte = readFunc(context);
        if (byte < 0) break;

        // Only store every Nth sample based on ratio
        if ((i % downsampleRatio_) == 0 && stored < allocatedSize_) {
          dataBank_[stored++] = (uint8_t)byte;
        }
      }

      dataSize_ = stored;
      position_ = 0;

      // Print status
      Serial.print("PCM: Loaded ");
      Serial.print(dataSize_);
      Serial.print(" bytes");
      if (downsampleRatio_ > 1) {
        Serial.print(" (downsampled ");
        Serial.print(downsampleRatio_);
        Serial.print("x from ");
        Serial.print(originalSize);
        Serial.print(")");
      }
      Serial.print(" into ");
      Serial.println(usingPSRAM_ ? "PSRAM" : "RAM");

      if (downsampleRatio_ > 1) {
        Serial.println("PCM: TIP - For better quality, use vgm_prep.py:");
        Serial.print("PCM:   python vgm_prep.py song.vgz --dac-rate ");
        Serial.print(downsampleRatio_);
        Serial.println(" -o song.vgm");
      }

      return true;
    }
  }

  // All allocation attempts failed
  dacDisabled_ = true;

  // Still need to read and discard the data
  for (uint32_t i = 0; i < originalSize; i++) {
    readFunc(context);
  }

  Serial.print("PCM: WARNING - Could not allocate memory for ");
  Serial.print(originalSize);
  Serial.println(" bytes of DAC data");
  Serial.print("PCM: Free RAM: ");
  Serial.print(getFreeMemory());
  Serial.println(" bytes");
  Serial.println("PCM: DAC playback disabled for this file");
  Serial.println("PCM: TIP - Use vgm_prep.py to convert for low-memory playback:");
  Serial.println("PCM:   python vgm_prep.py song.vgz --dac-rate 4 -o song.vgm");

  return false;
}

void PCMDataBank::clear() {
  if (dataBank_) {
#if defined(PCM_USE_PSRAM)
    if (usingPSRAM_) {
      extmem_free(dataBank_);
    } else {
      delete[] dataBank_;
    }
#else
    delete[] dataBank_;
#endif
    dataBank_ = nullptr;
  }

  allocatedSize_ = 0;
  dataSize_ = 0;
  originalSize_ = 0;
  position_ = 0;
  downsampleRatio_ = 1;
  readCount_ = 0;
  usingPSRAM_ = false;
  dacDisabled_ = false;
}

// =============================================================================
// Data Access
// =============================================================================

uint8_t PCMDataBank::readByte() {
  if (!dataBank_ || position_ >= dataSize_) {
    return 0x80;  // Silence (center value for unsigned 8-bit audio)
  }

  // When downsampled, we need to return the same sample multiple times
  // to maintain correct timing. readCount_ tracks how many times we've
  // returned the current sample.
  uint8_t sample = dataBank_[position_];

  readCount_++;
  if (readCount_ >= downsampleRatio_) {
    readCount_ = 0;
    position_++;
  }

  return sample;
}

void PCMDataBank::seek(uint32_t position) {
  // Convert from original position to stored position
  uint32_t storedPos = position / downsampleRatio_;

  if (storedPos < dataSize_) {
    position_ = storedPos;
  } else {
    position_ = dataSize_;
  }

  // Reset read counter - start fresh at new position
  readCount_ = 0;
}

uint32_t PCMDataBank::getPosition() const {
  // Convert from stored position back to original position
  return position_ * downsampleRatio_;
}

// =============================================================================
// Status
// =============================================================================

void PCMDataBank::printStatus() const {
  Serial.println("=== PCM Data Bank Status ===");

  if (dacDisabled_) {
    Serial.println("  Status: DAC DISABLED (no memory)");
  } else if (dataBank_) {
    Serial.print("  Status: Active (");
    Serial.print(usingPSRAM_ ? "PSRAM" : "RAM");
    Serial.println(")");
    Serial.print("  Stored: ");
    Serial.print(dataSize_);
    Serial.print(" / ");
    Serial.print(allocatedSize_);
    Serial.println(" bytes");
    if (downsampleRatio_ > 1) {
      Serial.print("  Downsample: ");
      Serial.print(downsampleRatio_);
      Serial.print("x (original: ");
      Serial.print(originalSize_);
      Serial.println(" bytes)");
    }
    Serial.print("  Position: ");
    Serial.println(position_);
  } else {
    Serial.println("  Status: Not allocated");
  }

  Serial.print("  Free RAM: ");
  Serial.print(getFreeMemory());
  Serial.println(" bytes");
}
