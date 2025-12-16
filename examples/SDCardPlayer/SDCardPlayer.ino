/**
 * SDCardPlayer - Play VGM files from SD card with serial menu
 *
 * This example plays VGM files from an SD card and provides an interactive
 * serial menu for file selection, playback control, and playlist support.
 *
 * =============================================================================
 * PLATFORM SUPPORT
 * =============================================================================
 *
 * RECOMMENDED: Teensy 4.1
 *   Best experience - built-in SD, hardware SPI, full VGZ support.
 *
 * ARDUINO UNO: NOT SUPPORTED (only 2KB RAM)
 *
 * ARDUINO MEGA: Limited support - results may vary.
 *   Software SPI for shift register is slower and may affect timing.
 *   Use vgm_prep.py to prepare files.
 *
 * =============================================================================
 * ARDUINO MEGA WIRING
 * =============================================================================
 *
 * The shift register has no chip select, so it MUST use different pins
 * than the SD card to avoid SPI bus conflicts.
 *
 * ARDUINO MEGA PINOUT:
 *   Genesis Engine Board:
 *     WR_P (PSG write)    -> Pin 2
 *     WR_Y (YM2612 write) -> Pin 3
 *     IC_Y (YM2612 reset) -> Pin 4
 *     A0_Y (YM2612 addr)  -> Pin 5
 *     A1_Y (YM2612 port)  -> Pin 6
 *     Shift CLK           -> Pin 7   *** NOT pin 52! ***
 *     Shift DATA          -> Pin 8   *** NOT pin 51! ***
 *
 *   SD Card Module:
 *     CS                  -> Pin 53
 *     MOSI                -> Pin 51
 *     MISO                -> Pin 50
 *     SCK                 -> Pin 52
 *     VCC                 -> 5V
 *     GND                 -> GND
 *
 * TEENSY 4.1:
 *   Uses built-in SD slot (no SD wiring needed)
 *   Genesis board can use hardware SPI (default pins) - no conflict
 *
 * ESP32 PINOUT:
 *   Genesis Engine Board (software SPI - avoids SD card conflict):
 *     WR_P (PSG write)    -> GPIO 16
 *     WR_Y (YM2612 write) -> GPIO 17
 *     IC_Y (YM2612 reset) -> GPIO 25
 *     A0_Y (YM2612 addr)  -> GPIO 26
 *     A1_Y (YM2612 port)  -> GPIO 27
 *     Shift CLK           -> GPIO 4   *** NOT 18! (SD uses hardware SPI) ***
 *     Shift DATA          -> GPIO 13  *** NOT 23! (SD uses hardware SPI) ***
 *
 *   SD Card Module (hardware SPI - VSPI):
 *     CS                  -> GPIO 5
 *     MOSI                -> GPIO 23
 *     MISO                -> GPIO 19
 *     SCK                 -> GPIO 18
 *     VCC                 -> 3.3V (NOT 5V!)
 *     GND                 -> GND
 *
 * =============================================================================
 *
 * SD Card Contents:
 *   - Place .vgm files in the root directory
 *   - Optionally create playlist.txt for playlist mode (see README)
 *
 * Serial Commands:
 *   list              List VGM files on SD card
 *   play <n>          Play file by number
 *   play <filename>   Play file by name
 *   stop              Stop playback
 *   pause             Pause/resume playback
 *   next              Next track (playlist mode)
 *   prev              Previous track (playlist mode)
 *   loop              Toggle loop mode
 *   playlist <name>   Load and start playlist (<name>.txt)
 *   info              Show current track info
 *   help              Show command list
 *
 * Platform Notes:
 *   - Teensy/ESP32: Full .vgm and .vgz support with PCM/DAC playback
 *   - Arduino Uno/Mega: Only .vgm files supported (not .vgz), limited RAM
 *     for PCM data. Use vgm_prep.py to prepare files:
 *
 *       python vgm_prep.py song.vgz -o song.vgm
 *
 *     This tool decompresses VGZ, inlines DAC samples, and reduces DAC
 *     sample rate to 1/4 (suitable for Uno/Mega). The output file plays
 *     without needing RAM for PCM storage. For full quality on faster
 *     boards, use --dac-rate 1.
 */

#include <GenesisEngine.h>
#include <SD.h>

// Check if SD support is available
#if !GENESIS_ENGINE_USE_SD
  #error "SD card support not available on this platform"
#endif

