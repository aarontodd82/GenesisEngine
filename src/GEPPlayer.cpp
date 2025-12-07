#include "GEPPlayer.h"
#include "config/feature_config.h"

// DPCM 4-bit delta step table (must match vgm2gep.py)
static const int8_t DPCM_STEPS[16] PROGMEM = {
  -34, -21, -13, -8, -5, -3, -1, 0, 1, 3, 5, 8, 13, 21, 34, 55
};

// =============================================================================
// Constructor
// =============================================================================

GEPPlayer::GEPPlayer(GenesisBoard& board)
  : board_(board),
    flags_(0),
    dictCount_(0),
    totalSamples_(0),
    loopChunk_(0xFFFF),
    loopOffset_(0xFFFF),
    dict_(nullptr),
    pcm_(nullptr),
    samples_(nullptr),
    sampleCount_(0),
    chunks_(nullptr),
    chunkSizes_(nullptr),
    chunkCount_(0),
    currentChunk_(0),
    currentPos_(0),
    currentData_(nullptr),
    currentDataSize_(0),
    pcmPos_(0),
    dpcmSample_(128),
    useDPCM_(false),
    samplePlaying_(false),
    sampleEnd_(0),
    sampleRate_(0),
    sampleWaitAccum_(0),
    state_(GEPState::STOPPED),
    looping_(false),
    currentSample_(0),
    waitSamples_(0),
    playbackStartTime_(0),
    samplesPlayed_(0)
{
}

// =============================================================================
// Playback Control
// =============================================================================

bool GEPPlayer::play(const uint8_t* header, const uint8_t* dict,
                     const uint8_t* data, const uint8_t* pcm,
                     const uint8_t* samples, uint8_t sampleCount) {
  stop();

  // Parse header
  parseHeader(header);

  // Store samples table
  samples_ = samples;
  sampleCount_ = sampleCount;

  // Store pointers
  dict_ = dict;
  pcm_ = pcm;
  chunks_ = nullptr;
  chunkSizes_ = nullptr;
  chunkCount_ = 1;
  currentData_ = data;
  currentDataSize_ = 0xFFFF;  // Unknown, will stop on END command

  // Reset state
  currentChunk_ = 0;
  currentPos_ = 0;
  pcmPos_ = 0;
  currentSample_ = 0;
  waitSamples_ = 0;
  samplesPlayed_ = 0;
  playbackStartTime_ = micros();

  // Check for DPCM compressed PCM
  useDPCM_ = (flags_ & GEP_FLAG_DPCM) != 0;
  if (useDPCM_ && pcm_ != nullptr) {
    // Initialize DPCM decoder with first sample
    dpcmSample_ = pgm_read_byte(&pcm_[0]);
    pcmPos_ = 1;  // Skip initial sample byte
  } else {
    dpcmSample_ = 128;
  }

  // Reset sample playback state
  samplePlaying_ = false;
  sampleEnd_ = 0;
  sampleRate_ = 0;
  sampleWaitAccum_ = 0;
  sampleLastCheck_ = 0;

  // Mute and start
  board_.muteAll();
  state_ = GEPState::PLAYING;

  GENESIS_DEBUG_PRINTLN("GEP playback started");
  return true;
}

bool GEPPlayer::playChunked(const uint8_t* header, const uint8_t* dict,
                            const uint8_t* const* chunks, const uint16_t* chunkSizes,
                            uint8_t chunkCount, const uint8_t* pcm,
                            const uint8_t* samples, uint8_t sampleCount) {
  stop();

  // Parse header
  parseHeader(header);

  // Store samples table
  samples_ = samples;
  sampleCount_ = sampleCount;

  // Store pointers
  dict_ = dict;
  pcm_ = pcm;
  chunks_ = chunks;
  chunkSizes_ = chunkSizes;
  chunkCount_ = chunkCount;

  // Load first chunk
  if (!loadChunk(0)) {
    return false;
  }

  // Reset state
  pcmPos_ = 0;
  currentSample_ = 0;
  waitSamples_ = 0;
  samplesPlayed_ = 0;
  playbackStartTime_ = micros();

  // Check for DPCM compressed PCM
  useDPCM_ = (flags_ & GEP_FLAG_DPCM) != 0;
  if (useDPCM_ && pcm_ != nullptr) {
    // Initialize DPCM decoder with first sample
    dpcmSample_ = pgm_read_byte(&pcm_[0]);
    pcmPos_ = 1;  // Skip initial sample byte
  } else {
    dpcmSample_ = 128;
  }

  // Reset sample playback state
  samplePlaying_ = false;
  sampleEnd_ = 0;
  sampleRate_ = 0;
  sampleWaitAccum_ = 0;
  sampleLastCheck_ = 0;

  // Mute and start
  board_.muteAll();
  state_ = GEPState::PLAYING;

  GENESIS_DEBUG_PRINTLN("GEP chunked playback started");
  return true;
}

