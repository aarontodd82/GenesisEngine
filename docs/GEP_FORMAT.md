# GEP Format Specification (Genesis Engine Packed)

A compact music format optimized for AVR microcontrollers while remaining playable on all platforms.

## Design Goals

1. **Minimize flash usage** - Target 2-4x better compression than VGM
2. **Fast decoding** - No complex algorithms, just lookup tables
3. **No RAM buffering** - Stream directly from PROGMEM
4. **Preserve timing accuracy** - No audible artifacts
5. **DAC support** - Optional PCM samples for drums (can be stripped for smaller size)

## Compression Strategies

Based on VGM analysis:

### 1. Delta Timing (saves ~40% on waits)
Instead of absolute sample counts, use frame-relative timing:
- Base unit: 1/60s frames (735 samples NTSC)
- Sub-frame deltas encoded compactly
- Most waits become 1 byte instead of 3

### 2. Register Dictionary (saves ~30% on FM writes)
- Build dictionary of unique (reg, value) pairs at conversion time
- Commands reference dictionary index (1 byte) instead of reg+value (2 bytes)
- 256 most common pairs get single-byte encoding
- Rare writes use escape + 2 bytes

### 3. Redundant Write Elimination (saves ~20%)
- Skip writes that don't change register state
- Especially effective for timer registers (reg 0x27 in many songs)
- Track register state during conversion

### 4. DAC Compression
- Store PCM samples once in a separate block
- Use delta encoding for PCM (most samples are similar to previous)
- DAC commands reference sample offset
- Optional: 4-bit DPCM for ~50% PCM size reduction
- Converter has --strip-dac option to remove entirely

### 5. PSG Optimization
- PSG commands are already compact (1 byte payload)
- Group consecutive PSG writes with single prefix byte

### 6. Pattern References (for larger songs)
- Identify repeated command sequences
- Store once, reference by ID
- Most effective for looping sections

## File Structure

```
[Header: 16 bytes]
[Dictionary: variable]
[PCM Block: variable, optional]
[Command Stream: variable]
```

### Header (16 bytes)
```
Offset  Size  Description
0x00    4     Magic "GEP\x01" (version 1)
0x04    2     Flags:
                bit 0: has PSG
                bit 1: has YM2612
                bit 2: has DAC/PCM
                bit 3: multi-chunk mode
                bit 4: PCM is 4-bit DPCM encoded
0x06    1     Dictionary entry count (0-255, 0=256)
0x07    1     PCM block count (0-255)
0x08    4     Total duration in samples (at 44100 Hz)
0x0C    2     Loop point chunk index (0xFFFF = no loop)
0x0E    2     Loop point offset within chunk
```

### Dictionary Block
```
[Count: 1 byte] - number of entries (0 means 256)
[Entries: Count * 2 bytes] - pairs of [register, value]
```
- Sorted by frequency during conversion
- Index 0 = most common write

### PCM Block (if has DAC flag set)
```
[Total size: 2 bytes] - total PCM data size
[Data: variable] - raw 8-bit unsigned PCM, or 4-bit DPCM
```
- For DPCM: each byte contains 2 samples (high nibble first)
- DPCM delta table: -8,-4,-2,-1,0,0,1,2,4,8 (approximate, tuned for drums)

### Command Stream

| Byte Range | Command | Description |
|------------|---------|-------------|
| 0x00-0x3F | WAIT_SHORT | Wait 1-64 samples |
| 0x40-0x7F | DICT_WRITE | Write dictionary entry 0-63 (most common) |
| 0x80-0x8F | PSG_MULTI | 1-16 PSG write bytes follow |
| 0x90-0x9F | WAIT_FRAMES | Wait 1-16 frames (735 samples each) |
| 0xA0-0xAF | YM_KEY | Key on/off shortcut for channel 0-11 |
| 0xB0 | DICT_EXT | Extended dict: next byte = entry 64-255 |
| 0xB1 | YM_RAW_P0 | Raw YM port 0 write: [reg] [val] follow |
| 0xB2 | YM_RAW_P1 | Raw YM port 1 write: [reg] [val] follow |
| 0xB3 | PSG_RAW | Single PSG write: [val] follows |
| 0xB4 | WAIT_LONG | Wait N samples: [lo] [hi] follow |
| 0xB5 | LOOP_MARK | Loop point marker |
| 0xB6 | DAC_WRITE | Write DAC sample at current PCM position |
| 0xB7 | DAC_SEEK | Seek PCM position: [lo] [hi] follow |
| 0xB8 | DAC_BLOCK | DAC burst: [count] [wait_per_sample] follow, writes count samples |
| 0xB9-0xBF | Reserved | Future use |
| 0xC0-0xCF | DAC_WAIT | Write DAC + wait 0-15 samples (like VGM 0x80-0x8F) |
| 0xD0-0xDF | Reserved | Future use |
| 0xE0-0xEF | PATTERN | Play pattern 0-15 |
| 0xF0-0xFD | Reserved | Future use |
| 0xFE | CHUNK_END | End of this chunk, continue to next |
| 0xFF | END | End of song |

