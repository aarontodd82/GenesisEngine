# BasicPlayback Example

Play VGM/VGZ files from flash memory using the Genesis Engine library.

## Quick Start

1. **Convert your VGM file to a header:**
   ```
   python vgm2header.py your_song.vgz
   ```
   This creates `your_song.h` in the same directory.

2. **Update the sketch:**
   - Change the include to match your file:
     ```cpp
     #include "your_song.h"
     ```
   - Update the play() calls to use your variable name:
     ```cpp
     player.play(your_song_vgm, your_song_vgm_len);
     ```

3. **Upload and enjoy!**

## Script Options

```
python vgm2header.py input.vgz                  # Basic conversion
python vgm2header.py input.vgz --name my_song   # Custom variable name
python vgm2header.py input.vgz --strip-dac      # Remove PCM samples (smaller)
python vgm2header.py input.vgz --platform mega  # Truncate to fit platform
```

Available platforms: `uno`, `mega`, `teensy40`, `teensy41`, `esp32`, `rp2040`

## Pin Configuration

Adjust the pin definitions in the sketch to match your wiring:

```cpp
const uint8_t PIN_WR_P = 2;   // SN76489 write strobe
const uint8_t PIN_WR_Y = 3;   // YM2612 write strobe
const uint8_t PIN_IC_Y = 4;   // YM2612 reset
const uint8_t PIN_A0_Y = 5;   // YM2612 address bit 0
const uint8_t PIN_A1_Y = 6;   // YM2612 address bit 1
const uint8_t PIN_SCK  = 7;   // Shift register clock
const uint8_t PIN_SDI  = 8;   // Shift register data
```

## Notes

- Large VGM files may not fit on AVR boards (Uno/Mega have 32KB per-array limit)
- Use `--strip-dac` to remove PCM samples if you need to reduce size
- Teensy and ESP32 can handle much larger files