void GEPPlayer::stop() {
  if (state_ == GEPState::STOPPED) {
    return;
  }

  board_.muteAll();
  state_ = GEPState::STOPPED;
  currentSample_ = 0;
  waitSamples_ = 0;

  GENESIS_DEBUG_PRINTLN("GEP playback stopped");
}

void GEPPlayer::pause() {
  if (state_ == GEPState::PLAYING) {
    state_ = GEPState::PAUSED;
    board_.muteAll();
    GENESIS_DEBUG_PRINTLN("GEP playback paused");
  }
}

void GEPPlayer::resume() {
  if (state_ == GEPState::PAUSED) {
    state_ = GEPState::PLAYING;
    uint32_t elapsedMicros = (samplesPlayed_ * 10000UL) / 441UL;
    playbackStartTime_ = micros() - elapsedMicros;
    GENESIS_DEBUG_PRINTLN("GEP playback resumed");
  }
}

// =============================================================================
// Update
// =============================================================================

void GEPPlayer::update() {
  if (state_ != GEPState::PLAYING) {
    return;
  }

  uint32_t now = micros();
  uint32_t elapsed = now - playbackStartTime_;

  // Handle micros() overflow
  if (elapsed > 0x80000000UL) {
    playbackStartTime_ = now;
    elapsed = 0;
  }

  // Calculate target samples at 44100Hz (integer math only)
  uint32_t targetSamples = (elapsed / 10000UL) * 441UL +
                           ((elapsed % 10000UL) * 441UL) / 10000UL;

  // Process commands until we catch up to real time
  while (samplesPlayed_ < targetSamples) {
    if (waitSamples_ > 0) {
      uint32_t toAdvance = targetSamples - samplesPlayed_;
      if (toAdvance > waitSamples_) {
        toAdvance = waitSamples_;
      }

      // If DAC streaming, output ONE byte if enough time has passed
      if (samplePlaying_ && pcm_ != nullptr) {
        sampleWaitAccum_ += toAdvance;
        if (sampleWaitAccum_ >= sampleRate_) {
          board_.writeDAC(pgm_read_byte(&pcm_[pcmPos_++]));
          sampleWaitAccum_ = 0;  // Reset, don't accumulate debt
        }
      }

      waitSamples_ -= toAdvance;
      samplesPlayed_ += toAdvance;
      currentSample_ += toAdvance;

      if (waitSamples_ > 0) {
        return;
      }
    }

    processCommands();

    if (state_ != GEPState::PLAYING) {
      return;
    }
  }
}

// =============================================================================
// Internal Methods
// =============================================================================

void GEPPlayer::parseHeader(const uint8_t* header) {
  // Skip magic (4 bytes)
  flags_ = pgm_read_byte(header + 4) | (pgm_read_byte(header + 5) << 8);
  dictCount_ = pgm_read_byte(header + 6);
  if (dictCount_ == 0) dictCount_ = 256;
  // Skip PCM block count (header + 7)
  totalSamples_ = pgm_read_byte(header + 8) |
                  (pgm_read_byte(header + 9) << 8) |
                  ((uint32_t)pgm_read_byte(header + 10) << 16) |
                  ((uint32_t)pgm_read_byte(header + 11) << 24);
  loopChunk_ = pgm_read_byte(header + 12) | (pgm_read_byte(header + 13) << 8);
  loopOffset_ = pgm_read_byte(header + 14) | (pgm_read_byte(header + 15) << 8);
}

bool GEPPlayer::loadChunk(uint8_t chunkIndex) {
  if (chunkIndex >= chunkCount_) {
    return false;
  }

  currentChunk_ = chunkIndex;
  currentPos_ = 0;

  if (chunks_ != nullptr) {
    // Multi-chunk mode
    currentData_ = (const uint8_t*)pgm_read_ptr(&chunks_[chunkIndex]);
    currentDataSize_ = pgm_read_word(&chunkSizes_[chunkIndex]);
  }

  return true;
}

uint8_t GEPPlayer::readByte() {
  return pgm_read_byte(&currentData_[currentPos_++]);
}

uint16_t GEPPlayer::readWord() {
  uint8_t lo = readByte();
  uint8_t hi = readByte();
  return lo | (hi << 8);
}

void GEPPlayer::processCommands() {
  waitSamples_ = 0;

  while (waitSamples_ == 0) {
    int32_t result = processCommand();

    if (result < 0) {
      // End or error
      if (looping_ && loopChunk_ != 0xFFFF) {
        // Seek to loop point
        if (loadChunk(loopChunk_)) {
          currentPos_ = loopOffset_;
          GENESIS_DEBUG_PRINTLN("GEP looping");
          continue;
        }
      }
      board_.muteAll();
      state_ = GEPState::FINISHED;
      GENESIS_DEBUG_PRINTLN("GEP playback finished");
      return;
    }

    waitSamples_ = result;
  }
}