// =============================================================================
// Pin Configuration - Genesis Engine Board (platform specific)
// =============================================================================
// IMPORTANT FOR ARDUINO UNO/MEGA:
// The shift register MUST use pins 7 and 8 (software bit-bang) because the
// SD card uses the hardware SPI pins. See wiring diagram above!
// =============================================================================

#ifdef ARDUINO_ARCH_ESP32
  const uint8_t PIN_WR_P = 16;  // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 17;  // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 25;  // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 26;  // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 27;  // A1_Y - YM2612 address bit 1 (port select)
  const uint8_t PIN_SCK  = 4;   // Shift CLK - Software SPI (GPIO<32 for direct register access)
  const uint8_t PIN_SDI  = 13;  // Shift DATA - Software SPI (GPIO<32 for direct register access)
#else
  // Teensy / Arduino defaults
  const uint8_t PIN_WR_P = 2;   // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 3;   // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 4;   // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 5;   // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 6;   // A1_Y - YM2612 address bit 1 (port select)
  const uint8_t PIN_SCK  = 7;   // Shift register clock - PIN 7 FOR ARDUINO (software SPI)
  const uint8_t PIN_SDI  = 8;   // Shift register data  - PIN 8 FOR ARDUINO (software SPI)
#endif

// SD Card CS pin (platform-specific default from feature_config.h)
const uint8_t PIN_SD_CS = GENESIS_ENGINE_SD_CS_PIN;

// =============================================================================
// Create objects
// =============================================================================

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);
GenesisEngine player(board);

// =============================================================================
// File list management
// =============================================================================

// Platform-specific settings
#if defined(__AVR_ATmega328P__)
  // Uno doesn't have enough RAM for SD playback
  #error "Arduino Uno not supported - use Mega or Teensy instead (Uno only has 2KB RAM)"
#elif defined(PLATFORM_AVR)
  // Mega: 8KB RAM - comfortable for SD playback
  #define MAX_FILES 20
  #define MAX_FILENAME_LEN 13    // 8.3 format only
  #define MAX_PLAYLISTS 4
#else
  // Teensy/ESP32: Plenty of RAM
  #define MAX_FILES 50
  #define MAX_FILENAME_LEN 64    // Long filename support
  #define MAX_PLAYLISTS 10
#endif

char fileList[MAX_FILES][MAX_FILENAME_LEN];
uint8_t fileCount = 0;
int8_t currentFileIndex = -1;

// Playlist file list (stores names without .txt extension)
#ifndef AVR_NO_PLAYLISTS
  #define MAX_PLAYLISTS 10
  char playlistFiles[MAX_PLAYLISTS][MAX_FILENAME_LEN];
  uint8_t playlistFileCount = 0;

  // ===========================================================================
  // Playlist Management
  // ===========================================================================

  #define MAX_PLAYLIST_SIZE MAX_FILES

  uint8_t playlistIndices[MAX_PLAYLIST_SIZE];  // Index into fileList for each track
  uint8_t playlistPlays[MAX_PLAYLIST_SIZE];    // How many times to play each track
  uint8_t playlistSize = 0;
  uint8_t playlistPos = 0;
  uint8_t currentPlays = 0;           // How many times current track has played
  uint16_t lastLoopCount = 0;         // Track loop count to detect new loops
  bool playlistShuffle = false;
  bool playlistLoop = false;
  bool playlistActive = false;

  // Delay between songs in playlist (milliseconds)
  #define PLAYLIST_SONG_DELAY 750
#endif

// Serial command buffer
#define CMD_BUFFER_SIZE 64
char cmdBuffer[CMD_BUFFER_SIZE];
uint8_t cmdPos = 0;

// =============================================================================
// Function declarations
// =============================================================================

void scanFiles();
void printFileList();
void playFileByIndex(uint8_t index);
void playFileByName(const char* name);
void processCommand(const char* cmd);
void printHelp();
void printInfo();
void printError(const char* msg, const char* detail = nullptr);

#ifndef AVR_NO_PLAYLISTS
  void scanPlaylists();
  bool loadPlaylist(const char* name);
  void startPlaylist();
  void playNextInPlaylist();
  void shufflePlaylist();
  int8_t findFileIndex(const char* name);
#endif

