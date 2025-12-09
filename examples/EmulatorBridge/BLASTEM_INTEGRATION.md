# BlastEm Integration Guide

This document describes how to modify the BlastEm Genesis/Mega Drive emulator to stream real-time audio register writes to the Genesis Engine hardware board.

## Overview

BlastEm is an open-source, highly accurate Genesis emulator. By adding a serial output module, we can capture YM2612 (FM) and SN76489 (PSG) register writes as they occur during emulation and send them to real hardware for authentic audio playback.

**Key insight**: BlastEm already handles all timing internally through cycle-accurate CPU emulation. We don't need to send timing/wait commands - register writes arrive exactly when they should be executed.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        BlastEm                              │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────────┐  │
│  │  68000   │───>│ Memory   │───>│  YM2612 Emulation    │  │
│  │   CPU    │    │   Bus    │    │  (ym2612.c)          │  │
│  └──────────┘    └──────────┘    └──────────┬───────────┘  │
│                        │                     │              │
│                        │              ┌──────▼───────┐      │
│                        │              │ serial_bridge│      │
│                        ▼              │   (NEW)      │      │
│               ┌──────────────┐        └──────┬───────┘      │
│               │ PSG Emulation│               │              │
│               │  (psg.c)     │───────────────┤              │
│               └──────────────┘               │              │
└──────────────────────────────────────────────┼──────────────┘
                                               │ Serial
                                               │ (1 Mbaud)
                                               ▼
                               ┌───────────────────────────┐
                               │    Genesis Engine Board   │
                               │                           │
                               │  ┌─────────┐ ┌─────────┐  │
                               │  │ YM2612  │ │ SN76489 │  │
                               │  │  (FM)   │ │  (PSG)  │  │
                               │  └─────────┘ └─────────┘  │
                               └───────────────────────────┘
```

## Source Code Location

BlastEm source is available at:
- **Official Mercurial repo**: https://www.retrodev.com/repos/blastem
- **GitHub mirror**: https://github.com/libretro/blastem

## Files to Modify/Create

### 1. New File: `serial_bridge.c`

```c
/**
 * serial_bridge.c - Real-time audio streaming to Genesis Engine hardware
 */

#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include "serial_bridge.h"
#include "config.h"

// Platform-specific serial includes
#ifdef _WIN32
  #include <windows.h>
  static HANDLE serial_handle = INVALID_HANDLE_VALUE;
#else
  #include <fcntl.h>
  #include <termios.h>
  #include <unistd.h>
  #include <dirent.h>
  static int serial_fd = -1;
#endif

// Protocol constants
#define CMD_PING         0x00
#define CMD_ACK          0x0F
#define CMD_PSG_WRITE    0x50
#define CMD_YM2612_PORT0 0x52
#define CMD_YM2612_PORT1 0x53
#define CMD_END_STREAM   0x66
#define FLOW_READY       0x06

// State
static bool bridge_enabled = false;
static bool bridge_connected = false;
static char bridge_port[256] = "";
static uint8_t board_type = 0;

// =============================================================================
// Platform-Specific Serial Implementation
// =============================================================================

#ifdef _WIN32

static bool serial_open(const char *port, int baud) {
    char full_path[256];
    snprintf(full_path, sizeof(full_path), "\\\\.\\%s", port);

    serial_handle = CreateFileA(full_path, GENERIC_READ | GENERIC_WRITE,
                                0, NULL, OPEN_EXISTING, 0, NULL);
    if (serial_handle == INVALID_HANDLE_VALUE) {
        return false;
    }

    DCB dcb = {0};
    dcb.DCBlength = sizeof(dcb);
    GetCommState(serial_handle, &dcb);
    dcb.BaudRate = baud;
    dcb.ByteSize = 8;
    dcb.StopBits = ONESTOPBIT;
    dcb.Parity = NOPARITY;
    dcb.fDtrControl = DTR_CONTROL_ENABLE;
    SetCommState(serial_handle, &dcb);

    COMMTIMEOUTS timeouts = {0};
    timeouts.ReadTotalTimeoutConstant = 1000;
    timeouts.WriteTotalTimeoutConstant = 1000;
    SetCommTimeouts(serial_handle, &timeouts);

    // DTR toggle to reset Arduino
    EscapeCommFunction(serial_handle, CLRDTR);
    Sleep(100);
    EscapeCommFunction(serial_handle, SETDTR);
    Sleep(2000);  // Wait for bootloader

    PurgeComm(serial_handle, PURGE_RXCLEAR | PURGE_TXCLEAR);
    return true;
}

static void serial_close(void) {
    if (serial_handle != INVALID_HANDLE_VALUE) {
        CloseHandle(serial_handle);
        serial_handle = INVALID_HANDLE_VALUE;
    }
}

