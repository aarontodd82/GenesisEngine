#ifndef GEP_PLAYER_H
#define GEP_PLAYER_H

#include <Arduino.h>
#include "GenesisBoard.h"

// =============================================================================
// GEPPlayer - Player for GEP (Genesis Engine Packed) format
//
// GEP is an optimized format that achieves 2-4x better compression than VGM
// while maintaining full playback quality. Designed for AVR microcontrollers.
// =============================================================================

// GEP format constants
#define GEP_MAGIC_0 'G'
#define GEP_MAGIC_1 'E'
#define GEP_MAGIC_2 'P'
#define GEP_MAGIC_3 0x01

// Header flags
#define GEP_FLAG_PSG        0x01
#define GEP_FLAG_YM2612     0x02
#define GEP_FLAG_DAC        0x04
#define GEP_FLAG_MULTI_CHUNK 0x08
#define GEP_FLAG_DPCM       0x10
#define GEP_FLAG_SAMPLES    0x20

// Command bytes
#define GEP_CMD_WAIT_SHORT_BASE   0x00  // 0x00-0x3F: wait 1-64 samples
#define GEP_CMD_DICT_WRITE_BASE   0x40  // 0x40-0x7F: dict entry 0-63
#define GEP_CMD_PSG_MULTI_BASE    0x80  // 0x80-0x8F: 1-16 PSG writes
#define GEP_CMD_WAIT_FRAMES_BASE  0x90  // 0x90-0x9F: wait 1-16 frames
#define GEP_CMD_YM_KEY_BASE       0xA0  // 0xA0-0xAB: key on/off
#define GEP_CMD_DICT_EXT          0xB0
#define GEP_CMD_YM_RAW_P0         0xB1
#define GEP_CMD_YM_RAW_P1         0xB2
#define GEP_CMD_PSG_RAW           0xB3
#define GEP_CMD_WAIT_LONG         0xB4
#define GEP_CMD_LOOP_MARK         0xB5
#define GEP_CMD_DAC_WRITE         0xB6
#define GEP_CMD_DAC_SEEK          0xB7
#define GEP_CMD_DAC_BLOCK         0xB8
#define GEP_CMD_DAC_RUN           0xB9
#define GEP_CMD_SAMPLE_PLAY       0xBB  // Play sample: [id] [rate]
#define GEP_CMD_DAC_START         0xBC  // Start DAC stream: [pos_lo] [pos_hi] [rate]
#define GEP_CMD_DAC_WAIT_BASE     0xC0  // 0xC0-0xCF: DAC + wait 0-15
#define GEP_CMD_SAMPLE_BASE       0xD0  // 0xD0-0xDF: Play sample 0-15 (quick)
#define GEP_CMD_CHUNK_END         0xFE
#define GEP_CMD_END               0xFF

#define GEP_SAMPLES_PER_FRAME     735

// Player state
enum class GEPState {
  STOPPED,
  PLAYING,
  PAUSED,
  FINISHED
};

class GEPPlayer {
public:
  GEPPlayer(GenesisBoard& board);

  // -------------------------------------------------------------------------
  // Playback Control
  // -------------------------------------------------------------------------

  // Play GEP from single PROGMEM array
  bool play(const uint8_t* header, const uint8_t* dict,
            const uint8_t* data, const uint8_t* pcm = nullptr,
            const uint8_t* samples = nullptr, uint8_t sampleCount = 0);

  // Play GEP from multiple chunks (for large songs)
  bool playChunked(const uint8_t* header, const uint8_t* dict,
                   const uint8_t* const* chunks, const uint16_t* chunkSizes,
                   uint8_t chunkCount, const uint8_t* pcm = nullptr,
                   const uint8_t* samples = nullptr, uint8_t sampleCount = 0);

  void stop();
  void pause();
  void resume();

  // -------------------------------------------------------------------------
  // Update - MUST BE CALLED FREQUENTLY
  // -------------------------------------------------------------------------

  void update();

  // -------------------------------------------------------------------------
  // Status
  // -------------------------------------------------------------------------

  GEPState getState() const { return state_; }
  bool isPlaying() const { return state_ == GEPState::PLAYING; }
  bool isPaused() const { return state_ == GEPState::PAUSED; }
  bool isStopped() const { return state_ == GEPState::STOPPED; }
  bool isFinished() const { return state_ == GEPState::FINISHED; }

  // -------------------------------------------------------------------------
  // Settings
  // -------------------------------------------------------------------------

  void setLooping(bool loop) { looping_ = loop; }
  bool isLooping() const { return looping_; }

  // -------------------------------------------------------------------------
  // Information
  // -------------------------------------------------------------------------

  uint32_t getTotalSamples() const { return totalSamples_; }
  uint32_t getCurrentSample() const { return currentSample_; }

  float getDurationSeconds() const {
    return (float)totalSamples_ / 44100.0f;
  }

  float getPositionSeconds() const {
    return (float)currentSample_ / 44100.0f;
  }

private:
  GenesisBoard& board_;

  // Header info
  uint16_t flags_;
  uint16_t dictCount_;
  uint32_t totalSamples_;
  uint16_t loopChunk_;
  uint16_t loopOffset_;

  // Data pointers (in PROGMEM)
  const uint8_t* dict_;
  const uint8_t* pcm_;
  const uint8_t* samples_;      // Sample table (5 bytes per sample)
  uint8_t sampleCount_;
  const uint8_t* const* chunks_;
  const uint16_t* chunkSizes_;
  uint8_t chunkCount_;

  // Current position
  uint8_t currentChunk_;
  uint16_t currentPos_;
  const uint8_t* currentData_;
  uint16_t currentDataSize_;

  // PCM state
  uint16_t pcmPos_;
  uint8_t dpcmSample_;   // Current DPCM decoded sample value
  bool useDPCM_;         // True if PCM is DPCM compressed

  // Sample playback state (for reconstructing DAC timing)
  bool samplePlaying_;       // Is a sample currently playing?
  uint16_t sampleEnd_;       // End position in PCM data
  uint8_t sampleRate_;       // DAC output rate (in 44100Hz sample units)
  uint32_t sampleWaitAccum_; // Accumulated time until next DAC byte
  uint32_t sampleLastCheck_; // Last targetSamples value when we checked

  // Playback state
  GEPState state_;
  bool looping_;
  uint32_t currentSample_;
  uint32_t waitSamples_;
  uint32_t playbackStartTime_;
  uint32_t samplesPlayed_;

  // -------------------------------------------------------------------------
  // Internal Methods
  // -------------------------------------------------------------------------

  void parseHeader(const uint8_t* header);
  bool loadChunk(uint8_t chunkIndex);
  uint8_t readByte();
  uint16_t readWord();
  void processCommands();
  int32_t processCommand();

  // Dictionary lookup
  void writeDictEntry(uint8_t index);

  // Key on/off helpers
  void writeKeyOnOff(uint8_t code);

  // PCM/DPCM helpers
  uint8_t readPCMSample();
  void seekPCM(uint16_t pos);

  // Sample playback helpers
  void triggerSample(uint8_t sampleId, uint8_t rate);
};

#endif // GEP_PLAYER_H