// =============================================================================
// Setup
// =============================================================================

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
    // Wait for serial (with timeout for non-USB boards)
  }

  Serial.println(F(""));
  Serial.println(F("========================================"));
  Serial.println(F("  Genesis Engine SD Card Player"));
  Serial.println(F("========================================"));
  Serial.println(F(""));

  // Initialize the Genesis board
  board.begin();
  Serial.println(F("Genesis board initialized"));

  // Initialize SD card
  Serial.print(F("Initializing SD card (CS="));
  Serial.print(PIN_SD_CS);
  Serial.print(F(")... "));

  if (!SD.begin(PIN_SD_CS)) {
    Serial.println(F("FAILED!"));
    Serial.println(F(""));
    Serial.println(F("ERROR: SD card not found"));
    Serial.println(F("Please check:"));
    Serial.println(F("  1. SD card is inserted"));
    Serial.println(F("  2. SD card is FAT16/FAT32 formatted"));
    Serial.println(F("  3. Wiring is correct"));
#if defined(PLATFORM_AVR)
    Serial.println(F("  4. CS pin matches your SD module"));
#endif
    Serial.println(F(""));
    Serial.println(F("System halted."));
    while (1) { delay(1000); }
  }
  Serial.println(F("OK!"));

  // Scan for VGM files
  scanFiles();
#ifndef AVR_NO_PLAYLISTS
  scanPlaylists();

  // Check for auto-start playlist
  if (SD.exists("/auto.txt")) {
    Serial.println(F(""));
    Serial.println(F("Found auto.txt - starting auto playlist..."));
    if (loadPlaylist("auto")) {
      startPlaylist();
      return;  // Skip the prompt
    }
  }
#endif

  Serial.println(F(""));
  Serial.println(F("Type 'help' for commands, 'list' to see files"));
  Serial.println(F(""));
  Serial.print(F("> "));
}

// =============================================================================
// Main Loop
// =============================================================================

void loop() {
  // Update player (timing critical)
  player.update();

  // Check for serial commands
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (cmdPos > 0) {
        cmdBuffer[cmdPos] = '\0';
        Serial.println();  // Echo newline
        processCommand(cmdBuffer);
        Serial.print(F("> "));
        cmdPos = 0;
      }
    } else if (c == '\b' || c == 127) {  // Backspace
      if (cmdPos > 0) {
        cmdPos--;
        Serial.print(F("\b \b"));  // Erase character
      }
    } else if (cmdPos < CMD_BUFFER_SIZE - 1 && c >= 32) {
      cmdBuffer[cmdPos++] = c;
      Serial.print(c);  // Echo character
    }
  }

#ifndef AVR_NO_PLAYLISTS
  // Playlist: monitor for loop events to track play count
  if (playlistActive && player.isPlaying()) {
    uint16_t loopCount = player.getLoopCount();
    if (loopCount > lastLoopCount) {
      lastLoopCount = loopCount;
      // A loop occurred - this counts as completing a play
      uint8_t targetPlays = playlistPlays[playlistPos];
      currentPlays++;
      if (currentPlays >= targetPlays) {
        // Done with this track, advance to next
        player.stop();
        playNextInPlaylist();
      } else {
        // Still more plays needed
        Serial.print(F("[Playlist: track "));
        Serial.print(playlistPos + 1);
        Serial.print(F("/"));
        Serial.print(playlistSize);
        Serial.print(F(", play "));
        Serial.print(currentPlays + 1);
        Serial.print(F("/"));
        Serial.print(targetPlays);
        Serial.println(F("]"));
      }
    }
  }
#endif

  // Show status when playback finishes
  static bool wasPlaying = false;
  if (wasPlaying && player.isFinished()) {
#ifndef AVR_NO_PLAYLISTS
    if (playlistActive) {
      // Playlist mode: advance to next track
      playNextInPlaylist();
    } else
#endif
    {
      Serial.println(F("Playback finished"));
      Serial.print(F("> "));
    }
    wasPlaying = false;
  } else if (player.isPlaying()) {
    wasPlaying = true;
  }
}

// =============================================================================
// File Management
// =============================================================================

void scanFiles() {
  Serial.print(F("Scanning for VGM files... "));
  fileCount = 0;

  File root = SD.open("/");
  if (!root) {
    Serial.println(F("Failed to open root directory"));
    return;
  }

  while (fileCount < MAX_FILES) {
    File entry = root.openNextFile();
    if (!entry) break;

    if (!entry.isDirectory()) {
      const char* name = entry.name();
      size_t len = strlen(name);

      // Check for .vgm or .vgz extension (case insensitive)
      if (len >= 4) {
        const char* ext = name + len - 4;
        bool isVGM = (strcasecmp(ext, ".vgm") == 0);
        bool isVGZ = (strcasecmp(ext, ".vgz") == 0);

        if (isVGM || isVGZ) {
          // On AVR, warn about VGZ but still list it
          strncpy(fileList[fileCount], name, MAX_FILENAME_LEN - 1);
          fileList[fileCount][MAX_FILENAME_LEN - 1] = '\0';
          fileCount++;
        }
      }
    }
    entry.close();
  }
  root.close();

  Serial.print(fileCount);
  Serial.println(F(" files found"));
}