int32_t GEPPlayer::processCommand() {
  if (currentPos_ >= currentDataSize_ && currentDataSize_ != 0xFFFF) {
    return -1;
  }

  uint8_t cmd = readByte();

  // WAIT_SHORT (0x00-0x3F): wait 1-64 samples
  if (cmd <= 0x3F) {
    return (cmd & 0x3F) + 1;
  }

  // DICT_WRITE (0x40-0x7F): dictionary entry 0-63
  if (cmd >= 0x40 && cmd <= 0x7F) {
    writeDictEntry(cmd & 0x3F);
    return 0;
  }

  // PSG_MULTI (0x80-0x8F): 1-16 PSG writes
  if (cmd >= 0x80 && cmd <= 0x8F) {
    uint8_t count = (cmd & 0x0F) + 1;
    for (uint8_t i = 0; i < count; i++) {
      board_.writePSG(readByte());
    }
    return 0;
  }

  // WAIT_FRAMES (0x90-0x9F): wait 1-16 frames
  if (cmd >= 0x90 && cmd <= 0x9F) {
    return ((cmd & 0x0F) + 1) * GEP_SAMPLES_PER_FRAME;
  }

  // YM_KEY (0xA0-0xAB): key on/off shortcuts
  if (cmd >= 0xA0 && cmd <= 0xAB) {
    writeKeyOnOff(cmd & 0x0F);
    return 0;
  }

  // Extended commands
  switch (cmd) {
    case GEP_CMD_DICT_EXT: {
      uint8_t idx = readByte();
      writeDictEntry(idx);
      return 0;
    }

    case GEP_CMD_YM_RAW_P0: {
      uint8_t reg = readByte();
      uint8_t val = readByte();
      board_.writeYM2612(0, reg, val);
      return 0;
    }

    case GEP_CMD_YM_RAW_P1: {
      uint8_t reg = readByte();
      uint8_t val = readByte();
      board_.writeYM2612(1, reg, val);
      return 0;
    }

    case GEP_CMD_PSG_RAW: {
      board_.writePSG(readByte());
      return 0;
    }

    case GEP_CMD_WAIT_LONG: {
      return readWord();
    }

    case GEP_CMD_LOOP_MARK: {
      // Just a marker, store current position as loop point
      // (Already handled in header, but could be used for runtime detection)
      return 0;
    }

    case GEP_CMD_DAC_WRITE: {
      board_.writeDAC(readPCMSample());
      return 0;
    }

    case GEP_CMD_DAC_SEEK: {
      seekPCM(readWord());
      return 0;
    }

    case GEP_CMD_DAC_BLOCK: {
      uint8_t count = readByte();
      uint8_t wait = readByte();
      // Play count samples with fixed wait
      for (uint8_t i = 0; i < count; i++) {
        board_.writeDAC(readPCMSample());
      }
      return count * wait;
    }

    case GEP_CMD_DAC_RUN: {
      // DAC_RUN: [count] [packed nibbles...]
      uint8_t count = readByte();
      uint32_t totalWait = 0;

      for (uint8_t i = 0; i < count; i += 2) {
        uint8_t packed = readByte();
        uint8_t wait0 = (packed >> 4) & 0x0F;
        uint8_t wait1 = packed & 0x0F;

        board_.writeDAC(readPCMSample());
        totalWait += wait0;

        if (i + 1 < count) {
          board_.writeDAC(readPCMSample());
          totalWait += wait1;
        }
      }
      return totalWait;
    }

    case GEP_CMD_CHUNK_END: {
      // Load next chunk
      if (currentChunk_ + 1 < chunkCount_) {
        loadChunk(currentChunk_ + 1);
        return 0;
      }
      return -1;  // No more chunks
    }

    case GEP_CMD_END: {
      return -1;
    }

    case GEP_CMD_SAMPLE_PLAY: {
      // Extended sample play: [sample_id] [rate]
      uint8_t sampleId = readByte();
      uint8_t rate = readByte();
      triggerSample(sampleId, rate);
      return 0;
    }

    case GEP_CMD_DAC_START: {
      // DAC stream start: [pos_lo] [pos_hi] [rate]
      uint16_t pos = readWord();
      uint8_t rate = readByte();
      // Seek to position and start streaming
      pcmPos_ = pos;
      sampleRate_ = rate > 0 ? rate : 1;
      sampleWaitAccum_ = 0;
      samplePlaying_ = true;
      return 0;
    }

    default:
      // DAC_WAIT (0xC0-0xCF): DAC + wait 0-15
      if (cmd >= 0xC0 && cmd <= 0xCF) {
        board_.writeDAC(readPCMSample());
        return cmd & 0x0F;
      }

      // SAMPLE_BASE (0xD0-0xDF): Quick sample trigger 0-15, followed by rate byte
      if (cmd >= GEP_CMD_SAMPLE_BASE && cmd <= (GEP_CMD_SAMPLE_BASE + 0x0F)) {
        uint8_t rate = readByte();
        triggerSample(cmd & 0x0F, rate);
        return 0;
      }

      // Unknown command - skip
      GENESIS_DEBUG_PRINT("Unknown GEP cmd: ");
      GENESIS_DEBUG_PRINTLN(cmd, HEX);
      return 0;
  }
}