## DAC Handling

### With DAC (--include-dac or default)
- PCM block stores all unique samples
- DAC_WRITE outputs sample at current seek position, advances by 1
- DAC_SEEK jumps to absolute position in PCM block
- DAC_BLOCK does burst playback (count samples with fixed wait between)
- DAC_WAIT combines write + short wait (common pattern)

### Without DAC (--strip-dac)
- PCM block omitted entirely
- All DAC commands converted to equivalent WAITs
- Significantly smaller file size
- FM and PSG still play perfectly

### DAC Compression Modes

1. **Raw PCM** (default): 8-bit unsigned samples, best quality
2. **4-bit DPCM** (--dpcm): Delta encoding, ~50% size, slight quality loss
   - Good for drums/percussion which are short and noisy anyway

## Multi-Chunk Mode

For songs larger than 32KB (AVR array limit):
- Set multi-chunk flag in header
- Each chunk is a separate PROGMEM array
- Chunk ends with CHUNK_END (0xFE)
- Player loads next chunk when needed
- Converter splits at safe boundaries (after complete frames)

### Chunk Naming
```c
const uint8_t song_gep_0[] PROGMEM = { ... };
const uint8_t song_gep_1[] PROGMEM = { ... };
const uint8_t* const song_gep_chunks[] PROGMEM = { song_gep_0, song_gep_1 };
const uint16_t song_gep_chunk_sizes[] PROGMEM = { sizeof(song_gep_0), sizeof(song_gep_1) };
```

## Encoding Examples

### VGM: Write YM2612 reg 0x28, value 0xF1 (key on ch1)
```
VGM:  52 28 F1     (3 bytes)
GEP:  A1           (1 byte - YM_KEY for channel 1 on)
 or:  42           (1 byte - if dict entry 2 is this write)
```

### VGM: Wait 735 samples (1 NTSC frame)
```
VGM:  62           (1 byte)
GEP:  90           (1 byte - WAIT_FRAMES 1)
```

### VGM: 4 consecutive PSG writes
```
VGM:  50 xx 50 xx 50 xx 50 xx  (8 bytes)
GEP:  83 xx xx xx xx           (5 bytes - PSG_MULTI 4 + data)
```

### VGM: DAC write + wait 3 samples
```
VGM:  83                       (1 byte)
GEP:  C3                       (1 byte - DAC_WAIT 3)
```

## Estimated Compression Ratios

### "Stage 7 - Prarie" (no DAC in original)
- Original VGM: 305 KB
- GEP estimate:
  - 30K redundant reg 0x27 writes → eliminated: -90 KB
  - Dictionary for 66% of writes: -40 KB
  - Wait optimization: -30 KB
  - **Estimated: ~80-100 KB (3-4x compression)**

### "Green Hill Zone" (heavy DAC)
- Original VGM: 509 KB
- With DAC stripped: 37 KB → GEP ~15-20 KB
- With DAC (raw): ~60-80 KB
- With DAC (DPCM): ~40-50 KB

## Platform Considerations

### Arduino Uno (32KB flash, 2KB RAM)
- Single chunk mode only
- ~20-25KB available for music after code
- Best for: Short songs, PSG-only, no DAC

### Arduino Mega (256KB flash, 8KB RAM)
- Multi-chunk mode: up to ~200KB for music
- Full songs with DAC possible
- Best for: Complete Genesis soundtracks

### Teensy 4.x / ESP32
- Huge flash, can use VGM directly if preferred
- GEP still beneficial for very long songs
- Can decompress VGZ on the fly

## Implementation Plan

### Converter (vgm2gep.py)
1. Parse VGM
2. Optional: strip DAC commands
3. Track register state, eliminate redundant writes
4. Collect (reg, value) pairs, build frequency-sorted dictionary
5. Optional: compress PCM with DPCM
6. Encode command stream
7. Split into chunks if needed
8. Generate C header

### Player (GEPPlayer class)
1. ~800 bytes code
2. Dictionary lookup from PROGMEM
3. Multi-chunk support
4. Same timing engine as VGM player
5. DAC support with PCM block streaming

## Version History

- v1: Initial format with DAC support