#ifndef AVR_NO_PLAYLISTS
void scanPlaylists() {
  Serial.print(F("Scanning for playlists... "));
  playlistFileCount = 0;

  File root = SD.open("/");
  if (!root) {
    Serial.println(F("0 found"));
    return;
  }

  char line[12];  // Just need to read "#PLAYLIST"

  while (playlistFileCount < MAX_PLAYLISTS) {
    File entry = root.openNextFile();
    if (!entry) break;

    if (!entry.isDirectory()) {
      const char* name = entry.name();
      size_t len = strlen(name);

      // Check for .txt extension
      if (len >= 4 && strcasecmp(name + len - 4, ".txt") == 0) {
        // Check if it starts with #PLAYLIST
        size_t bytesRead = entry.readBytes(line, 9);
        line[bytesRead] = '\0';

        if (strcmp(line, "#PLAYLIST") == 0) {
          // Store name without .txt extension
          size_t nameLen = len - 4;
          if (nameLen >= MAX_FILENAME_LEN) nameLen = MAX_FILENAME_LEN - 1;
          strncpy(playlistFiles[playlistFileCount], name, nameLen);
          playlistFiles[playlistFileCount][nameLen] = '\0';
          playlistFileCount++;
        }
      }
    }
    entry.close();
  }
  root.close();

  Serial.print(playlistFileCount);
  Serial.println(F(" found"));
}
#endif

void printFileList() {
  Serial.println(F(""));

  if (fileCount == 0) {
    Serial.println(F("No VGM files found on SD card"));
    Serial.println(F("Place .vgm files in the root of the SD card"));
#if !GENESIS_ENGINE_USE_VGZ
    Serial.println(F("Note: This board only supports .vgm (not .vgz)"));
    Serial.println(F("Use: python vgm_prep.py file.vgz -o file.vgm"));
#endif
  } else {
    Serial.println(F("Songs:"));
    Serial.println(F("------"));

    for (uint8_t i = 0; i < fileCount; i++) {
      Serial.print(F("  "));
      if (i < 9) Serial.print(' ');  // Align single digits
      Serial.print(i + 1);
      Serial.print(F(". "));
      Serial.print(fileList[i]);

      // Mark currently playing file
      if (i == currentFileIndex && player.isPlaying()) {
        Serial.print(F(" [PLAYING]"));
      }

      // Warn about VGZ on AVR
#if !GENESIS_ENGINE_USE_VGZ
      size_t len = strlen(fileList[i]);
      if (len >= 4 && strcasecmp(fileList[i] + len - 4, ".vgz") == 0) {
        Serial.print(F(" (unsupported)"));
      }
#endif

      Serial.println();
    }
  }

#ifndef AVR_NO_PLAYLISTS
  // Show playlists (numbered after songs)
  if (playlistFileCount > 0) {
    Serial.println(F(""));
    Serial.println(F("Playlists:"));
    Serial.println(F("----------"));

    for (uint8_t i = 0; i < playlistFileCount; i++) {
      uint8_t num = fileCount + i + 1;
      Serial.print(F("  "));
      if (num < 10) Serial.print(' ');  // Align single digits
      Serial.print(num);
      Serial.print(F(". "));
      Serial.print(playlistFiles[i]);

      // Mark if this is the auto playlist
      if (strcasecmp(playlistFiles[i], "auto") == 0) {
        Serial.print(F(" [auto-start]"));
      }

      Serial.println();
    }
  }
#endif

  Serial.println(F(""));
}

// =============================================================================
// Playback Control
// =============================================================================

void playFileByIndex(uint8_t index) {
  if (index >= fileCount) {
    printError("Invalid file number");
    return;
  }

  playFileByName(fileList[index]);
  currentFileIndex = index;
}

