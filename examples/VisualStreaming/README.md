# VisualStreaming

Stream VGM files to the Genesis Engine board with real-time per-channel waveform visualization.

![Visualizer Screenshot](screenshot.png)

## Features

- **10 oscilloscope displays**: 6 FM channels + 4 PSG channels
- **Accurate waveforms**: Uses ymfm (same emulator as Furnace Tracker) for FM, pure Python for PSG
- **Cross-platform**: Windows, Linux, macOS
- **Non-blocking**: Visualization never interferes with playback timing
- **Professional look**: Dear ImGui with ImPlot for smooth, GPU-accelerated rendering

## Requirements

- Python 3.9+
- OpenGL 3.3+ compatible graphics
- Genesis Engine board (Arduino/Teensy/ESP32 with YM2612 + SN76489)

## Installation

```bash
# Install Python dependencies
pip install -r requirements.txt
```

## Usage

```bash
# Interactive mode - finds VGM files and serial port automatically
python stream_vgm_visual.py

# Specify file directly
python stream_vgm_visual.py song.vgm

# Specify port
python stream_vgm_visual.py song.vgm --port COM3

# Loop playback
python stream_vgm_visual.py song.vgm --loop
```

## Controls

| Key | Action |
|-----|--------|
| Space | Pause/Resume |
| Escape | Stop and exit |
| 1-6 | Toggle FM channel mute |
| 7-0 | Toggle PSG channel mute |

## How It Works

The visualizer runs in parallel with the streamer:

1. **VGM Parser**: Loads and preprocesses VGM file (same as `stream_vgm.py`)
2. **Command Interceptor**: Captures chip writes as they're streamed
3. **Chip Emulators**: YM2612 (ymfm) and SN76489 (Python) generate per-channel waveforms
4. **GUI Renderer**: ImPlot displays waveforms at 60fps

The actual audio comes from the real hardware - the emulators are only for visualization.

## Building ymfm (if needed)

Pre-built libraries are included for common platforms. If you need to rebuild:

```bash
cd lib
mkdir build && cd build
cmake ..
cmake --build . --config Release
```

Requires CMake 3.16+ and a C++14 compiler.

## Troubleshooting

**"No module named 'imgui_bundle'"**
```bash
pip install imgui-bundle
```

**Black window / OpenGL errors**
- Update your graphics drivers
- Ensure OpenGL 3.3+ is supported

**Waveforms don't match audio**
- This is normal for slight timing differences
- Emulation is approximate, real chips have analog characteristics

## Credits

- **ymfm**: Aaron Giles (BSD license)
- **Dear ImGui**: Omar Cornut (MIT license)
- **ImPlot**: Evan Pezent (MIT license)
- **stream_vgm.py**: Original streaming logic from SerialStreaming example
