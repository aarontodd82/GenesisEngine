# Genesis Engine

**Authentic Sega Genesis / Mega Drive sound on real hardware.**

The Genesis Engine is a sound board and library that brings the iconic sound of the Sega Genesis to your projects. Featuring the **YM2612 FM synthesizer** and **SN76489 PSG** chips on a board easily controlled by Teensy/Arduino/ESP32

Play VGM music files, stream audio directly from an emulator, or use it as a standalone synthesizer. 

## The Board

The Genesis Engine board design is based on [Stefan Nikolaj's DOS Synth], with several upgrades:

- **Stereo output** — Full left/right channel separation
- **Accurate clocks** — Crystal oscillators tuned for correct pitch
- **FM and PSG in tune** — Both chips share a proper clock relationship
- **Upgraded Op-amp** — Designed for low noise

The `hardware/` directory contains KiCad schematics and PCB files.

## Features

- **Real Chips** — YM2612 FM synthesis and SN76489 PSG, not emulation
- **VGM Playback** — Play music from Genesis, Mega Drive, Game Gear, and Master System
- **Multiple Playback Modes**
  - Flash memory (PROGMEM) for standalone operation
  - SD card for large music libraries
  - Serial streaming from PC
  - Real-time streaming from emulators (BlastEm)
- **Cross-Platform** — Teensy 4.x, ESP32, RP2040, AVR boards, and more
- **Compressed Formats** — Native VGZ support, plus custom GEP format (2-4x smaller)
- **PCM/DAC Support** — Sampled drums and vocals on YM2612 channel 6
- **Smart Memory Management** — Automatically adapts to your board's capabilities

## Supported Systems for VGM

| System | FM Chip | PSG Chip | Support |
|--------|---------|----------|---------|
| Sega Genesis / Mega Drive | YM2612 | SN76489 | Full |
| Sega Master System | — | SN76489 | PSG only |
| Sega Game Gear | — | SN76489 | PSG only |
| ColecoVision | — | SN76489 | PSG only |
| IBM PCjr | — | SN76489 | PSG only |
| Tandy 1000 PCs | — | SN76489 | PSG only |

## Quick Start

### Wiring

Connect the Genesis Engine board to your microcontroller.

| Board Pin | Function | Default | Configurable? |
|-----------|----------|---------|---------------|
| WR_P | PSG write strobe (active low) | 2 | Yes |
| WR_Y | YM2612 write strobe (active low) | 3 | Yes |
| IC_Y | YM2612 reset (active low) | 4 | Yes |
| A0_Y | YM2612 address/data select | 5 | Yes |
| A1_Y | YM2612 port select | 6 | Yes |
| SCK | Shift register clock | SPI | See below |
| SDI | Shift register data (MOSI) | SPI | See below |
| VCC | 5V power | — | — |
| GND | Ground | — | — |

**SPI Pins** — By default, the library uses hardware SPI for fast data transfer. These pins are fixed per board:

| Board | SCK | SDI (MOSI) |
|-------|-----|------------|
| Teensy 4.x | 13 | 11 |
| ESP32 | 18 | 23 |
| Uno | 13 | 11 |
| Mega | 52 | 51 |

If you need different pins for SCK/SDI, set `USE_HARDWARE_SPI` to 0 in `GenesisBoard.cpp` to enable software SPI on any GPIO.

### Basic Usage

```cpp
#include <GenesisEngine.h>
#include "music.h"  // Your VGM converted to a header file

// Define your pin connections
GenesisBoard board(WR_P, WR_Y, IC_Y, A0_Y, A1_Y, SCK, SDI);
GenesisEngine player(board);

void setup() {
    board.begin();
    player.play(music_data, music_length);
    player.setLooping(true);
}

void loop() {
    player.update();  // Call frequently for proper timing
}
```

### SD Card Playback

```cpp
#include <GenesisEngine.h>
#include <SD.h>

GenesisBoard board(WR_P, WR_Y, IC_Y, A0_Y, A1_Y, SCK, SDI);
GenesisEngine player(board);

void setup() {
    SD.begin(BUILTIN_SDCARD);  // Teensy 4.1 built-in SD
    board.begin();
    player.playFile("/music/greenhill.vgm");
}

void loop() {
    player.update();
}
```

## Playback Modes

### Flash Memory (PROGMEM)
Store music directly in your microcontroller's flash. Convert VGM files with the included tool:

```bash
python tools/vgm2header.py song.vgm -o song.h
```

### GEP Format (Compressed)
For memory-constrained boards, the GEP format provides 2-4x compression:

```bash
python tools/vgm2gep.py song.vgm -o song_gep.h
```

### SD Card
Play VGM and VGZ files directly from SD. The SDCardPlayer example includes an interactive serial menu for browsing and playback control.

### Serial Streaming
Stream VGM files from your PC in real-time:

```bash
python tools/stream_vgm.py song.vgm
```

### Emulator Bridge
Stream audio directly from BlastEm or other Genesis emulators to hear games on real hardware as you play.

## Platform Notes

| Feature | Teensy 4.x | ESP32 | AVR |
|---------|------------|-------|-----|
| PROGMEM playback | Yes | Yes | Yes |
| GEP format | Yes | Yes | Yes |
| Serial streaming | Yes | Yes | Yes |
| SD card | Built-in | Yes | External |
| VGZ decompression | Native | Native | Via tools |
| PCM buffer size | 8KB+ | 4KB | 256 bytes |

Teensy 4.x offers the best experience with fast GPIO, large memory, and built-in SD card support on the 4.1.

## Examples

- **BasicPlayback** — VGM playback from flash memory
- **GEPPlayback** — Compressed format for smaller boards
- **SDCardPlayer** — SD card player with serial menu
- **SerialStreaming** — Stream music from PC over USB
- **EmulatorBridge** — Real-time audio from Genesis emulators

## Tools

| Tool | Description |
|------|-------------|
| `vgm2header.py` | Convert VGM/VGZ to C header files |
| `vgm2gep.py` | Convert to compressed GEP format |
| `vgm_prep.py` | Prepare VGM files (inline DAC, reduce sample rate) |
| `stream_vgm.py` | Stream VGM from PC to board |

## License

LGPL-2.1 — See [LICENSE](LICENSE) for details.

## Links

- [GitHub Repository](https://github.com/fm-90s/GenesisEngine)
- [FM-90s](https://fm-90s.com)

---

*Genesis Engine is not affiliated with or endorsed by Sega. Sega Genesis and Mega Drive are trademarks of Sega Corporation.*
