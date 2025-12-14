# SerialStreaming

Stream VGM files from your PC directly to the Genesis Engine board over USB. No SD card needed - just plug in, run the script, and listen to Sega Genesis music on real hardware.

## Requirements

- Arduino Uno, Mega, Teensy 4.x, or ESP32
- Python 3 with pyserial (`pip install pyserial`)
- USB cable

## Quick Start

1. Upload `SerialStreaming.ino` to your board
2. Run the streamer:

```bash
python stream_vgm.py
```

This launches an interactive wizard that finds VGM files and your board automatically.

## Command Line Usage

```bash
# Basic playback
python stream_vgm.py song.vgm

# Specify port manually
python stream_vgm.py song.vgm --port COM3
python stream_vgm.py song.vgm --port /dev/ttyUSB0

# Loop forever (Ctrl+C to stop)
python stream_vgm.py song.vgm --loop

# Loop 3 times
python stream_vgm.py song.vgm --loop 3

# List available serial ports
python stream_vgm.py --list-ports
```

## DAC/PCM Options

Some VGM files contain sampled audio (drums, voices). These require high throughput and may not play smoothly on slower boards.

```bash
# Reduce DAC sample rate (1=full, 2=half, 4=quarter)
python stream_vgm.py song.vgm --dac-rate 2

# Strip DAC entirely (FM/PSG only, smallest bandwidth)
python stream_vgm.py song.vgm --no-dac
```

By default, Uno/Mega use 1/4 DAC rate automatically. Teensy uses full rate.

## Troubleshooting

**No response from device**
- Make sure the sketch is uploaded
- Check that the baud rate matches (default: 1000000)
- Try unplugging and replugging the USB cable

**Audio glitches or stuttering**
- Try `--dac-rate 2` or `--dac-rate 4`
- Use `--no-dac` for problematic files
- Ensure no other programs are using the serial port

**Wrong port detected**
- Use `--list-ports` to see available ports
- Specify manually with `--port`

---

## How It Works

The system streams VGM commands from your PC to the board in real-time using a custom binary protocol.

### Architecture

```
PC (stream_vgm.py)                    Board (SerialStreaming.ino)
┌──────────────────┐                  ┌──────────────────────────┐
│ Load VGM/VGZ     │                  │                          │
│       ↓          │                  │   Ring Buffer            │
│ Preprocess:      │   1Mbaud USB     │   ┌────────────────┐     │
│ - Inline PCM     │ ═══════════════> │   │ ░░░░░░░▓▓▓▓▓▓ │     │
│ - Optimize waits │   Chunked data   │   └───────┬────────┘     │
│ - Reduce DAC rate│                  │           ↓              │
│       ↓          │                  │   Command Parser         │
│ Chunk + checksum │                  │           ↓              │
│       ↓          │                  │   ┌───────┴───────┐      │
│ Send via serial  │                  │   ↓               ↓      │
└──────────────────┘                  │ YM2612 (FM)   SN76489    │
                                      └──────────────────────────┘
```

### Protocol

Data is sent in checksummed chunks to handle serial errors:

```
[0x01][length][data...][XOR checksum]
```

The board responds with:
- `0x06` (ACK) - chunk received OK, ready for more
- `0x15` (NAK) - bad checksum, please resend

### Timing

VGM files run at 44100 Hz. The board uses `micros()` to schedule commands precisely. Wait commands tell the board how many samples to delay before the next chip write.

### Buffering

The board fills its ring buffer before starting playback:
- Uno: 512 bytes (384 before play)
- Mega: 2KB (1.5KB before play)
- Teensy: 4KB (3KB before play)

This absorbs USB latency jitter and keeps playback smooth.

### DAC Handling

VGM files store PCM samples in a separate data block. The Python script inlines these samples directly into the command stream, so the board doesn't need RAM for sample storage. For slow boards, it skips samples to reduce bandwidth.
