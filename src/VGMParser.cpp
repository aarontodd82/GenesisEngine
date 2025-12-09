#include "VGMParser.h"
#include "config/feature_config.h"

// =============================================================================
// Constructor
// =============================================================================
VGMParser::VGMParser(GenesisBoard& board)
  : board_(board),
    source_(nullptr),
    version_(0),
    totalSamples_(0),
    loopSamples_(0),
    dataOffset_(0),
    loopOffset_(0),
    hasLoop_(false),
    hasYM2612_(false),
    hasSN76489_(false),
    finished_(true),
    unsupportedCallback_(nullptr)
{
}

// =============================================================================
// Initialization
// =============================================================================

void VGMParser::setSource(VGMSource* source) {
  source_ = source;
  reset();
}

bool VGMParser::parseHeader() {
  if (!source_ || !source_->isOpen()) {
    return false;
  }

  // Seek to beginning
  if (!source_->seek(0)) {
    return false;
  }

  // Check magic number
  uint32_t magic = source_->readUInt32();
  if (magic != VGM_MAGIC) {
    GENESIS_DEBUG_PRINTLN("Invalid VGM magic");
    return false;
  }

  // Read header fields
  source_->seek(VGM_OFF_VERSION);
  version_ = source_->readUInt32();

  source_->seek(VGM_OFF_SN76489);
  uint32_t sn76489Clock = source_->readUInt32();
  hasSN76489_ = (sn76489Clock != 0);

  source_->seek(VGM_OFF_SAMPLES);
  totalSamples_ = source_->readUInt32();

  source_->seek(VGM_OFF_LOOP);
  uint32_t loopOffsetRel = source_->readUInt32();
  hasLoop_ = (loopOffsetRel != 0);
  if (hasLoop_) {
    loopOffset_ = VGM_OFF_LOOP + loopOffsetRel;
  }

  source_->seek(VGM_OFF_LOOP_SAMP);
  loopSamples_ = source_->readUInt32();

  // YM2612 clock (v1.10+)
  if (version_ >= 0x110) {
    source_->seek(VGM_OFF_YM2612);
    uint32_t ym2612Clock = source_->readUInt32();
    hasYM2612_ = (ym2612Clock != 0);
  }

  // Data offset (v1.50+)
  if (version_ >= 0x150) {
    source_->seek(VGM_OFF_DATA);
    uint32_t dataOffsetRel = source_->readUInt32();
    if (dataOffsetRel != 0) {
      dataOffset_ = VGM_OFF_DATA + dataOffsetRel;
    } else {
      dataOffset_ = 0x40;  // Default for older versions
    }
  } else {
    dataOffset_ = 0x40;
  }

  // Check if this file is playable on our hardware
  if (!hasYM2612_ && !hasSN76489_) {
    GENESIS_DEBUG_PRINTLN("VGM has no supported chips");
    return false;
  }

  GENESIS_DEBUG_PRINT("VGM version: ");
  GENESIS_DEBUG_PRINTLN(version_, HEX);
  GENESIS_DEBUG_PRINT("YM2612: ");
  GENESIS_DEBUG_PRINTLN(hasYM2612_ ? "yes" : "no");
  GENESIS_DEBUG_PRINT("SN76489: ");
  GENESIS_DEBUG_PRINTLN(hasSN76489_ ? "yes" : "no");
  GENESIS_DEBUG_PRINT("Total samples: ");
  GENESIS_DEBUG_PRINTLN(totalSamples_);

  // Seek to data start
  source_->seek(dataOffset_);
  finished_ = false;

  return true;
}

void VGMParser::reset() {
  finished_ = true;
  pcmDataBank_.clear();
}

// =============================================================================
// Playback Control
// =============================================================================

