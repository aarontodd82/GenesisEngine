# SDCardPlayer

Play VGM files from an SD card with an interactive serial menu. This example turns the Genesis Engine into a standalone music player for Sega Genesis/Mega Drive soundtracks.

## How to Use

1. **Prepare your files**: Place `.vgm` files in the root of a FAT32-formatted SD card. On Arduino Uno/Mega, use `vgm_prep.py` to convert `.vgz` files first:
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
