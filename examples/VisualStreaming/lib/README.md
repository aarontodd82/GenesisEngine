# ymfm Library Build Instructions

This directory contains the ymfm FM synthesis library wrapper for Python.

## Pre-built Libraries

Pre-built libraries are provided for common platforms:
- `windows/ymfm.dll` - Windows x64
- `linux/libymfm.so` - Linux x64
- `macos/libymfm.dylib` - macOS (Universal: Intel + Apple Silicon)

## Building from Source

### Prerequisites

- CMake 3.16+
- C++14 compiler:
  - Windows: Visual Studio 2019+ or MinGW-w64
  - Linux: GCC 7+ or Clang 6+
  - macOS: Xcode Command Line Tools

### Clone ymfm

```bash
git clone https://github.com/aaronsgiles/ymfm.git
```

### Build Steps

```bash
mkdir build && cd build
cmake ..
cmake --build . --config Release
```

### Output Location

The built library will be in:
- Windows: `build/Release/ymfm.dll`
- Linux: `build/libymfm.so`
- macOS: `build/libymfm.dylib`

Copy to the appropriate platform subdirectory.

## API Reference

```c
// Create YM2612 emulator instance
// clock: typically 7670453 (NTSC) or 7600489 (PAL)
void* ymfm_create(uint32_t clock);

// Destroy instance
void ymfm_destroy(void* chip);

// Reset chip to initial state
void ymfm_reset(void* chip);

// Write to register
// port: 0 or 1 (corresponds to 0x52/0x53 VGM commands)
// addr: register address
// data: register value
void ymfm_write(void* chip, uint8_t port, uint8_t addr, uint8_t data);

// Generate audio samples (mixed output)
void ymfm_generate(void* chip, int16_t* output, int samples);

// Generate per-channel audio samples
// Each buffer should have space for 'samples' int16_t values
void ymfm_generate_per_channel(
    void* chip,
    int16_t* ch1, int16_t* ch2, int16_t* ch3,
    int16_t* ch4, int16_t* ch5, int16_t* ch6,
    int samples
);

// Get current state of a channel (for visualization hints)
// Returns: 0 if key off, 1 if key on
int ymfm_get_key_state(void* chip, int channel);
```
