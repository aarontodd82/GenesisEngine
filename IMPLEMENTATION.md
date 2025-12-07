# FM-90s Genesis Engine - Implementation Guide

## Project Overview

**Name:** FM-90s Genesis Engine
**Purpose:** A simple, multi-platform Arduino library for playing VGM files on the FM-90s Genesis Engine sound board (YM2612 FM + SN76489 PSG)
**Target Audience:** Hobbyists and makers who want easy VGM playback with minimal configuration

### Supported Systems (VGM Playback)

| System | FM Chip | PSG Chip | Support Level |
|--------|---------|----------|---------------|
| Sega Genesis / Mega Drive | YM2612 | SN76489 | Full |
| Sega Game Gear | - | SN76489 | Full (PSG only) |
| Sega Master System | - | SN76489 | Full (PSG only) |

---

## Goals

1. **Maximum Compatibility** - Works on Arduino Uno through Teensy 4.1
2. **Zero Configuration** - Auto-detects platform and enables appropriate features
3. **Simple API** - Users can play music with minimal code
4. **Self-Contained** - Single library include, no complex dependencies
5. **Progressive Features** - Basic features everywhere, advanced features on capable hardware

---

## Platform Support Matrix

| Feature | Arduino AVR | ESP32 | Teensy 4.x |
|---------|-------------|-------|------------|
| PROGMEM playback | Yes | Yes | Yes |
| Serial streaming | Yes (VGM only) | Yes | Yes |
| SD card playback | With SD module | Yes | Yes (built-in on 4.1) |
| VGZ decompression | No (use tool) | Yes | Yes |
| USB MIDI instrument | No | No | Yes |
| DAC pre-render | No | No | Yes (with Audio Board) |
| Large buffers | No (256B) | Yes (4KB) | Yes (8KB+) |

---

## Architecture Decisions

### Platform Detection
- Automatic via compiler macros (`TEENSYDUINO`, `ARDUINO_ARCH_AVR`, etc.)
- No user configuration required
- Optional override defines if needed

### Pin Configuration
- Runtime configurable via constructor
- User passes their pin numbers, library handles the rest
- No compile-time pin definitions to confuse users

### VGM/VGZ Handling
- **Arduino AVR:** Python tool converts VGZ → VGM → C header (PROGMEM)
- **Arduino AVR Serial:** Requires pre-decompressed VGM files
- **Teensy/ESP32:** Native VGZ decompression using uzlib

### Timing Strategy
- Cooperative `update()` model with `micros()` for basic playback
- Timer interrupts on Teensy for sample-accurate timing
- Smart timing pattern from reference implementation (tracks elapsed time, only waits if needed)

### SD Card
- Conditional compilation - only included if platform supports it
- Uses standard Arduino SD library for compatibility
- Teensy uses `BUILTIN_SDCARD` for 4.1

### Serial Menu
- Optional include (`GenesisEngineMenu.h`)
- Commands: list files, play, stop, pause, next, previous, loop toggle
- Works over any Stream (Serial, Serial1, etc.)

### MIDI Instrument Mode
- Teensy-only (USB MIDI)
- Phase 2 feature
- Maps MIDI notes to YM2612 FM channels

### DAC Pre-render (Phase 2)
- Teensy with Audio Board only
- Pre-renders PCM to temp file for smooth playback
- Based on reference implementation's proven approach

---

## Formats Supported

### VGM (Video Game Music)
- Standard format, widely available
- Large file sizes, especially with DAC
- Best for Teensy/ESP32 with plenty of flash

### GEP (Genesis Engine Packed) - NEW!
- Custom optimized format for AVR
- 2-4x smaller than VGM (sometimes 10-20x for DAC-heavy files)
- Multi-chunk support for songs larger than 32KB
- Same playback quality as VGM
- See `docs/GEP_FORMAT.md` for specification

#### GEP Compression Results

| Song | VGM Size | GEP (no DAC) | GEP (with DAC) |
|------|----------|--------------|----------------|
| Green Hill Zone | 509 KB | 21 KB (4%) | 271 KB (53%) |
| Stage 7 Prarie | 305 KB | 71 KB (23%) | N/A |

---

## Library Structure