uint32_t VGMParser::processUntilWait() {
  if (finished_ || !source_) {
    return 0;
  }

  while (source_->available()) {
    int32_t waitSamples = processCommand();

    if (waitSamples < 0) {
      // End of file or error
      finished_ = true;
      return 0;
    }

    if (waitSamples > 0) {
      return waitSamples;
    }

    // waitSamples == 0, continue processing
  }

  // Ran out of data
  finished_ = true;
  return 0;
}

bool VGMParser::seekToLoop() {
  if (!hasLoop_ || !source_ || !source_->canSeek()) {
    return false;
  }

  if (source_->seek(loopOffset_)) {
    finished_ = false;
    return true;
  }

  return false;
}

// =============================================================================
// Command Processing
// =============================================================================

int32_t VGMParser::processCommand() {
  int cmdByte = source_->read();
  if (cmdByte < 0) {
    return -1;  // End of data
  }

  uint8_t cmd = (uint8_t)cmdByte;

  // -------------------------------------------------------------------------
  // PSG Write (0x50)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_PSG) {
    uint8_t val = source_->read();
    board_.writePSG(val);
    return 0;
  }

  // -------------------------------------------------------------------------
  // YM2612 Port 0 Write (0x52)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_YM2612_P0) {
    uint8_t reg = source_->read();
    uint8_t val = source_->read();
    board_.writeYM2612(0, reg, val);
    return 0;
  }

  // -------------------------------------------------------------------------
  // YM2612 Port 1 Write (0x53)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_YM2612_P1) {
    uint8_t reg = source_->read();
    uint8_t val = source_->read();
    board_.writeYM2612(1, reg, val);
    return 0;
  }

  // -------------------------------------------------------------------------
  // Wait N samples (0x61)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_WAIT) {
    uint16_t samples = source_->readUInt16();
    return samples;
  }

  // -------------------------------------------------------------------------
  // Wait 735 samples / 60Hz frame (0x62)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_WAIT_735) {
    return VGM_WAIT_NTSC;
  }

  // -------------------------------------------------------------------------
  // Wait 882 samples / 50Hz frame (0x63)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_WAIT_882) {
    return VGM_WAIT_PAL;
  }

  // -------------------------------------------------------------------------
  // End of VGM data (0x66)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_END) {
    return -1;
  }

  // -------------------------------------------------------------------------
  // Data block (0x67)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_DATA_BLOCK) {
    handleDataBlock();
    return 0;
  }

  // -------------------------------------------------------------------------
  // Short wait (0x70-0x7F = wait 1-16 samples)
  // -------------------------------------------------------------------------
  if (cmd >= 0x70 && cmd <= 0x7F) {
    return (cmd & 0x0F) + 1;
  }

  // -------------------------------------------------------------------------
  // YM2612 DAC + wait (0x80-0x8F)
  // -------------------------------------------------------------------------
  if (cmd >= 0x80 && cmd <= 0x8F) {
    // Write PCM sample from data bank
    if (pcmDataBank_.hasData()) {
      board_.writeDAC(pcmDataBank_.readByte());
    }
    // Return wait count (0-15 samples)
    return cmd & 0x0F;
  }

  // -------------------------------------------------------------------------
  // PCM data seek (0xE0)
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_PCM_SEEK) {
    uint32_t seekPos = source_->readUInt32();
    pcmDataBank_.seek(seekPos);
    return 0;
  }

  // -------------------------------------------------------------------------
  // DAC stream commands (0x90-0x95) - skip for now
  // These require more complex handling
  // -------------------------------------------------------------------------
  if (cmd >= 0x90 && cmd <= 0x95) {
    skipCommand(cmd);
    return 0;
  }

  // -------------------------------------------------------------------------
  // Unsupported chip writes - call callback or skip
  // -------------------------------------------------------------------------
  if (cmd == VGM_CMD_YM2413 || cmd == VGM_CMD_YM2151 || cmd == VGM_CMD_YM2203) {
    uint8_t reg = source_->read();
    uint8_t val = source_->read();
    if (unsupportedCallback_) {
      unsupportedCallback_(cmd, reg, val);
    }
    return 0;
  }

  // -------------------------------------------------------------------------
  // Unknown command - try to skip it
  // -------------------------------------------------------------------------
  skipCommand(cmd);
  return 0;
}

