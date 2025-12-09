/**
 * BridgeProtocol.h - Protocol definitions for real-time emulator bridging
 *
 * This is a simplified protocol for streaming register writes from an emulator
 * to real hardware. Unlike VGM streaming, there are NO timing commands because
 * the emulator handles all timing internally.
 *
 * The emulator sends register writes exactly when they should occur, and the
 * microcontroller immediately writes them to the hardware chips.
 */

#ifndef BRIDGE_PROTOCOL_H
#define BRIDGE_PROTOCOL_H

// =============================================================================
// Control Commands
// =============================================================================

#define CMD_PING             0x00  // Emulator->Device: Connection request
                                   // Device responds: ACK + BOARD_TYPE + READY

#define CMD_ACK              0x0F  // Device->Emulator: Acknowledgment

// =============================================================================
// Chip Write Commands (VGM-compatible byte values)
// =============================================================================

#define CMD_PSG_WRITE        0x50  // Write to SN76489 PSG
                                   // Format: 0x50 <value>
                                   // Total: 2 bytes

#define CMD_YM2612_PORT0     0x52  // Write to YM2612 Port 0 (channels 1-3)
                                   // Format: 0x52 <register> <value>
                                   // Total: 3 bytes

#define CMD_YM2612_PORT1     0x53  // Write to YM2612 Port 1 (channels 4-6)
                                   // Format: 0x53 <register> <value>
                                   // Total: 3 bytes

// =============================================================================
// Stream Control
// =============================================================================

#define CMD_END_STREAM       0x66  // End of stream / reset request
                                   // Device silences all chips and responds READY
                                   // Format: 0x66
                                   // Total: 1 byte

// =============================================================================
// Flow Control (Device -> Emulator)
// =============================================================================

#define FLOW_READY           0x06  // Device is ready (ASCII ACK)
                                   // Sent after: PING, END_STREAM, or reconnect

// =============================================================================
// Board Type Identifiers
// =============================================================================

#define BOARD_TYPE_UNO       1     // Arduino Uno (ATmega328P)
#define BOARD_TYPE_MEGA      2     // Arduino Mega (ATmega2560)
#define BOARD_TYPE_OTHER     3     // Other/unknown board
#define BOARD_TYPE_TEENSY4   4     // Teensy 4.0/4.1 (IMXRT1062)
#define BOARD_TYPE_ESP32     5     // ESP32

// =============================================================================
// Recommended Serial Settings
// =============================================================================

// Baud rate: 1,000,000 (1 Mbaud)
//
// At 1 Mbaud, each byte takes ~10us to transmit.
// A YM2612 write (3 bytes) takes ~30us.
// A frame (1/60 sec) is 16,667us = ~555 YM2612 writes max throughput.
// Real games rarely exceed 100-200 writes per frame, so this is plenty.
//
// Lower baud rates (115200, 250000) will also work for most games,
// but may struggle with DAC-heavy tracks.

#define RECOMMENDED_BAUD     1000000

// =============================================================================
// Protocol Notes
// =============================================================================

/*
 * CONNECTION SEQUENCE:
 *
 * 1. Emulator opens serial port
 * 2. Emulator sends: 0x00 (PING)
 * 3. Device responds: 0x0F <board_type> 0x06 (ACK + type + READY)
 * 4. Emulator begins sending register writes
 *
 * DURING PLAYBACK:
 *
 * - Emulator sends register writes as they occur in emulation
 * - No acknowledgment needed for each write (fire-and-forget)
 * - Device writes to hardware immediately upon receipt
 *
 * DISCONNECTION / RESET:
 *
 * - Emulator sends: 0x66 (END_STREAM)
 * - Device silences chips and responds: 0x06 (READY)
 * - Or: if no data received for 1 second, device auto-silences
 *
 * RECONNECTION:
 *
 * - Emulator can send PING at any time to reconnect
 * - Device will reset chips and respond with ACK sequence
 */

#endif // BRIDGE_PROTOCOL_H
