# BasicPlayback Example

Play VGM/VGZ files from flash memory using the Genesis Engine library.

## Quick Start

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

## Pin Configuration

Adjust the pin definitions in the sketch to match your wiring to the Genesis Engine board.