static bool serial_is_open(void) {
    return serial_handle != INVALID_HANDLE_VALUE;
}

static int serial_write(const uint8_t *data, int len) {
    if (serial_handle == INVALID_HANDLE_VALUE) return -1;
    DWORD written;
    WriteFile(serial_handle, data, len, &written, NULL);
    return (int)written;
}

static int serial_read(uint8_t *data, int len) {
    if (serial_handle == INVALID_HANDLE_VALUE) return -1;
    DWORD read_count;
    ReadFile(serial_handle, data, len, &read_count, NULL);
    return (int)read_count;
}

// List available COM ports
static int serial_list_ports(char ports[][64], int max_ports) {
    int count = 0;
    for (int i = 1; i <= 256 && count < max_ports; i++) {
        char port[16];
        snprintf(port, sizeof(port), "COM%d", i);
        char full_path[32];
        snprintf(full_path, sizeof(full_path), "\\\\.\\%s", port);

        HANDLE h = CreateFileA(full_path, GENERIC_READ | GENERIC_WRITE,
                               0, NULL, OPEN_EXISTING, 0, NULL);
        if (h != INVALID_HANDLE_VALUE) {
            CloseHandle(h);
            strncpy(ports[count++], port, 64);
        }
    }
    return count;
}

#else  // Linux/macOS

static bool serial_open(const char *port, int baud) {
    serial_fd = open(port, O_RDWR | O_NOCTTY);
    if (serial_fd < 0) return false;

    struct termios tty;
    memset(&tty, 0, sizeof(tty));

    cfsetispeed(&tty, B1000000);  // Note: may need B115200 on some systems
    cfsetospeed(&tty, B1000000);

    tty.c_cflag = CS8 | CLOCAL | CREAD;
    tty.c_iflag = IGNPAR;
    tty.c_oflag = 0;
    tty.c_lflag = 0;
    tty.c_cc[VTIME] = 10;  // 1 second timeout
    tty.c_cc[VMIN] = 0;

    tcflush(serial_fd, TCIFLUSH);
    tcsetattr(serial_fd, TCSANOW, &tty);

    // DTR toggle to reset Arduino
    int status;
    ioctl(serial_fd, TIOCMGET, &status);
    status &= ~TIOCM_DTR;
    ioctl(serial_fd, TIOCMSET, &status);
    usleep(100000);
    status |= TIOCM_DTR;
    ioctl(serial_fd, TIOCMSET, &status);
    usleep(2000000);  // Wait for bootloader

    tcflush(serial_fd, TCIFLUSH);
    return true;
}

static void serial_close(void) {
    if (serial_fd >= 0) {
        close(serial_fd);
        serial_fd = -1;
    }
}

static bool serial_is_open(void) {
    return serial_fd >= 0;
}

static int serial_write(const uint8_t *data, int len) {
    if (serial_fd < 0) return -1;
    return write(serial_fd, data, len);
}

static int serial_read(uint8_t *data, int len) {
    if (serial_fd < 0) return -1;
    return read(serial_fd, data, len);
}

// List available serial ports
static int serial_list_ports(char ports[][64], int max_ports) {
    int count = 0;

#ifdef __APPLE__
    // macOS: /dev/cu.usbmodem* or /dev/cu.usbserial*
    DIR *dir = opendir("/dev");
    if (dir) {
        struct dirent *ent;
        while ((ent = readdir(dir)) != NULL && count < max_ports) {
            if (strncmp(ent->d_name, "cu.usb", 6) == 0) {
                snprintf(ports[count++], 64, "/dev/%s", ent->d_name);
            }
        }
        closedir(dir);
    }
#else
    // Linux: /dev/ttyUSB* or /dev/ttyACM*
    DIR *dir = opendir("/dev");
    if (dir) {
        struct dirent *ent;
        while ((ent = readdir(dir)) != NULL && count < max_ports) {
            if (strncmp(ent->d_name, "ttyUSB", 6) == 0 ||
                strncmp(ent->d_name, "ttyACM", 6) == 0) {
                snprintf(ports[count++], 64, "/dev/%s", ent->d_name);
            }
        }
        closedir(dir);
    }
#endif
    return count;
}

#endif

// =============================================================================
// Bridge Public API
// =============================================================================

void serial_bridge_init(void) {
    // Load settings from config if available
    // bridge_port could be loaded from blastem.cfg
}