```
GenesisEngine/
├── library.properties              # Arduino library metadata
├── keywords.txt                    # Syntax highlighting
├── README.md                       # User documentation
├── IMPLEMENTATION.md               # This file
│
├── docs/
│   └── GEP_FORMAT.md               # GEP format specification
│
├── src/
│   ├── GenesisEngine.h             # Main VGM player
│   ├── GenesisEngine.cpp           # VGM player implementation
│   │
│   ├── GEPPlayer.h                 # GEP format player
│   ├── GEPPlayer.cpp               # GEP player implementation
│   │
│   ├── GenesisBoard.h              # Hardware abstraction
│   ├── GenesisBoard.cpp            # YM2612 + SN76489 driver
│   │
│   ├── VGMParser.h                 # VGM format parsing
│   ├── VGMParser.cpp               # Command processing
│   │
│   ├── VGMCommands.h               # VGM command definitions
│   │
│   ├── sources/
│   │   ├── VGMSource.h             # Abstract base class
│   │   ├── ProgmemSource.h         # Read from PROGMEM
│   │   ├── ProgmemSource.cpp
│   │   ├── StreamSource.h          # Read from any Stream (Serial, File)
│   │   ├── StreamSource.cpp
│   │   ├── SDSource.h              # SD card specific (conditional)
│   │   └── SDSource.cpp
│   │
│   ├── config/
│   │   ├── platform_detect.h       # Auto-detection macros
│   │   └── feature_config.h        # Feature enable/disable logic
│   │
│   ├── menu/
│   │   ├── GenesisEngineMenu.h     # Serial menu (optional include)
│   │   └── GenesisEngineMenu.cpp
│   │
│   └── teensy/                     # Teensy-specific (conditional)
│       ├── VGZDecompressor.h       # uzlib wrapper
│       ├── VGZDecompressor.cpp
│       ├── DACPrerender.h          # DAC pre-rendering (Phase 2)
│       ├── DACPrerender.cpp
│       ├── MidiInstrument.h        # MIDI mode (Phase 2)
│       └── MidiInstrument.cpp
│
├── examples/
│   ├── BasicPlayback/
│   │   └── BasicPlayback.ino       # Simplest example - PROGMEM
│   ├── SDCardPlayer/
│   │   └── SDCardPlayer.ino        # Play from SD card
│   ├── SerialStreaming/
│   │   └── SerialStreaming.ino     # Stream VGM over serial
│   ├── SerialMenu/
│   │   └── SerialMenu.ino          # Interactive menu
│   └── MidiInstrument/
│       └── MidiInstrument.ino      # Teensy USB MIDI
│
└── tools/
    ├── vgm2header.py               # Convert VGM/VGZ to C header (VGM format)
    ├── vgm2gep.py                  # Convert VGM/VGZ to GEP format
    ├── vgm_analyze.py              # Analyze VGM file structure
    └── README.md                   # Tool documentation
```

---

## Reference Implementation Locations

The following files from the MIDI-Player project contain code and patterns to reference:

**Project Root:** `C:\Users\aaron\OneDrive\Documents\PlatformIO\Projects\MIDI-Player`

### Hardware Interface (Genesis Board)

| File | Purpose | Key Code |
|------|---------|----------|
| `src/genesis_board.h` | YM2612 + SN76489 class definition | Pin config, timing constants, method signatures |
| `src/genesis_board.cpp` | Hardware driver implementation | SPI bit-bang, write timing, DAC streaming mode |

**Key Patterns:**
- Smart unified timing with `lastWriteTime_` tracking
- `waitIfNeeded(uint32_t minMicros)` - only delays if necessary
- Bit reversal for SN76489
- DAC streaming mode optimization (latch address once)

**Timing Constants:**
```cpp
YM_BUSY_US = 5      // YM2612 busy flag duration
PSG_BUSY_US = 9     // SN76489 write delay
```

### VGM Parsing & Playback

| File | Purpose | Key Code |
|------|---------|----------|
| `src/vgm_file.h` | VGM format definitions, ChipType enum | Header structure, command bytes |
| `src/vgm_file.cpp` | Streaming parser with gzip support | Loop handling, PCM data blocks, seeking |
| `src/vgm_player.h` | Player state machine | Timing model, command dispatch |
| `src/vgm_player.cpp` | VGM command processing | All chip routing, DAC handling |

**Key Patterns:**
- Sample-accurate timing at 44.1kHz (`MICROS_PER_SAMPLE = 22.675737`)
- Double-precision accumulator for fractional timing (`nextSampleTimeF_`)
- Loop snapshot capture for seamless looping
- PCM data bank management