void playFileByName(const char* name) {
  // Build full path
  char path[MAX_FILENAME_LEN + 2];
  path[0] = '/';
  strncpy(path + 1, name, MAX_FILENAME_LEN);
  path[MAX_FILENAME_LEN + 1] = '\0';

  Serial.print(F("Playing: "));
  Serial.println(name);

  // Check for VGZ on unsupported platform
#if !GENESIS_ENGINE_USE_VGZ
  size_t len = strlen(name);
  if (len >= 4 && strcasecmp(name + len - 4, ".vgz") == 0) {
    Serial.println(F(""));
    Serial.println(F("ERROR: VGZ files not supported on this board"));
    Serial.println(F(""));
    Serial.println(F("Please decompress the file first:"));
    Serial.print(F("  python vgm_prep.py "));
    Serial.print(name);
    Serial.println(F(" -o output.vgm"));
    Serial.println(F(""));
    return;
  }
#endif

  if (!player.playFile(path)) {
    printError("Failed to play file", name);
    return;
  }

  // Show file info
  Serial.print(F("  Duration: "));
  uint32_t dur = (uint32_t)player.getDurationSeconds();
  Serial.print(dur / 60);
  Serial.print(':');
  if (dur % 60 < 10) Serial.print('0');
  Serial.println(dur % 60);

  if (player.hasYM2612()) Serial.println(F("  Chips: YM2612 (FM)"));
  if (player.hasSN76489()) Serial.println(F("  Chips: SN76489 (PSG)"));
  if (player.hasLoop()) Serial.println(F("  Loop: Yes"));

  Serial.println(F(""));
}

#ifndef AVR_NO_PLAYLISTS
// =============================================================================
// Playlist Functions
// =============================================================================

int8_t findFileIndex(const char* name) {
  for (uint8_t i = 0; i < fileCount; i++) {
    if (strcasecmp(fileList[i], name) == 0) {
      return i;
    }
  }
  return -1;
}

void shufflePlaylist() {
  // Fisher-Yates shuffle
  // Seed random from analog pin for entropy
  randomSeed(analogRead(A0) ^ micros());

  for (uint8_t i = playlistSize - 1; i > 0; i--) {
    uint8_t j = random(i + 1);
    // Swap indices
    uint8_t tmpIdx = playlistIndices[i];
    playlistIndices[i] = playlistIndices[j];
    playlistIndices[j] = tmpIdx;
    // Swap play counts
    uint8_t tmpPlays = playlistPlays[i];
    playlistPlays[i] = playlistPlays[j];
    playlistPlays[j] = tmpPlays;
  }
}

bool loadPlaylist(const char* name) {
  // Build path: /<name>.txt
  char path[MAX_FILENAME_LEN + 6];
  snprintf(path, sizeof(path), "/%s.txt", name);

  File playlistFile = SD.open(path);
  if (!playlistFile) {
    printError("Playlist not found", name);
    return false;
  }

  // Reset playlist state
  playlistSize = 0;
  playlistPos = 0;
  playlistShuffle = false;
  playlistLoop = false;
  playlistActive = false;

  // Check for #PLAYLIST header
  char line[MAX_FILENAME_LEN + 8];
  if (!playlistFile.available()) {
    playlistFile.close();
    printError("Empty playlist file");
    return false;
  }

  // Read first non-empty line
  bool foundHeader = false;
  while (playlistFile.available() && !foundHeader) {
    size_t len = playlistFile.readBytesUntil('\n', line, sizeof(line) - 1);
    line[len] = '\0';
    // Trim CR if present
    if (len > 0 && line[len - 1] == '\r') line[len - 1] = '\0';
    // Skip empty lines
    if (line[0] != '\0') {
      if (strcmp(line, "#PLAYLIST") == 0) {
        foundHeader = true;
      } else {
        playlistFile.close();
        printError("Not a playlist (missing #PLAYLIST header)");
        return false;
      }
    }
  }

  if (!foundHeader) {
    playlistFile.close();
    printError("Not a playlist (missing #PLAYLIST header)");
    return false;
  }

  Serial.print(F("Loading playlist: "));
  Serial.println(name);

  // Parse remaining lines
  while (playlistFile.available() && playlistSize < MAX_PLAYLIST_SIZE) {
    size_t len = playlistFile.readBytesUntil('\n', line, sizeof(line) - 1);
    line[len] = '\0';
    // Trim CR if present
    if (len > 0 && line[len - 1] == '\r') line[len - 1] = '\0';

    // Skip empty lines
    if (line[0] == '\0') continue;

    // Handle comments
    if (line[0] == '#') continue;

    // Handle directives
    if (line[0] == ':') {
      if (strcasecmp(line, ":shuffle") == 0) {
        playlistShuffle = true;
        Serial.println(F("  Shuffle: ON"));
      } else if (strcasecmp(line, ":loop") == 0) {
        playlistLoop = true;
        Serial.println(F("  Loop: ON"));
      }
      continue;
    }

    // Parse track line: filename or filename,plays
    char* comma = strchr(line, ',');
    uint8_t plays = 1;
    if (comma) {
      *comma = '\0';
      plays = atoi(comma + 1);
      if (plays == 0) plays = 1;
    }

    // Find file in our file list
    int8_t idx = findFileIndex(line);
    if (idx < 0) {
      Serial.print(F("  Warning: file not found: "));
      Serial.println(line);
      continue;
    }

    playlistIndices[playlistSize] = idx;
    playlistPlays[playlistSize] = plays;
    playlistSize++;
  }

  playlistFile.close();

  if (playlistSize == 0) {
    printError("Playlist has no valid tracks");
    return false;
  }

  Serial.print(F("  Loaded "));
  Serial.print(playlistSize);
  Serial.println(F(" tracks"));

  // Shuffle if requested
  if (playlistShuffle) {
    shufflePlaylist();
  }

  return true;
}