bool serial_bridge_connect(const char *port) {
    if (bridge_connected) {
        serial_bridge_disconnect();
    }

    if (!serial_open(port, 1000000)) {
        printf("Serial bridge: Failed to open %s\n", port);
        return false;
    }

    strncpy(bridge_port, port, sizeof(bridge_port) - 1);

    // Send PING and wait for ACK + board type + READY
    uint8_t ping = CMD_PING;
    serial_write(&ping, 1);

    uint8_t response[3];
    int bytes_read = serial_read(response, 3);

    if (bytes_read >= 3 &&
        response[0] == CMD_ACK &&
        response[2] == FLOW_READY) {

        board_type = response[1];
        bridge_connected = true;
        bridge_enabled = true;

        const char *board_names[] = {
            "Unknown", "Arduino Uno", "Arduino Mega",
            "Other", "Teensy 4.x", "ESP32"
        };
        const char *name = (board_type < 6) ? board_names[board_type] : "Unknown";

        printf("Serial bridge: Connected to %s on %s\n", name, port);
        return true;
    }

    printf("Serial bridge: No response from device on %s\n", port);
    serial_close();
    return false;
}

bool serial_bridge_auto_connect(void) {
    char ports[16][64];
    int count = serial_list_ports(ports, 16);

    printf("Serial bridge: Scanning %d port(s)...\n", count);

    for (int i = 0; i < count; i++) {
        printf("  Trying %s... ", ports[i]);
        fflush(stdout);

        if (serial_bridge_connect(ports[i])) {
            return true;
        }
        printf("no response\n");
    }

    printf("Serial bridge: No Genesis Engine board found\n");
    return false;
}

void serial_bridge_disconnect(void) {
    if (bridge_connected) {
        // Send end-of-stream to silence chips
        uint8_t cmd = CMD_END_STREAM;
        serial_write(&cmd, 1);

        serial_close();
        bridge_connected = false;
        printf("Serial bridge: Disconnected\n");
    }
}

void serial_bridge_enable(bool enable) {
    bridge_enabled = enable && bridge_connected;
}

bool serial_bridge_is_connected(void) {
    return bridge_connected;
}

bool serial_bridge_is_enabled(void) {
    return bridge_enabled && bridge_connected;
}

const char* serial_bridge_get_port(void) {
    return bridge_connected ? bridge_port : NULL;
}

uint8_t serial_bridge_get_board_type(void) {
    return board_type;
}

// =============================================================================
// Audio Write Functions - Called from YM2612/PSG emulation
// =============================================================================

void serial_bridge_ym2612_write(uint8_t port, uint8_t reg, uint8_t value) {
    if (!bridge_enabled || !serial_is_open()) return;

    uint8_t cmd[3] = {
        (port == 0) ? CMD_YM2612_PORT0 : CMD_YM2612_PORT1,
        reg,
        value
    };
    serial_write(cmd, 3);
}

void serial_bridge_psg_write(uint8_t value) {
    if (!bridge_enabled || !serial_is_open()) return;

    uint8_t cmd[2] = { CMD_PSG_WRITE, value };
    serial_write(cmd, 2);
}

// Called when emulator resets or ROM changes
void serial_bridge_reset(void) {
    if (!bridge_enabled || !serial_is_open()) return;

    uint8_t cmd = CMD_END_STREAM;
    serial_write(&cmd, 1);

    // Drain any pending response
    uint8_t buf[16];
    serial_read(buf, sizeof(buf));
}
```

### 2. New File: `serial_bridge.h`

```c
/**
 * serial_bridge.h - Real-time audio streaming to Genesis Engine hardware
 */

#ifndef SERIAL_BRIDGE_H
#define SERIAL_BRIDGE_H

#include <stdint.h>
#include <stdbool.h>

// Initialize the serial bridge subsystem
void serial_bridge_init(void);

// Connect to a specific port (e.g., "COM3" or "/dev/ttyUSB0")
bool serial_bridge_connect(const char *port);

// Auto-detect and connect to Genesis Engine board
bool serial_bridge_auto_connect(void);

// Disconnect from hardware
void serial_bridge_disconnect(void);

// Enable/disable streaming (while staying connected)
void serial_bridge_enable(bool enable);

// Status queries
bool serial_bridge_is_connected(void);
bool serial_bridge_is_enabled(void);
const char* serial_bridge_get_port(void);
uint8_t serial_bridge_get_board_type(void);

// Audio write functions - called from chip emulation
void serial_bridge_ym2612_write(uint8_t port, uint8_t reg, uint8_t value);
void serial_bridge_psg_write(uint8_t value);

// Reset/silence hardware (called on ROM change, etc.)
void serial_bridge_reset(void);

#endif // SERIAL_BRIDGE_H
```

### 3. Modify: `ym2612.c`

Add the serial bridge calls to the register write function:

```c
#include "serial_bridge.h"  // Add at top of file