void GEPPlayer::writeDictEntry(uint8_t index) {
  // Dictionary format: [port] [reg] [val] - 3 bytes per entry
  uint16_t offset = index * 3;
  uint8_t port = pgm_read_byte(&dict_[offset]);
  uint8_t reg = pgm_read_byte(&dict_[offset + 1]);
  uint8_t val = pgm_read_byte(&dict_[offset + 2]);

  board_.writeYM2612(port, reg, val);
}

void GEPPlayer::writeKeyOnOff(uint8_t code) {
  // Code 0-5: key off for channel 0-5 (port 0: ch 0-2, port 1: ch 3-5)
  // Code 6-11: key on for channel 0-5
  uint8_t channel = code % 6;
  bool keyOn = code >= 6;

  // YM2612 key on register is 0x28
  // Channel bits: 0-2 for port 0, 4-6 for port 1
  uint8_t chBits;
  if (channel < 3) {
    chBits = channel;  // 0, 1, 2
  } else {
    chBits = channel + 1;  // 4, 5, 6
  }

  uint8_t val = chBits | (keyOn ? 0xF0 : 0x00);
  board_.writeYM2612(0, 0x28, val);
}

uint8_t GEPPlayer::readPCMSample() {
  if (pcm_ == nullptr) {
    return 128;  // Silence
  }

  if (useDPCM_) {
    // DPCM format: first byte is initial sample, then packed nibbles
    // Each byte has two 4-bit delta indices (high nibble first)
    // pcmPos_ tracks the nibble position (starting after initial sample)

    // Calculate byte position and nibble within byte
    // After initial sample byte, nibbles are packed 2 per byte
    uint16_t nibbleIdx = pcmPos_ - 1;  // pcmPos_ starts at 1 after init
    uint16_t byteIdx = 1 + (nibbleIdx / 2);  // +1 for initial sample byte
    uint8_t nibbleInByte = nibbleIdx % 2;

    uint8_t packed = pgm_read_byte(&pcm_[byteIdx]);
    uint8_t deltaIdx = nibbleInByte == 0 ? (packed >> 4) : (packed & 0x0F);

    // Apply delta step
    int8_t delta = pgm_read_byte(&DPCM_STEPS[deltaIdx]);
    int16_t newSample = (int16_t)dpcmSample_ + delta;

    // Clamp to 0-255
    if (newSample < 0) newSample = 0;
    if (newSample > 255) newSample = 255;

    dpcmSample_ = (uint8_t)newSample;
    pcmPos_++;

    return dpcmSample_;
  } else {
    // Raw PCM - just read the byte
    return pgm_read_byte(&pcm_[pcmPos_++]);
  }
}

void GEPPlayer::seekPCM(uint16_t pos) {
  if (useDPCM_) {
    // For DPCM, we need to decode from the beginning to reach the target
    // This is expensive but seeks are rare in DAC playback
    // Reset to initial sample
    dpcmSample_ = pgm_read_byte(&pcm_[0]);
    pcmPos_ = 1;

    // Decode samples until we reach the target position
    for (uint16_t i = 0; i < pos; i++) {
      readPCMSample();
    }
  } else {
    // Raw PCM - direct seek
    pcmPos_ = pos;
  }
}

void GEPPlayer::triggerSample(uint8_t sampleId, uint8_t rate) {
  if (samples_ == nullptr || sampleId >= sampleCount_ || pcm_ == nullptr) {
    return;
  }

  // Sample table format: [start_lo, start_hi, length_lo, length_hi, rate] - 5 bytes
  uint16_t offset = sampleId * 5;
  uint16_t start = pgm_read_byte(&samples_[offset]) |
                   (pgm_read_byte(&samples_[offset + 1]) << 8);
  uint16_t length = pgm_read_byte(&samples_[offset + 2]) |
                    (pgm_read_byte(&samples_[offset + 3]) << 8);

  // Set up sample playback - player will output DAC bytes during waits
  pcmPos_ = start;
  sampleEnd_ = start + length;
  sampleRate_ = rate > 0 ? rate : 1;
  sampleWaitAccum_ = 0;  // Output first byte immediately
  samplePlaying_ = true;
}