void startPlaylist() {
  if (playlistSize == 0) return;

  playlistActive = true;
  playlistPos = 0;
  currentPlays = 0;
  lastLoopCount = 0;

  // Start first track
  uint8_t fileIdx = playlistIndices[0];
  uint8_t targetPlays = playlistPlays[0];

  playFileByIndex(fileIdx);

  // Enable looping if we need to play more than once (must be after file loads)
  player.setLooping(targetPlays > 1 && player.hasLoop());

  Serial.print(F("[Playlist: track 1/"));
  Serial.print(playlistSize);
  if (targetPlays > 1) {
    Serial.print(F(", play 1/"));
    Serial.print(targetPlays);
  }
  Serial.println(F("]"));
}

void playNextInPlaylist() {
  if (!playlistActive || playlistSize == 0) return;

  uint8_t targetPlays = playlistPlays[playlistPos];
  currentPlays++;

  // Check if we need to play this track again
  if (currentPlays < targetPlays) {
    // Track will loop automatically (looping is already enabled)
    Serial.print(F("[Playlist: track "));
    Serial.print(playlistPos + 1);
    Serial.print(F("/"));
    Serial.print(playlistSize);
    Serial.print(F(", play "));
    Serial.print(currentPlays + 1);
    Serial.print(F("/"));
    Serial.print(targetPlays);
    Serial.println(F("]"));
    return;
  }

  // Move to next track
  playlistPos++;
  currentPlays = 0;
  lastLoopCount = 0;

  // Check if playlist is complete
  if (playlistPos >= playlistSize) {
    if (playlistLoop) {
      // Restart playlist
      playlistPos = 0;
      Serial.println(F("[Playlist: restarting]"));
    } else {
      // Playlist finished
      playlistActive = false;
      Serial.println(F("[Playlist: finished]"));
      return;
    }
  }

  // Brief pause between songs
  delay(PLAYLIST_SONG_DELAY);

  // Play next track
  uint8_t fileIdx = playlistIndices[playlistPos];
  targetPlays = playlistPlays[playlistPos];

  playFileByIndex(fileIdx);

  // Enable looping if we need to play more than once (must be after file loads)
  player.setLooping(targetPlays > 1 && player.hasLoop());

  Serial.print(F("[Playlist: track "));
  Serial.print(playlistPos + 1);
  Serial.print(F("/"));
  Serial.print(playlistSize);
  if (targetPlays > 1) {
    Serial.print(F(", play 1/"));
    Serial.print(targetPlays);
  }
  Serial.println(F("]"));
}
#endif // AVR_NO_PLAYLISTS

// =============================================================================
// Command Processing
// =============================================================================

