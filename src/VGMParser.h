#ifndef VGM_PARSER_H
#define VGM_PARSER_H

#include <Arduino.h>
#include "VGMCommands.h"
#include "sources/VGMSource.h"
#include "GenesisBoard.h"
#include "PCMDataBank.h"

// =============================================================================
// VGMParser - Parses and executes VGM commands
// =============================================================================

// Callback for unsupported chip writes (for future expansion)
typedef void (*UnsupportedChipCallback)(uint8_t cmd, uint8_t reg, uint8_t val);

class VGMParser {
public:
  VGMParser(GenesisBoard& board);

  // -------------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------------

  // Set the data source
  void setSource(VGMSource* source);

  // Parse header and prepare for playback
  // Returns true if valid VGM file for this hardware
  bool parseHeader();

  // -------------------------------------------------------------------------
  // Playback Control
  // -------------------------------------------------------------------------

  // Reset parser state
  void reset();

  // Process commands until a wait is encountered
  // Returns number of samples to wait (0 = end of file or error)
  uint32_t processUntilWait();

  // Check if playback has reached the end
  bool isFinished() const { return finished_; }

  // Check if file has a loop point
  bool hasLoop() const { return hasLoop_; }

  // Seek to loop point (call when end is reached and looping is desired)
  // Increments the loop counter each time it's called
  bool seekToLoop();

  // Get number of times the file has looped (0 = first play through)
  uint16_t getLoopCount() const { return loopCount_; }

  // -------------------------------------------------------------------------
  // File Information
  // -------------------------------------------------------------------------

  uint32_t getTotalSamples() const { return totalSamples_; }
  uint32_t getLoopSamples() const { return loopSamples_; }
  uint32_t getLoopOffset() const { return loopOffset_; }
  uint32_t getLoopOffsetInData() const { return loopOffsetInData_; }
  uint32_t getDataOffset() const { return dataOffset_; }
  uint32_t getVersion() const { return version_; }
  bool hasYM2612() const { return hasYM2612_; }
  bool hasSN76489() const { return hasSN76489_; }

  // -------------------------------------------------------------------------
  // PCM Data Bank (for DAC playback)
  // -------------------------------------------------------------------------

  // Get reference to PCM data bank
  PCMDataBank& getPCMDataBank() { return pcmDataBank_; }
  const PCMDataBank& getPCMDataBank() const { return pcmDataBank_; }

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------

  // Set callback for unsupported chip writes
  void setUnsupportedCallback(UnsupportedChipCallback callback) {
    unsupportedCallback_ = callback;
  }

private:
  GenesisBoard& board_;
  VGMSource* source_;

  // Header info
  uint32_t version_;
  uint32_t totalSamples_;
  uint32_t loopSamples_;
  uint32_t dataOffset_;
  uint32_t loopOffset_;        // Absolute loop position in file
  uint32_t loopOffsetInData_;  // Loop position relative to data start (for seeking)
  bool hasLoop_;
  bool hasYM2612_;
  bool hasSN76489_;

  // Playback state
  bool finished_;
  uint16_t loopCount_;  // Number of times seekToLoop() has been called

  // PSG attenuation for FM+PSG mix (reduces PSG volume when both chips present)
  uint8_t psgAttenuation_;  // 0 = no attenuation, 2 = typical for FM+PSG mix

  // PCM data bank for DAC playback
  PCMDataBank pcmDataBank_;

  // Callback
  UnsupportedChipCallback unsupportedCallback_;

  // -------------------------------------------------------------------------
  // Internal Command Processing
  // -------------------------------------------------------------------------

  // Process a single command
  // Returns samples to wait (0 = no wait, continue processing)
  // Returns -1 on end/error (sets finished_ flag)
  int32_t processCommand();

  // Handle data block command (loads PCM data)
  void handleDataBlock();

  // Skip unknown command
  void skipCommand(uint8_t cmd);
};

#endif // VGM_PARSER_H
