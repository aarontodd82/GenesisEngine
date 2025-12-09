/**
 * SDCardPlayer - Play VGM files from SD card with serial menu
 *
 * This example plays VGM files from an SD card and provides an interactive
 * serial menu for file selection, playback control, and playlist support.
 *
 * Hardware:
 *   - FM-90s Genesis Engine board connected to pins defined below
 *   - SD card module (or built-in SD on Teensy 4.1)
 *
 * SD Card Wiring:
 *   Teensy 4.1:  Built-in SD slot (no wiring needed)
 *   Arduino Uno: CS=10, MOSI=11, MISO=12, SCK=13
 *   Arduino Mega: CS=53, MOSI=51, MISO=50, SCK=52
 *   ESP32: CS=5, MOSI=23, MISO=19, SCK=18
 *
 * SD Card Contents:
 *   - Place .vgm files in the root directory
 *   - Optionally create playlist.m3u for playlist mode
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
 *   playlist          Start playlist (plays playlist.m3u)
 *   info              Show current track info
 *   help              Show command list
 *
 * Platform Notes:
 *   - Teensy/ESP32: Full .vgm and .vgz support with PCM/DAC playback
 *   - Arduino Uno/Mega: Only .vgm files supported (not .vgz), limited RAM
 *     for PCM data. Use tools/vgm_prep.py to prepare files:
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
// Pin Configuration
// Adjust these to match your wiring to the FM-90s Genesis Engine board
// =============================================================================

const uint8_t PIN_WR_P = 2;   // WR_P - SN76489 (PSG) write strobe
const uint8_t PIN_WR_Y = 3;   // WR_Y - YM2612 write strobe
const uint8_t PIN_IC_Y = 4;   // IC_Y - YM2612 reset
const uint8_t PIN_A0_Y = 5;   // A0_Y - YM2612 address bit 0
const uint8_t PIN_A1_Y = 6;   // A1_Y - YM2612 address bit 1 (port select)
const uint8_t PIN_SCK  = 7;   // SCK  - Shift register clock (if not using HW SPI)
const uint8_t PIN_SDI  = 8;   // SDI  - Shift register data (if not using HW SPI)

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

// AVR: Fewer files, shorter names to save RAM
// Other platforms: More files, longer names
#if defined(PLATFORM_AVR)
  #define MAX_FILES 20
  #define MAX_FILENAME_LEN 13   // 8.3 format
#else
  #define MAX_FILES 50
  #define MAX_FILENAME_LEN 64   // Long filename support
#endif

char fileList[MAX_FILES][MAX_FILENAME_LEN];
uint8_t fileCount = 0;
int8_t currentFileIndex = -1;

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

  // Show status when playback finishes
  static bool wasPlaying = false;
  if (wasPlaying && player.isFinished()) {
    Serial.println(F("Playback finished"));
    Serial.print(F("> "));
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

void printFileList() {
  if (fileCount == 0) {
    Serial.println(F("No VGM files found on SD card"));
    Serial.println(F(""));
    Serial.println(F("Place .vgm files in the root of the SD card"));
#if !GENESIS_ENGINE_USE_VGZ
    Serial.println(F("Note: This board only supports .vgm (not .vgz)"));
    Serial.println(F("Use: python tools/vgm_prep.py file.vgz -o file.vgm"));
#endif
    return;
  }

  Serial.println(F(""));
  Serial.println(F("Files on SD card:"));
  Serial.println(F("-----------------"));

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
    Serial.print(F("  python tools/vgm_prep.py "));
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
    if (currentFileIndex < fileCount - 1) {
      playFileByIndex(currentFileIndex + 1);
    } else {
      Serial.println(F("Already at last file"));
    }
  }
  else if (strcasecmp(cmd, "prev") == 0) {
    if (currentFileIndex > 0) {
      playFileByIndex(currentFileIndex - 1);
    } else {
      Serial.println(F("Already at first file"));
    }
  }
  else if (strcasecmp(cmd, "rescan") == 0) {
    scanFiles();
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
      } else {
        printError("Invalid file number");
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
  else if (strncasecmp(cmd, "playlist", 8) == 0) {
    // TODO: Implement playlist support
    Serial.println(F("Playlist support coming soon!"));
  }
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
      } else {
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
  Serial.println(F("  list          List VGM files on SD card"));
  Serial.println(F("  play <n>      Play file by number"));
  Serial.println(F("  play <name>   Play file by name"));
  Serial.println(F("  stop          Stop playback"));
  Serial.println(F("  pause         Pause/resume playback"));
  Serial.println(F("  next          Next file"));
  Serial.println(F("  prev          Previous file"));
  Serial.println(F("  loop          Toggle loop mode"));
  Serial.println(F("  info          Show current track info"));
  Serial.println(F("  rescan        Rescan SD card for files"));
  Serial.println(F("  help          Show this help"));
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

  Serial.print(F("Loop: "));
  Serial.println(player.isLooping() ? F("ON") : F("OFF"));
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