void processCommand(const char* cmd) {
  // Skip leading whitespace
  while (*cmd == ' ') cmd++;

  // Empty command
  if (*cmd == '\0') return;

  // Parse command
  if (strcasecmp(cmd, "help") == 0 || strcmp(cmd, "?") == 0) {
    printHelp();
  }
  else if (strcasecmp(cmd, "list") == 0 || strcasecmp(cmd, "ls") == 0) {
    printFileList();
  }
  else if (strcasecmp(cmd, "stop") == 0) {
    player.stop();
#ifndef AVR_NO_PLAYLISTS
    playlistActive = false;  // Stop also exits playlist mode
#endif
    Serial.println(F("Stopped"));
  }
  else if (strcasecmp(cmd, "pause") == 0) {
    if (player.isPaused()) {
      player.resume();
      Serial.println(F("Resumed"));
    } else if (player.isPlaying()) {
      player.pause();
      Serial.println(F("Paused"));
    } else {
      Serial.println(F("Nothing playing"));
    }
  }
  else if (strcasecmp(cmd, "loop") == 0) {
    player.setLooping(!player.isLooping());
    Serial.print(F("Loop: "));
    Serial.println(player.isLooping() ? F("ON") : F("OFF"));
  }
  else if (strcasecmp(cmd, "info") == 0) {
    printInfo();
  }
  else if (strcasecmp(cmd, "next") == 0) {
#ifndef AVR_NO_PLAYLISTS
    if (playlistActive) {
      // In playlist mode: advance to next track
      player.stop();
      playlistPos++;
      currentPlays = 0;
      lastLoopCount = 0;
      if (playlistPos >= playlistSize) {
        if (playlistLoop) {
          playlistPos = 0;
        } else {
          playlistActive = false;
          Serial.println(F("[Playlist: finished]"));
          return;
        }
      }
      delay(PLAYLIST_SONG_DELAY);
      uint8_t fileIdx = playlistIndices[playlistPos];
      uint8_t targetPlays = playlistPlays[playlistPos];
      playFileByIndex(fileIdx);
      player.setLooping(targetPlays > 1 && player.hasLoop());
      Serial.print(F("[Playlist: track "));
      Serial.print(playlistPos + 1);
      Serial.print(F("/"));
      Serial.print(playlistSize);
      Serial.println(F("]"));
    } else
#endif
    if (currentFileIndex < fileCount - 1) {
      playFileByIndex(currentFileIndex + 1);
    } else {
      Serial.println(F("Already at last file"));
    }
  }
  else if (strcasecmp(cmd, "prev") == 0) {
#ifndef AVR_NO_PLAYLISTS
    if (playlistActive) {
      // In playlist mode: go to previous track
      player.stop();
      if (playlistPos > 0) {
        playlistPos--;
      } else if (playlistLoop) {
        playlistPos = playlistSize - 1;
      } else {
        Serial.println(F("Already at first track"));
        return;
      }
      currentPlays = 0;
      lastLoopCount = 0;
      delay(PLAYLIST_SONG_DELAY);
      uint8_t fileIdx = playlistIndices[playlistPos];
      uint8_t targetPlays = playlistPlays[playlistPos];
      playFileByIndex(fileIdx);
      player.setLooping(targetPlays > 1 && player.hasLoop());
      Serial.print(F("[Playlist: track "));
      Serial.print(playlistPos + 1);
      Serial.print(F("/"));
      Serial.print(playlistSize);
      Serial.println(F("]"));
    } else
#endif
    if (currentFileIndex > 0) {
      playFileByIndex(currentFileIndex - 1);
    } else {
      Serial.println(F("Already at first file"));
    }
  }
  else if (strcasecmp(cmd, "rescan") == 0) {
    scanFiles();
#ifndef AVR_NO_PLAYLISTS
    scanPlaylists();
#endif
    printFileList();
  }
  else if (strncasecmp(cmd, "play ", 5) == 0) {
    const char* arg = cmd + 5;
    while (*arg == ' ') arg++;  // Skip spaces

    // Check if argument is a number
    bool isNumber = true;
    for (const char* p = arg; *p && isNumber; p++) {
      if (*p < '0' || *p > '9') isNumber = false;
    }

    if (isNumber && *arg) {
      int num = atoi(arg);
      if (num >= 1 && num <= fileCount) {
        playFileByIndex(num - 1);
      }
#ifndef AVR_NO_PLAYLISTS
      else if (num > fileCount && num <= fileCount + playlistFileCount) {
        // It's a playlist number
        int pIdx = num - fileCount - 1;
        if (loadPlaylist(playlistFiles[pIdx])) {
          startPlaylist();
        }
      }
#endif
      else {
        printError("Invalid number");
        Serial.print(F("Valid range: 1-"));
        Serial.println(fileCount);
      }
    } else if (*arg) {
      // Try to play by filename
      playFileByName(arg);
    } else {
      printError("Usage: play <number> or play <filename>");
    }
  }
#ifndef AVR_NO_PLAYLISTS
  else if (strncasecmp(cmd, "playlist ", 9) == 0) {
    const char* arg = cmd + 9;
    while (*arg == ' ') arg++;  // Skip spaces
    if (*arg) {
      if (loadPlaylist(arg)) {
        startPlaylist();
      }
    } else {
      printError("Usage: playlist <name> (loads <name>.txt)");
    }
  }
  else if (strcasecmp(cmd, "playlist") == 0) {
    printError("Usage: playlist <name> (loads <name>.txt)");
  }
#endif
  else {
    // Try to interpret as a number for quick play
    bool isNumber = true;
    for (const char* p = cmd; *p && isNumber; p++) {
      if (*p < '0' || *p > '9') isNumber = false;
    }

    if (isNumber && *cmd) {
      int num = atoi(cmd);
      if (num >= 1 && num <= fileCount) {
        playFileByIndex(num - 1);
      }
#ifndef AVR_NO_PLAYLISTS
      else if (num > fileCount && num <= fileCount + playlistFileCount) {
        // It's a playlist number
        int pIdx = num - fileCount - 1;
        if (loadPlaylist(playlistFiles[pIdx])) {
          startPlaylist();
        }
      }
#endif
      else {
        printError("Unknown command", cmd);
        Serial.println(F("Type 'help' for available commands"));
      }
    } else {
      printError("Unknown command", cmd);
      Serial.println(F("Type 'help' for available commands"));
    }
  }
}

