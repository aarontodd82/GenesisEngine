# BasicPlayback Example

Play VGM/VGZ files from flash memory using the Genesis Engine library.

## Quick Start

A sample file `greenhill.vgz` is included. Convert it first:

1. **Convert your VGM file:**
   ```
   python vgm2header.py your_song.vgz
   ```
   The wizard will ask which platform you're using and handle the rest.

2. **Update the sketch:**
   ```cpp
   #include "your_song.h"
   ```
   And update the playChunked() calls:
   ```cpp
   player.playChunked(your_song_chunks, your_song_chunk_sizes,
                      YOUR_SONG_NUM_CHUNKS, YOUR_SONG_TOTAL_LEN);
   ```

3. **Upload and enjoy!**

## Batch Conversion

Skip the wizard with `-p` to specify platform directly:
```
python vgm2header.py *.vgz -p teensy41
```

## Platform Limits

Flash memory limits for VGM data vary by platform:

| Platform | Max VGM Size | Notes |
|----------|--------------|-------|
| **Teensy 4.1** | ~7 MB | Recommended for large files |
| **Teensy 4.0** | ~1.5 MB | |
| **ESP32** | ~2 MB | |
| **RP2040** | ~1.5 MB | |
| **Arduino Mega** | **60 KB** | 16-bit PROGMEM pointer limit |
| **Arduino Uno** | **16 KB** | Very limited flash |

### Arduino Limitations

**Arduino Uno and Mega have severe flash limitations** due to AVR's 16-bit PROGMEM addressing. The converter will automatically truncate files to fit.

For Arduino, use `--strip-dac` to remove PCM samples (drums):
```
python vgm2header.py song.vgz -p mega --strip-dac
```

This often reduces file size by 90%+ while keeping all FM and PSG music. A 500KB VGM typically becomes 30-50KB without DAC.

**For larger music libraries on Arduino, use the SDCardPlayer example instead.**

## Pin Configuration

Adjust the pin definitions in the sketch to match your wiring to the Genesis Engine board.
