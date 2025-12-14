# EmulatorBridge

This example turns your Genesis Engine board into a real-time audio output device for the BlastEm emulator. Play any Genesis/Mega Drive game and hear authentic sound through real YM2612 and SN76489 chips.

## Quick Start

1. **Upload the sketch** to your Genesis Engine board using the Arduino IDE or PlatformIO
2. **Download BlastEm** from [https://github.com/aarontodd82/blastem-genesis-engine/releases](https://github.com/aarontodd82/blastem-genesis-engine/releases) (Windows, macOS, and Linux binaries available)
3. **Connect the board** via USB
4. **Run BlastEm** with any ROM: `./blastem game.bin`

If the board is detected, you'll see: "Genesis Engine audio board found, using hardware audio"

That's it - all game audio now plays through the real chips.

## How It Works

When you play a game in BlastEm, the emulator intercepts every write to the YM2612 (FM synth) and SN76489 (PSG) registers. Instead of just emulating the sound, it streams those writes over USB serial to your Genesis Engine board at 1 Mbaud.

The board receives these commands in real-time and writes them to the actual chips. Audio stays in sync with the game since timing comes directly from the emulator.

### Board Compatibility

| Board | Buffer | FM + PSG | DAC/PCM |
|-------|--------|----------|---------|
| Arduino Uno | 512 bytes | Real hardware | Software (from PC) |
| Arduino Mega | 2 KB | Real hardware | Software (from PC) |
| Teensy 4.x | 32 KB | Real hardware | Real hardware |
| ESP32 | 16 KB | Real hardware | Real hardware |

**Arduino Uno/Mega**: FM synthesis and PSG play through real chips. DAC samples (used for voices, drums, sound effects in some games) play through BlastEm's software emulation on your PC. This hybrid approach works because DAC streaming requires more bandwidth than constrained boards can handle.

**Teensy 4.x/ESP32**: Full hardware audio - FM, PSG, and DAC all play through the real chips. BlastEm mutes its software audio completely.

## Troubleshooting

**No hardware detected**
- Check USB connection
- Verify the sketch is uploaded (LED should be on)
- Linux users: add yourself to the `dialout` group

**Audio glitches**
- Don't use fast-forward or turbo modes
- Avoid USB hubs - connect directly
- Close other programs using serial ports

**Notes hang when pausing**
- This is handled automatically - BlastEm silences the hardware when you pause or open menus

## Technical Details

### Protocol

The bridge uses VGM-style commands for chip writes:

| Command | Bytes | Description |
|---------|-------|-------------|
| `0x50 <val>` | 2 | Write to PSG |
| `0x52 <reg> <val>` | 3 | Write to YM2612 Port 0 |
| `0x53 <reg> <val>` | 3 | Write to YM2612 Port 1 |
| `0x61 <lo> <hi>` | 3 | Wait N samples (44.1kHz) |
| `0x62` | 1 | Wait 1/60 sec (NTSC frame) |
| `0x63` | 1 | Wait 1/50 sec (PAL frame) |
| `0x70-0x7F` | 1 | Wait 1-16 samples |
| `0x66` | 1 | End stream / reset |
| `0xAA` | 1 | Ping (connection handshake) |

### Connection Handshake

```
BlastEm                    Board
   |                         |
   |------- 0xAA PING ------>|
   |                         |
   |<-- 0x0F ACK + TYPE -----|
   |<------ 0x06 READY ------|
   |                         |
   |==== streaming begins ===|
```

The board type in the handshake response tells BlastEm which audio mode to use:
- Types 1-2 (Uno/Mega): Hybrid mode - stream FM/PSG to hardware, keep software DAC
- Types 4-5 (Teensy/ESP32): Full hardware mode - stream everything, mute software audio

### Data Flow

```
BlastEm (emulator)              USB Serial (1Mbaud)              Genesis Engine
┌──────────────────┐                                           ┌─────────────────┐
│  68000 CPU write │                                           │                 │
│  to $A04000      │ ──── 0x52 reg val ────────────────────────> Real YM2612    │
│  (YM2612)        │                                           │                 │
│                  │                                           │                 │
│  Z80 write to    │                                           │                 │
│  $7F11 (PSG)     │ ──── 0x50 val ────────────────────────────> Real SN76489   │
└──────────────────┘                                           └─────────────────┘
        │
        └── Wait commands encode timing at 44.1kHz sample resolution
```

### Timing Architecture

BlastEm runs with cycle-accurate emulation and tracks exactly when each register write occurs. It converts these timestamps into VGM-style wait commands:

1. **In BlastEm**: A `serial_bridge.c` module hooks into `ym2612.c` and `psg.c`
2. **Timing accumulation**: Cycles since last write are converted to 44.1kHz samples
3. **Wait encoding**: Uses efficient short waits (`0x70-0x7F`) when possible
4. **On the board**: `micros()` timing ensures writes happen at the correct intervals

The board's ring buffer absorbs USB timing jitter, keeping playback smooth even if USB batches data irregularly.

### Arduino Delay Buffer

On Arduino Uno/Mega, a frame-delay buffer (~67ms) synchronizes hardware FM/PSG with software DAC audio from BlastEm. This ensures both audio sources stay in sync despite different playback latencies.
