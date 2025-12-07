#ifndef GENESIS_ENGINE_H
#define GENESIS_ENGINE_H

#include <Arduino.h>
#include "config/platform_detect.h"
#include "config/feature_config.h"
#include "GenesisBoard.h"
#include "VGMParser.h"
#include "sources/VGMSource.h"
#include "sources/ProgmemSource.h"

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

  // PCM buffer for DAC playback
  // Size depends on platform
#if defined(PLATFORM_TEENSY4)
  static constexpr uint32_t PCM_BUFFER_SIZE = 65536;  // 64KB
#elif defined(PLATFORM_TEENSY3) || defined(PLATFORM_ESP32)
  static constexpr uint32_t PCM_BUFFER_SIZE = 32768;  // 32KB
#elif defined(PLATFORM_RP2040)
  static constexpr uint32_t PCM_BUFFER_SIZE = 16384;  // 16KB
#else
  static constexpr uint32_t PCM_BUFFER_SIZE = 0;      // No PCM buffer on AVR
#endif

#if PCM_BUFFER_SIZE > 0
  uint8_t pcmBuffer_[PCM_BUFFER_SIZE];
#endif

  // -------------------------------------------------------------------------
  // Internal Methods
  // -------------------------------------------------------------------------

  // Start playback from current source
  bool startPlayback();

  // Process pending samples
  void processCommands();
};

#endif // GENESIS_ENGINE_H
