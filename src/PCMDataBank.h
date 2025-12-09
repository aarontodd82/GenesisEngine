#ifndef PCM_DATA_BANK_H
#define PCM_DATA_BANK_H

#include <Arduino.h>
#include "config/platform_detect.h"

// =============================================================================
// PCMDataBank - Dynamic PCM sample storage for DAC playback
//
// Memory strategy:
// 1. Try PSRAM first (if available on Teensy 4.1)
// 2. Fall back to regular RAM
// 3. Try progressively smaller allocations until one succeeds
// 4. Downsample PCM data if needed to fit available memory
// 5. If no memory available, DAC playback is disabled gracefully
//
// This approach works on any platform - from Uno to Teensy 4.1
// =============================================================================

class PCMDataBank {
public:
  PCMDataBank();
  ~PCMDataBank();

  // -------------------------------------------------------------------------
  // Data Loading
  // -------------------------------------------------------------------------

  // Load PCM data block from VGM file
  // Allocates memory on first call, downsamples if needed
  // originalSize: size of data in VGM file
  // readFunc: function to read bytes from source
  // Returns true if data was loaded (even if downsampled)
  bool loadDataBlock(uint32_t originalSize,
                     int (*readFunc)(void* context),
                     void* context);

  // Clear all data and free memory
  void clear();

  // -------------------------------------------------------------------------
  // Data Access (for DAC playback)
  // -------------------------------------------------------------------------

  // Read byte at current position, advance position
  // Returns 0x80 (silence) if no data available
  uint8_t readByte();

  // Seek to position in original data space
  // (automatically adjusts for downsample ratio)
  void seek(uint32_t position);

  // Get current position (in original data space)
  uint32_t getPosition() const;

  // -------------------------------------------------------------------------
  // Status
  // -------------------------------------------------------------------------

  // Check if data bank has data
  bool hasData() const { return dataSize_ > 0; }

  // Check if DAC is disabled (no memory available)
  bool isDACDisabled() const { return dacDisabled_; }

  // Get actual stored size
  uint32_t getStoredSize() const { return dataSize_; }

  // Get original size before downsampling
  uint32_t getOriginalSize() const { return originalSize_; }

  // Get downsample ratio (1, 2, or 4)
  uint8_t getDownsampleRatio() const { return downsampleRatio_; }

  // Check if using PSRAM
  bool isPSRAM() const { return usingPSRAM_; }

  // Print status to Serial
  void printStatus() const;

private:
  uint8_t* dataBank_;       // PCM data storage
  uint32_t allocatedSize_;  // Size of allocated buffer
  uint32_t dataSize_;       // Actual data stored
  uint32_t originalSize_;   // Original size before downsampling
  uint32_t position_;       // Current read position (in stored data)
  uint8_t downsampleRatio_; // 1, 2, or 4
  uint8_t readCount_;       // Counter for repeating samples when downsampled
  bool usingPSRAM_;         // True if allocated from PSRAM
  bool dacDisabled_;        // True if allocation failed

  // Try to allocate memory, returns nullptr if failed
  uint8_t* tryAllocate(uint32_t size, bool& isPSRAM);

  // Get available free memory estimate
  static int getFreeMemory();
};

#endif // PCM_DATA_BANK_H