**VGM Commands Handled:**
- `0x50`: SN76489 PSG write
- `0x52`/`0x53`: YM2612 port 0/1 writes
- `0x61`: Wait N samples
- `0x62`: Wait 735 samples (1/60 sec)
- `0x63`: Wait 882 samples (1/50 sec)
- `0x66`: End of data
- `0x67`: Data block (PCM samples)
- `0x70-0x7F`: Short delays (1-16 samples)
- `0x80-0x8F`: YM2612 DAC + delay combined
- `0xE0`: PCM data bank seek

### DAC Pre-rendering (Phase 2)

| File | Purpose | Key Code |
|------|---------|----------|
| `src/dac_prerender.h` | Pre-renderer class definition | Buffer management |
| `src/dac_prerender.cpp` | PCM extraction and file writing | Scan VGM, extract DAC, write temp file |
| `src/audio_stream_dac_prerender.h` | AudioStream for playback | Ring buffer, sync with VGM timing |
| `src/audio_stream_dac_prerender.cpp` | Stream implementation | `refillBuffer()`, mixing |

**Key Pattern:** Pre-renders all DAC commands to linear 44.1kHz stream, outputs to `/TEMP/~dac.tmp`

### Compression (VGZ)

| File | Purpose | Key Code |
|------|---------|----------|
| `src/vgm_file.cpp` | gzip detection and decompression | Uses uzlib library |

**Pattern:** Check first two bytes for gzip magic (`0x1F 0x8B`), decompress on-the-fly

### Player Architecture

| File | Purpose | Key Code |
|------|---------|----------|
| `src/player_manager.h/cpp` | Lifecycle management | Callback-driven async, single player instance |
| `src/player_config.h` | Dependency injection | All subsystem pointers in one struct |
| `src/playback_state.h/cpp` | Status tracking | STOPPED/PLAYING/PAUSED states |

**Key Patterns:**
- Callback-driven async operations
- Single player instance at a time (saves RAM)
- Clean state machine

### Audio System (Teensy)

| File | Purpose | Key Code |
|------|---------|----------|
| `src/audio_system.h/cpp` | SGTL5000 setup | Mixer gains, effects |
| `src/audio_globals.h` | Extern declarations | Global audio objects |

---

## Serial Menu Commands

```
FM-90s Genesis Engine
=====================
Commands:
  list          - List VGM files on SD card
  play <n>      - Play file number N
  play <name>   - Play file by name
  stop          - Stop playback
  pause         - Pause/resume
  next          - Next track
  prev          - Previous track
  loop          - Toggle loop mode
  info          - Show current track info
  help          - Show this menu
```

---

## API Design

### Minimal Example (PROGMEM)

```cpp
#include <GenesisEngine.h>
#include "music/greenhill.h"

GenesisBoard board(WR_SN, WR_YM, IC_YM, A0_YM, A1_YM, SCK_PIN, SDI_PIN);
GenesisEngine player(board);

void setup() {
  board.begin();
  player.play(greenhill_vgm, sizeof(greenhill_vgm));
}

void loop() {
  player.update();
}
```

### SD Card Example

```cpp
#include <GenesisEngine.h>
#include <SD.h>

GenesisBoard board(WR_SN, WR_YM, IC_YM, A0_YM, A1_YM, SCK_PIN, SDI_PIN);
GenesisEngine player(board);

void setup() {
  SD.begin(BUILTIN_SDCARD);
  board.begin();
  player.playFile("/vgm/sonic.vgm");
}

void loop() {
  player.update();
}
```

### With Menu

```cpp
#include <GenesisEngine.h>
#include <GenesisEngineMenu.h>
#include <SD.h>

GenesisBoard board(WR_SN, WR_YM, IC_YM, A0_YM, A1_YM, SCK_PIN, SDI_PIN);
GenesisEngine player(board);
GenesisEngineMenu menu(player, Serial);

void setup() {
  Serial.begin(115200);
  SD.begin(BUILTIN_SDCARD);
  board.begin();
  menu.begin();
}

void loop() {
  player.update();
  menu.update();
}
```

### Full API

