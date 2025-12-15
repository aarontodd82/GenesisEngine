#ifndef GENESIS_ENGINE_H
#define GENESIS_ENGINE_H

#include <Arduino.h>
#include "config/platform_detect.h"
#include "config/feature_config.h"
#include "GenesisBoard.h"
#include "VGMParser.h"
#include "sources/VGMSource.h"
#include "sources/ProgmemSource.h"
#include "sources/ChunkedProgmemSource.h"
#if GENESIS_ENGINE_USE_SD
#include "sources/SDSource.h"
#endif
#if GENESIS_ENGINE_USE_VGZ && GENESIS_ENGINE_USE_SD
#include "sources/VGZSource.h"
#endif

// =============================================================================
// GenesisEngine - VGM Player for FM-90s Genesis Engine
//
// Simple API for playing VGM files on YM2612 + SN76489 hardware
// Supports Genesis, Mega Drive, Game Gear, and Master System VGMs
// =============================================================================

// Player state enum
enum class GenesisEngineState {
  STOPPED,
  PLAYING,
  PAUSED,
  FINISHED
};

class GenesisEngine {
public:
  // -------------------------------------------------------------------------
  // Constructor
  // -------------------------------------------------------------------------
  GenesisEngine(GenesisBoard& board);

  // -------------------------------------------------------------------------
  // Playback Control
  // -------------------------------------------------------------------------

  // Play VGM from PROGMEM data
  // data: pointer to VGM data in flash
  // length: size of data in bytes
  // Returns true if playback started successfully
  bool play(const uint8_t* data, size_t length);

  // Play VGM from chunked PROGMEM data (for AVR large files)
  // chunks: PROGMEM array of pointers to chunk arrays
  // chunkSizes: PROGMEM array of chunk sizes (uint16_t)
  // numChunks: number of chunks
  // totalLength: total size across all chunks
  // Returns true if playback started successfully
  bool playChunked(const uint8_t* const* chunks, const uint16_t* chunkSizes,
                   uint8_t numChunks, uint32_t totalLength);

  // Stop playback
  void stop();

  // Pause playback
  void pause();

  // Resume from pause
  void resume();

  // -------------------------------------------------------------------------
  // Update - MUST BE CALLED FREQUENTLY
  // -------------------------------------------------------------------------

  // Call this in loop() as often as possible
  // Processes VGM commands and maintains timing
  void update();

  // -------------------------------------------------------------------------
  // Status
  // -------------------------------------------------------------------------

  // Get current player state
  GenesisEngineState getState() const { return state_; }

  // Convenience state checks
  bool isPlaying() const { return state_ == GenesisEngineState::PLAYING; }
  bool isPaused() const { return state_ == GenesisEngineState::PAUSED; }
  bool isStopped() const { return state_ == GenesisEngineState::STOPPED; }
  bool isFinished() const { return state_ == GenesisEngineState::FINISHED; }

  // -------------------------------------------------------------------------
  // Settings
  // -------------------------------------------------------------------------

  // Enable/disable looping
  void setLooping(bool loop) { looping_ = loop; }
  bool isLooping() const { return looping_; }

  // -------------------------------------------------------------------------
  // Information
  // -------------------------------------------------------------------------

  // Get total duration in samples (at 44100 Hz)
  uint32_t getTotalSamples() const { return parser_.getTotalSamples(); }

  // Get current position in samples
  uint32_t getCurrentSample() const { return currentSample_; }

  // Get duration in seconds
  float getDurationSeconds() const {
    return (float)getTotalSamples() / VGM_SAMPLE_RATE;
  }

  // Get current position in seconds
  float getPositionSeconds() const {
    return (float)getCurrentSample() / VGM_SAMPLE_RATE;
  }

  // Check if file has YM2612 (FM) data
  bool hasYM2612() const { return parser_.hasYM2612(); }

  // Check if file has SN76489 (PSG) data
  bool hasSN76489() const { return parser_.hasSN76489(); }

  // Check if file has a loop point
  bool hasLoop() const { return parser_.hasLoop(); }

  // Get number of times the file has looped (0 = first play through)
  uint16_t getLoopCount() const { return parser_.getLoopCount(); }

#if GENESIS_ENGINE_USE_SD
  // -------------------------------------------------------------------------
  // SD Card Playback (if available)
  // -------------------------------------------------------------------------

  // Play VGM file from SD card
  bool playFile(const char* path);
#endif

private:
  GenesisBoard& board_;
  VGMParser parser_;

  // Sources
  ProgmemSource progmemSource_;
  ChunkedProgmemSource chunkedProgmemSource_;
#if GENESIS_ENGINE_USE_SD
  SDSource sdSource_;
#endif
#if GENESIS_ENGINE_USE_VGZ && GENESIS_ENGINE_USE_SD
  VGZSource vgzSource_;
#endif

  // State
  GenesisEngineState state_;
  bool looping_;

  // Timing - using fixed-point for AVR compatibility
  // VGM runs at 44100 Hz = 22.675736961 microseconds per sample
  // We use fixed-point: 22676 * 1000 = 22676000 nanoseconds (0.001% error)
  // Or simpler: track microseconds and target time
  uint32_t currentSample_;
  uint32_t waitSamples_;
  uint32_t playbackStartTime_;  // micros() when playback started
  uint32_t samplesPlayed_;      // total samples worth of time elapsed

  // Note: PCM data for DAC playback is handled dynamically by PCMDataBank
  // inside VGMParser. It allocates memory as needed (PSRAM if available,
  // otherwise RAM) and automatically downsamples if memory is limited.

  // -------------------------------------------------------------------------
  // Internal Methods
  // -------------------------------------------------------------------------

  // Start playback from current source
  bool startPlayback();

  // Process pending samples
  void processCommands();
};

#endif // GENESIS_ENGINE_H