// =============================================================================
// Display Functions
// =============================================================================

void printHelp() {
  Serial.println(F(""));
  Serial.println(F("Commands:"));
  Serial.println(F("---------"));
  Serial.println(F("  list            List VGM files on SD card"));
  Serial.println(F("  play <n>        Play file by number"));
  Serial.println(F("  play <name>     Play file by name"));
#ifndef AVR_NO_PLAYLISTS
  Serial.println(F("  playlist <name> Play playlist (<name>.txt)"));
#endif
  Serial.println(F("  stop            Stop playback"));
  Serial.println(F("  pause           Pause/resume playback"));
  Serial.println(F("  next            Next file"));
  Serial.println(F("  prev            Previous file"));
  Serial.println(F("  loop            Toggle loop mode"));
  Serial.println(F("  info            Show current track info"));
  Serial.println(F("  rescan          Rescan SD card for files"));
  Serial.println(F("  help            Show this help"));
  Serial.println(F(""));
  Serial.println(F("Tip: Just type a number to play that file"));
  Serial.println(F(""));
}

void printInfo() {
  Serial.println(F(""));
  if (player.isStopped() || player.isFinished()) {
    Serial.println(F("Status: Stopped"));
  } else if (player.isPaused()) {
    Serial.println(F("Status: Paused"));
  } else if (player.isPlaying()) {
    Serial.println(F("Status: Playing"));
  }

  if (currentFileIndex >= 0 && currentFileIndex < fileCount) {
    Serial.print(F("File: "));
    Serial.println(fileList[currentFileIndex]);
  }

  if (player.isPlaying() || player.isPaused()) {
    Serial.print(F("Position: "));
    uint32_t pos = (uint32_t)player.getPositionSeconds();
    uint32_t dur = (uint32_t)player.getDurationSeconds();
    Serial.print(pos / 60);
    Serial.print(':');
    if (pos % 60 < 10) Serial.print('0');
    Serial.print(pos % 60);
    Serial.print(F(" / "));
    Serial.print(dur / 60);
    Serial.print(':');
    if (dur % 60 < 10) Serial.print('0');
    Serial.println(dur % 60);
  }

#ifndef AVR_NO_PLAYLISTS
  if (playlistActive) {
    Serial.print(F("Playlist: track "));
    Serial.print(playlistPos + 1);
    Serial.print(F("/"));
    Serial.print(playlistSize);
    uint8_t targetPlays = playlistPlays[playlistPos];
    if (targetPlays > 1) {
      Serial.print(F(", play "));
      Serial.print(currentPlays + 1);
      Serial.print(F("/"));
      Serial.print(targetPlays);
    }
    if (playlistShuffle) Serial.print(F(" [shuffle]"));
    if (playlistLoop) Serial.print(F(" [loop]"));
    Serial.println();
  } else
#endif
  {
    Serial.print(F("Loop: "));
    Serial.println(player.isLooping() ? F("ON") : F("OFF"));
  }
  Serial.println(F(""));
}

void printError(const char* msg, const char* detail) {
  Serial.print(F("ERROR: "));
  Serial.print(msg);
  if (detail) {
    Serial.print(F(": "));
    Serial.print(detail);
  }
  Serial.println();
}
