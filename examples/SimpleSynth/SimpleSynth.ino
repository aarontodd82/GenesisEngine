/**
 * SimpleSynth - Direct Chip Control Demo
 *
 * Demonstrates using the GenesisEngine synthesis utilities for direct
 * control of the YM2612 (FM) and SN76489 (PSG) chips without VGM playback.
 *
 * This example shows:
 * - Loading FM patches from the default library
 * - Playing notes by MIDI number
 * - PSG tone generation
 * - Basic serial command interface
 *
 * Serial Commands (115200 baud):
 *   n<note>   - Play FM note (e.g., "n60" for middle C)
 *   s         - Stop FM note
 *   p<num>    - Change FM patch (0-7)
 *   t<note>   - Play PSG tone on channel 0
 *   q         - Silence all
 *   ?         - Show help
 *
 * Wiring: Match your GenesisEngine board connections
 */

#include <GenesisBoard.h>
#include <synth/FMPatch.h>
#include <synth/FMFrequency.h>
#include <synth/PSGFrequency.h>
#include <synth/DefaultPatches.h>

// Pin configuration - platform specific
#ifdef ARDUINO_ARCH_ESP32
  const uint8_t PIN_WR_P = 16;  // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 17;  // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 25;  // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 26;  // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 27;  // A1_Y - YM2612 address bit 1 (port select)
  const uint8_t PIN_SCK  = 18;  // SCK  - Hardware SPI (fixed on ESP32)
  const uint8_t PIN_SDI  = 23;  // SDI  - Hardware SPI (fixed on ESP32)
#else
  // Teensy / Arduino defaults
  const uint8_t PIN_WR_P = 2;   // WR_P - SN76489 (PSG) write strobe
  const uint8_t PIN_WR_Y = 3;   // WR_Y - YM2612 write strobe
  const uint8_t PIN_IC_Y = 4;   // IC_Y - YM2612 reset
  const uint8_t PIN_A0_Y = 5;   // A0_Y - YM2612 address bit 0
  const uint8_t PIN_A1_Y = 6;   // A1_Y - YM2612 address bit 1 (port select)
  const uint8_t PIN_SCK  = 13;  // SCK  - Shift register clock (ignored w/ HW SPI)
  const uint8_t PIN_SDI  = 11;  // SDI  - Shift register data (ignored w/ HW SPI)
#endif

GenesisBoard board(PIN_WR_P, PIN_WR_Y, PIN_IC_Y, PIN_A0_Y, PIN_A1_Y, PIN_SCK, PIN_SDI);

// Current state
FMPatch currentPatch;
uint8_t currentPatchNum = 0;
uint8_t currentFMNote = 0;
bool fmNoteOn = false;

// Serial input buffer
char inputBuffer[32];
uint8_t inputPos = 0;

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {
        // Wait for serial connection (with timeout for non-USB boards)
    }

    // Initialize hardware
    board.begin();
    board.reset();

    // Load the first default patch
    loadPatch(0);

    Serial.println(F("SimpleSynth - GenesisEngine Direct Control Demo"));
    Serial.println(F("Commands: n<note> s p<num> t<note> q ?"));
    Serial.println(F("Ready!"));
    Serial.print(F("> "));
}

void loop() {
    while (Serial.available()) {
        char c = Serial.read();

        // Handle line ending - process command
        if (c == '\n' || c == '\r') {
            Serial.println();  // Echo newline
            if (inputPos > 0) {
                inputBuffer[inputPos] = '\0';
                processCommand(inputBuffer);
                inputPos = 0;
            }
            Serial.print(F("> "));  // Prompt for next command
        }
        // Handle backspace
        else if (c == '\b' || c == 127) {
            if (inputPos > 0) {
                inputPos--;
                Serial.print(F("\b \b"));  // Erase character on screen
            }
        }
        // Add to buffer if room
        else if (inputPos < sizeof(inputBuffer) - 1) {
            inputBuffer[inputPos++] = c;
            Serial.print(c);  // Echo character
        }
    }
}

void processCommand(const char* cmd) {
    char command = cmd[0];
    const char* arg = &cmd[1];

    switch (command) {
        case 'n': {
            int note = atoi(arg);
            if (note >= 0 && note <= 127) {
                playFMNote(note);
            }
            break;
        }

        case 's':
            stopFMNote();
            break;

        case 'p': {
            int patch = atoi(arg);
            if (patch >= 0 && patch < DEFAULT_FM_PATCH_COUNT) {
                loadPatch(patch);
            }
            break;
        }

        case 't': {
            int note = atoi(arg);
            if (note >= 0 && note <= 127) {
                playPSGNote(note);
            }
            break;
        }

        case 'q':
            silenceAll();
            break;

        case '?':
            printHelp();
            break;

        default:
            Serial.print(F("Unknown command: "));
            Serial.println(cmd);
            break;
    }
}

void loadPatch(uint8_t patchNum) {
    if (patchNum >= DEFAULT_FM_PATCH_COUNT) return;

    // Copy patch from PROGMEM to RAM
    memcpy_P(&currentPatch, &defaultFMPatches[patchNum], sizeof(FMPatch));

    // Load to FM channel 0
    FMPatchUtils::loadToChannel(board, 0, currentPatch);

    currentPatchNum = patchNum;

    Serial.print(F("Loaded patch "));
    Serial.println(patchNum);
}

void playFMNote(uint8_t note) {
    // Stop previous note if playing
    if (fmNoteOn) {
        FMFrequency::keyOff(board, 0);
    }

    // Set frequency
    FMFrequency::writeToChannel(board, 0, note);

    // Key on
    FMFrequency::keyOn(board, 0);

    currentFMNote = note;
    fmNoteOn = true;

    Serial.print(F("FM note "));
    Serial.println(note);
}

void stopFMNote() {
    if (fmNoteOn) {
        FMFrequency::keyOff(board, 0);
        fmNoteOn = false;
        Serial.println(F("FM note off"));
    }
}

void playPSGNote(uint8_t note) {
    // Play on PSG channel 0 at medium volume
    PSGFrequency::playNote(board, 0, note, 2);

    Serial.print(F("PSG note "));
    Serial.println(note);
}

void silenceAll() {
    // Stop FM
    FMFrequency::keyOff(board, 0);
    fmNoteOn = false;

    // Silence PSG
    board.silencePSG();

    Serial.println(F("Silenced"));
}

void printHelp() {
    Serial.println(F("\n=== SimpleSynth Help ==="));
    Serial.println(F("n<note>  - Play FM note (0-127, 60=middle C)"));
    Serial.println(F("s        - Stop FM note"));
    Serial.println(F("p<num>   - Change FM patch (0-7)"));
    Serial.println(F("t<note>  - Play PSG tone (0-127)"));
    Serial.println(F("q        - Silence all"));
    Serial.println(F("?        - Show this help"));
    Serial.println(F("\nPatches: 0=EP, 1=Bass, 2=Brass, 3=Lead"));
    Serial.println(F("         4=Organ, 5=Strings, 6=Pluck, 7=Bell"));
    Serial.print(F("Current patch: "));
    Serial.println(currentPatchNum);
}