// =============================================================================
// Data Block Handling
// =============================================================================

// Static callback wrapper for PCMDataBank::loadDataBlock
static VGMSource* g_dataBlockSource = nullptr;

static int dataBlockReadCallback(void* context) {
  (void)context;
  if (g_dataBlockSource) {
    return g_dataBlockSource->read();
  }
  return -1;
}

void VGMParser::handleDataBlock() {
  // Format: 0x67 0x66 tt ss ss ss ss [data]
  // tt = data type
  // ss ss ss ss = size (little-endian)

  uint8_t marker = source_->read();  // Should be 0x66
  if (marker != 0x66) {
    GENESIS_DEBUG_PRINTLN("Invalid data block marker");
    return;
  }

  uint8_t dataType = source_->read();
  uint32_t dataSize = source_->readUInt32();

  Serial.print("Data block: type=0x");
  Serial.print(dataType, HEX);
  Serial.print(" size=");
  Serial.println(dataSize);

  // Handle YM2612 PCM data (type 0x00)
  if (dataType == VGM_DATA_YM2612_PCM) {
    // Use PCMDataBank to load the data
    // It will automatically handle memory allocation and downsampling
    g_dataBlockSource = source_;
    pcmDataBank_.loadDataBlock(dataSize, dataBlockReadCallback, nullptr);
    g_dataBlockSource = nullptr;
  } else {
    // Skip unsupported data block types
    Serial.print("Skipping unsupported data block type 0x");
    Serial.println(dataType, HEX);
    for (uint32_t i = 0; i < dataSize; i++) {
      source_->read();
    }
  }
}

// =============================================================================
// Skip Unknown Commands
// =============================================================================

void VGMParser::skipCommand(uint8_t cmd) {
  // Determine how many bytes to skip based on command
  // This is based on the VGM specification

  // Single-byte commands (0x30-0x3F) - 1 data byte
  if (cmd >= 0x30 && cmd <= 0x3F) {
    source_->read();
    return;
  }

  // Two-byte commands (0x40-0x4E) - 2 data bytes
  if (cmd >= 0x40 && cmd <= 0x4E) {
    source_->read();
    source_->read();
    return;
  }

  // 0x4F - Game Boy DMG (1 byte)
  if (cmd == 0x4F) {
    source_->read();
    return;
  }

  // Chip writes (0x51-0x5F) - 2 data bytes each
  if (cmd >= 0x51 && cmd <= 0x5F) {
    source_->read();
    source_->read();
    return;
  }

  // DAC stream commands
  if (cmd == 0x90) { source_->skip(4); return; }  // Setup stream
  if (cmd == 0x91) { source_->skip(4); return; }  // Set data
  if (cmd == 0x92) { source_->skip(5); return; }  // Set frequency
  if (cmd == 0x93) { source_->skip(10); return; } // Start stream
  if (cmd == 0x94) { source_->skip(1); return; }  // Stop stream
  if (cmd == 0x95) { source_->skip(4); return; }  // Start stream fast

  // Various 3-byte commands (0xA0-0xBF)
  if (cmd >= 0xA0 && cmd <= 0xBF) {
    source_->read();
    source_->read();
    return;
  }

  // 4-byte commands (0xC0-0xDF)
  if (cmd >= 0xC0 && cmd <= 0xDF) {
    source_->skip(3);
    return;
  }

  // 5-byte commands (0xE1-0xFF)
  if (cmd >= 0xE1 && cmd <= 0xFF) {
    source_->skip(4);
    return;
  }

  // Unknown - skip nothing and hope for the best
  GENESIS_DEBUG_PRINT("Unknown VGM command: ");
  GENESIS_DEBUG_PRINTLN(cmd, HEX);
}