// In ym_data_write() function, after the register value is stored:

void ym_data_write(ym2612_context *context, uint8_t value) {
    // ... existing code that processes the write ...

    // After the write is processed, send to hardware bridge
    if (context->selected_part) {
        serial_bridge_ym2612_write(1, context->selected_reg, value);
    } else {
        serial_bridge_ym2612_write(0, context->selected_reg, value);
    }

    // ... rest of existing code ...
}
```

### 4. Modify: `psg.c`

Add the serial bridge call to the PSG write function:

```c
#include "serial_bridge.h"  // Add at top of file

// In psg_write() function:

void psg_write(psg_context *context, uint8_t value) {
    // Send to hardware bridge
    serial_bridge_psg_write(value);

    // ... existing PSG emulation code ...
}
```

### 5. Modify: `genesis.c`

Initialize the bridge and handle reset:

```c
#include "serial_bridge.h"  // Add at top of file

// In genesis initialization (start_genesis or similar):
serial_bridge_init();

// In genesis reset function:
serial_bridge_reset();

// In cleanup/shutdown:
serial_bridge_disconnect();
```

### 6. Modify: `menu.c` or UI code

Add menu options for hardware bridge:

```c
// Add menu items for:
// - "Hardware Audio" submenu
//   - "Auto-Connect"       -> serial_bridge_auto_connect()
//   - "Connect to COM3"    -> serial_bridge_connect("COM3")
//   - "Disconnect"         -> serial_bridge_disconnect()
//   - "Enable/Disable"     -> serial_bridge_enable(toggle)
//   - Status display showing connection state
```

### 7. Modify: `Makefile`

Add serial_bridge.c to the build:

```makefile
# Add to SRCS
SRCS += serial_bridge.c

# On Windows, may need to link against setupapi for serial port enumeration
# On Linux/macOS, no additional libraries needed
```

## Configuration Options

Add to `default.cfg` or `blastem.cfg`:

```ini
[hardware_bridge]
; Auto-connect to hardware on startup (true/false)
auto_connect = false

; Preferred serial port (empty for auto-detect)
; Examples: COM3, /dev/ttyUSB0, /dev/cu.usbmodem14101
port =

; Enable hardware audio by default when connected
enabled = true
```

## Keyboard Shortcuts

Suggested bindings (add to `bindings.c`):

| Key | Action |
|-----|--------|
| `H` | Toggle hardware audio on/off |
| `Shift+H` | Auto-connect to hardware |

## UI Integration (Nuklear)

For the Nuklear-based UI, add a "Hardware Audio" section:

```c
// In render_menu() or similar:

if (nk_tree_push(ctx, NK_TREE_NODE, "Hardware Audio", NK_MINIMIZED)) {
    if (serial_bridge_is_connected()) {
        nk_label(ctx, "Status: Connected", NK_TEXT_LEFT);
        nk_labelf(ctx, NK_TEXT_LEFT, "Port: %s", serial_bridge_get_port());

        // Enable/disable toggle
        bool enabled = serial_bridge_is_enabled();
        if (nk_checkbox_label(ctx, "Enable", &enabled)) {
            serial_bridge_enable(enabled);
        }

        if (nk_button_label(ctx, "Disconnect")) {
            serial_bridge_disconnect();
        }
    } else {
        nk_label(ctx, "Status: Not connected", NK_TEXT_LEFT);

        if (nk_button_label(ctx, "Auto-Connect")) {
            serial_bridge_auto_connect();
        }

        // Manual port selection could go here
    }
    nk_tree_pop(ctx);
}
```

## Testing

1. Build BlastEm with the serial bridge code
2. Upload `EmulatorBridge.ino` to your Genesis Engine board
3. Launch BlastEm
4. Press `Shift+H` or use menu to auto-connect
5. Load a ROM - audio should play on real hardware!

## Troubleshooting

### No connection / timeout
- Check USB cable and board power
- Verify correct serial port in device manager / `ls /dev/tty*`
- Try a lower baud rate (115200) if 1Mbaud fails

### Audio glitches / dropouts
- Ensure BlastEm is running at 1x speed (not turbo mode)
- Check for USB latency issues (try a different USB port/hub)
- On Linux, add user to `dialout` group: `sudo usermod -a -G dialout $USER`

### Hanging notes when pausing
- The bridge has a 1-second timeout that auto-silences
- Explicit pause should call `serial_bridge_reset()`

## Notes

- The hardware bridge is independent of BlastEm's internal audio - both can run simultaneously for A/B comparison
- VGM logging (M key) remains fully functional alongside hardware output
- Hardware audio adds minimal CPU overhead (<1%)

## License

This integration code is provided under the same GPL v3 license as BlastEm.
