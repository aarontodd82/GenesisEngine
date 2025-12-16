# SDCardPlayer

Play VGM files from an SD card with an interactive serial menu. This example turns the Genesis Engine into a standalone music player for Sega Genesis/Mega Drive soundtracks.

## Wiring

### Recommended: Teensy 4.1

Teensy 4.1 provides the best experience with built-in SD card slot, plenty of RAM, and full VGZ support.

### Arduino Uno - NOT SUPPORTED

Only 2KB RAM - not enough for SD library.

### Arduino Mega - Limited Support

Works but results may vary. Software SPI for the shift register is slower than Teensy's hardware SPI, which may affect timing on some VGM files. Use `vgm_prep.py` to prepare files.

The SD card and shift register both use SPI, but the CD74HCT164E shift register has no chip select pin. To avoid conflicts, **the shift register MUST use different pins** than the SD card.

| Genesis Engine Board | Arduino Pin |
|---------------------|-------------|
| WR_P (PSG write)    | 2           |
| WR_Y (YM2612 write) | 3           |
| IC_Y (YM2612 reset) | 4           |
| A0_Y (YM2612 addr)  | 5           |
| A1_Y (YM2612 port)  | 6           |
| **Shift CLK**       | **7** ⚠️    |
| **Shift DATA**      | **8** ⚠️    |

⚠️ **Do NOT connect shift register to pins 51/52!** Those are used by the SD card.

SD card uses Mega's hardware SPI:

| SD Card Module | Arduino Pin |
|----------------|-------------|
| CS             | 53          |
| MOSI           | 51          |
| MISO           | 50          |
| SCK            | 52          |
| VCC            | 5V          |
| GND            | GND         |

**Teensy 4.1:**

Uses the built-in SD card slot - no SD wiring needed. Genesis board can use the default hardware SPI pins since there's no conflict.

### ESP32 - Full Support

ESP32 works great with SD cards, but requires **different shift register pins** than other examples. The SD card needs the hardware SPI bus, and since the shift register has no chip select, they cannot share pins.

⚠️ **IMPORTANT:** The shift register pins are DIFFERENT from SerialStreaming and other examples!

| Genesis Engine Board | ESP32 GPIO |
|---------------------|------------|
| WR_P (PSG write)    | 16         |
| WR_Y (YM2612 write) | 17         |
| IC_Y (YM2612 reset) | 25         |
| A0_Y (YM2612 addr)  | 26         |
| A1_Y (YM2612 port)  | 27         |
| **Shift CLK**       | **4** ⚠️   |
| **Shift DATA**      | **13** ⚠️  |

⚠️ **Do NOT use GPIO 18/23 for shift register!** Those are used by the SD card.

SD card uses ESP32's hardware SPI (VSPI):

| SD Card Module | ESP32 GPIO |
|----------------|------------|
| CS             | 5          |
| MOSI           | 23         |
| MISO           | 19         |
| SCK            | 18         |
| VCC            | 3.3V or 5V (check your module) |
| GND            | GND        |

**PlatformIO users:** Use the `esp32_sd` environment, not `esp32`.

---

## How to Use

1. **Prepare your files**: Place `.vgm` files in the root of a FAT32-formatted SD card. On Arduino Mega, use `vgm_prep.py` to convert `.vgz` files first:
   ```
   python vgm_prep.py song.vgz -o song.vgm
   ```

2. **Upload the sketch**: Open `SDCardPlayer.ino` in the Arduino IDE and upload it to your board.

3. **Open the Serial Monitor**: Go to **Tools > Serial Monitor** (or press `Ctrl+Shift+M`). Set the baud rate to **115200** using the dropdown in the bottom-right corner. You should see a welcome message and a `>` prompt.

4. **Control playback**: Type commands in the input field at the top and press Enter:
   | Command | Action |
   |---------|--------|
   | `list` | Show available VGM files |
   | `play 1` | Play file #1 (or use filename) |
   | `playlist name` | Play a playlist (name.txt) |
   | `stop` | Stop playback |
   | `pause` | Pause/resume |
   | `next` / `prev` | Skip tracks |
   | `loop` | Toggle looping |
   | `info` | Show current track status |
   | `help` | Full command list |

   Tip: You can also just type a number (e.g. `3`) to quickly play that file.

## Playlists

Create a `.txt` file on your SD card starting with `#PLAYLIST`:

```
#PLAYLIST
# My playlist - lines starting with # are comments
#
# Optional settings:
#   :shuffle  - randomize song order
#   :loop     - restart playlist when finished
#
# Songs: one per line, optionally with play count
#   song.vgm      - plays once
#   song.vgm,3    - plays 3 times (loops twice)
#
:shuffle
:loop

greenhill.vgm,2
starlight.vgm
boss.vgm,3
```

**Commands:**
- `playlist mylist` - loads and plays `mylist.txt`

**Auto-start:** Name your playlist `auto.txt` and it will play automatically when the board powers on - no serial connection needed.

## Tech Primer

The sketch creates a `GenesisBoard` (hardware driver) and `GenesisEngine` (player) instance. On startup, it initializes the SD card and scans the root directory for `.vgm`/`.vgz` files, storing up to 50 filenames (20 on AVR).

The main loop does two things: calls `player.update()` to process VGM commands in real-time, and polls the serial port for user input. Commands are parsed in `processCommand()` which maps text input to player API calls like `playFile()`, `pause()`, and `stop()`.

When you play a file, `GenesisEngine` opens it via `SDSource`, parses the VGM header for metadata (duration, loop points, chips used), then streams commands to the hardware. The player uses `micros()` to track elapsed time and processes chip writes at the correct 44100 Hz sample rate.

**Platform differences**: Teensy and ESP32 have enough RAM to decompress VGZ files on-the-fly and buffer PCM samples. AVR boards (Uno/Mega) lack this, so you must pre-process files with `vgm_prep.py`, which decompresses VGZ and inlines DAC samples directly into the command stream at a reduced sample rate.
