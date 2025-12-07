#include "GenesisEngine.h"

// =============================================================================
// Timing Constants
// VGM sample rate is 44100 Hz
// Microseconds per sample = 1000000 / 44100 = 22.675736961...
//
// For integer math, we use: samples = (elapsed_micros * 441) / 10000
// This gives us exact timing with no floating point
// =============================================================================

// =============================================================================
// Constructor
// =============================================================================

GenesisEngine::GenesisEngine(GenesisBoard& board)
  : board_(board),
    parser_(board),
    state_(GenesisEngineState::STOPPED),
    looping_(false),
    currentSample_(0),
    waitSamples_(0),
    playbackStartTime_(0),
    samplesPlayed_(0)
{
#if PCM_BUFFER_SIZE > 0
  parser_.setPCMBuffer(pcmBuffer_, PCM_BUFFER_SIZE);
#endif
}

// =============================================================================
// Playback Control
// =============================================================================

bool GenesisEngine::play(const uint8_t* data, size_t length) {
  // Stop any current playback
  stop();

  // Set up PROGMEM source
  progmemSource_.setData(data, length);

  if (!progmemSource_.open()) {
    GENESIS_DEBUG_PRINTLN("Failed to open PROGMEM source");
    return false;
  }

  parser_.setSource(&progmemSource_);

  return startPlayback();
}

bool GenesisEngine::startPlayback() {
  // Parse header
  if (!parser_.parseHeader()) {
    GENESIS_DEBUG_PRINTLN("Failed to parse VGM header");
    state_ = GenesisEngineState::STOPPED;
    return false;
  }

  // Reset timing
  currentSample_ = 0;
  waitSamples_ = 0;
  samplesPlayed_ = 0;
  playbackStartTime_ = micros();

  // Reset hardware
  board_.muteAll();

  // Start playing
  state_ = GenesisEngineState::PLAYING;

  GENESIS_DEBUG_PRINTLN("Playback started");
  return true;
}

void GenesisEngine::stop() {
  if (state_ == GenesisEngineState::STOPPED) {
    return;
  }

  // Mute all sound
  board_.muteAll();

  // Reset state
  parser_.reset();
  state_ = GenesisEngineState::STOPPED;
  currentSample_ = 0;
  waitSamples_ = 0;

  GENESIS_DEBUG_PRINTLN("Playback stopped");
}

void GenesisEngine::pause() {
  if (state_ == GenesisEngineState::PLAYING) {
    state_ = GenesisEngineState::PAUSED;
    board_.muteAll();
    GENESIS_DEBUG_PRINTLN("Playback paused");
  }
}

void GenesisEngine::resume() {
  if (state_ == GenesisEngineState::PAUSED) {
    state_ = GenesisEngineState::PLAYING;
    // Adjust start time so timing continues correctly
    // We pretend playback started (now - time_already_played)
    uint32_t elapsedSamplesMicros = (samplesPlayed_ * 10000UL) / 441UL;
    playbackStartTime_ = micros() - elapsedSamplesMicros;
    GENESIS_DEBUG_PRINTLN("Playback resumed");
  }
}

// =============================================================================
// Update
// =============================================================================

void GenesisEngine::update() {
  if (state_ != GenesisEngineState::PLAYING) {
    return;
  }

  uint32_t now = micros();
  uint32_t elapsed = now - playbackStartTime_;

  // Handle micros() overflow (happens every ~70 minutes)
  // If elapsed is huge, assume overflow occurred
  if (elapsed > 0x80000000UL) {
    // Overflow - reset timing base
    playbackStartTime_ = now;
    elapsed = 0;
    // Keep samplesPlayed_ as-is to maintain position
  }

  // Calculate how many samples should have played by now
  // samples = elapsed_micros * 44100 / 1000000 = elapsed_micros * 441 / 10000
  uint32_t targetSamples = (elapsed / 10000UL) * 441UL + ((elapsed % 10000UL) * 441UL) / 10000UL;

  // Process commands until we catch up
  while (samplesPlayed_ < targetSamples) {
    // If we have pending wait samples, consume them
    if (waitSamples_ > 0) {
      uint32_t samplesToAdvance = targetSamples - samplesPlayed_;
      if (samplesToAdvance > waitSamples_) {
        samplesToAdvance = waitSamples_;
      }

      waitSamples_ -= samplesToAdvance;
      samplesPlayed_ += samplesToAdvance;
      currentSample_ += samplesToAdvance;

      if (waitSamples_ > 0) {
        // Still waiting, done for now
        return;
      }
    }

    // Process commands until next wait
    processCommands();

    if (state_ != GenesisEngineState::PLAYING) {
      // Playback ended
      return;
    }
  }
}

void GenesisEngine::processCommands() {
  // Process VGM commands until a wait is encountered
  waitSamples_ = parser_.processUntilWait();

  if (parser_.isFinished()) {
    // End of file reached
    if (looping_ && parser_.hasLoop()) {
      // Seek to loop point
      if (parser_.seekToLoop()) {
        GENESIS_DEBUG_PRINTLN("Looping");
        waitSamples_ = parser_.processUntilWait();
        return;
      }
    }

    // Playback finished
    board_.muteAll();
    state_ = GenesisEngineState::FINISHED;
    GENESIS_DEBUG_PRINTLN("Playback finished");
  }
}

// =============================================================================
// SD Card Playback (placeholder for Phase 2)
// =============================================================================

#if GENESIS_ENGINE_USE_SD
bool GenesisEngine::playFile(const char* path) {
  // TODO: Implement in Phase 2
  GENESIS_DEBUG_PRINTLN("SD playback not yet implemented");
  return false;
}
#endif