```cpp
class GenesisEngine {
public:
  GenesisEngine(GenesisBoard& board);

  // Playback control
  bool play(const uint8_t* data, size_t length);  // PROGMEM
  bool playFile(const char* path);                 // SD card
  bool playStream(Stream& stream);                 // Serial/other
  void stop();
  void pause();
  void resume();

  // Must call frequently
  void update();

  // Status
  bool isPlaying() const;
  bool isPaused() const;
  bool isFinished() const;

  // Settings
  void setLooping(bool loop);
  bool isLooping() const;

  // Info (if available)
  uint32_t getCurrentSample() const;
  uint32_t getTotalSamples() const;
  const char* getTrackName() const;  // From GD3 tag
};
```

---

## Development Phases

### Phase 1: Core Library & Tools
- [ ] GenesisBoard hardware driver
- [ ] VGM parser (no compression)
- [ ] PROGMEM source
- [ ] Basic timing with `update()`
- [ ] Platform detection
- [ ] vgm2header.py converter (needed for testing)
- [ ] Basic example

### Phase 2: SD Card Support
- [ ] Stream source abstraction
- [ ] SD card source
- [ ] File-based playback
- [ ] Serial menu

### Phase 3: Teensy Enhancements
- [ ] VGZ decompression (uzlib)
- [ ] Larger buffers
- [ ] Timer-based accurate timing
- [ ] Loop handling from reference impl

### Phase 4: Advanced Features
- [ ] MIDI instrument mode
- [ ] GD3 tag parsing
- [ ] Fade out on loop

### Phase 5: Documentation & Polish
- [ ] Web-based converter
- [ ] Comprehensive documentation
- [ ] More examples

### Phase 6: DAC Pre-render (Teensy + Audio Board)
- [ ] DAC pre-rendering system
- [ ] AudioStream integration
- [ ] Temp file management

---

## Technical Notes

### Pin Naming Convention

The FM-90s Genesis Engine board uses the following pin labels:

| Pin | Description |
|-----|-------------|
| WR_P | SN76489 (PSG) write strobe (active low) |
| WR_Y | YM2612 write strobe (active low) |
| IC_Y | YM2612 reset (active low) |
| A0_Y | YM2612 address bit 0 |
| A1_Y | YM2612 address bit 1 (port select) |
| SCK  | Shift register clock (CD74HCT164E) |
| SDI  | Shift register data in |

### YM2612 Write Sequence
1. Set A1_Y (port select: 0 or 1)
2. Set A0_Y = LOW (address mode)
3. Shift out 8-bit register address via SCK/SDI
4. Pulse WR_Y LOW (minimum 1μs)
5. Set A0_Y = HIGH (data mode)
6. Shift out 8-bit data value
7. Pulse WR_Y LOW (triggers BUSY flag, wait 5μs before next write)

### SN76489 Write Sequence
1. Bit-reverse the data byte (hardware wiring quirk)
2. Shift out 8 bits via SCK/SDI
3. Pulse WR_P LOW (minimum 8μs pulse width)
4. Wait 9μs before next write

### VGM Timing
- VGM runs at 44100 Hz sample rate
- 1 sample = 22.675737 microseconds
- Commands specify delays in samples
- Accumulate fractional time to prevent drift

---

## Development vs Distribution

### For Development (Us)

Use PlatformIO with the included `platformio.ini`:

1. Open `GenesisEngine` folder in VS Code with PlatformIO
2. Select target board (mega, teensy41, etc.) from PlatformIO toolbar
3. Edit code in `src/`, test sketches in `examples/`
4. Build and upload directly - no copying files

The `platformio.ini` treats the library source as a local dependency, so changes are immediate.

### For Distribution (End Users)

The same folder works as an Arduino library:

1. User downloads/clones the `GenesisEngine` folder
2. Copies it to their Arduino `libraries/` folder (or installs via ZIP)
3. Opens examples from File → Examples → GenesisEngine
4. The `platformio.ini` file is ignored by Arduino IDE

**Important:** When distributing, do NOT include:
- `tools/test_vgm/*.h` (generated test files - too large)
- `.pio/` folder (PlatformIO build cache)

Keep in the distribution:
- `tools/vgm2header.py` (users need this)
- `tools/vgm_analyze.py` (optional but helpful)
- `tools/test_vgm/README.md` (instructions)

---

## Notes & Reminders

- The FM-90s Genesis Engine board uses a CD74HCT164E shift register for data
- Pins are directly connected (directly directly directly...)
- Reference implementation is battle-tested on Teensy 4.1
- Keep Arduino Uno RAM usage under 1.5KB to leave room for user code
- Test on real hardware frequently during development
