/**
 * StreamingProtocol.h - Binary protocol definitions for VGM streaming
 *
 * This header defines the compact binary protocol used for high-performance
 * serial streaming between Python and Arduino.
 *
 * Protocol characteristics:
 *   - Little-endian for multi-byte values (matches AVR native format)
 *   - Single-byte commands followed by binary arguments
 *   - PING/ACK handshaking for device readiness
 */

#ifndef STREAMING_PROTOCOL_H
#define STREAMING_PROTOCOL_H

// =============================================================================
// Control Commands
// =============================================================================

#define CMD_PING             0x00  // Python->Arduino: Is device ready?
#define CMD_ACK              0x0F  // Arduino->Python: Acknowledgment/ready

// =============================================================================
// Chip Write Commands (matches VGM command bytes)
// =============================================================================

#define CMD_PSG_WRITE        0x50  // Write byte to SN76489 PSG
                                   // Args: uint8_t value

#define CMD_YM2612_WRITE_A0  0x52  // Write to YM2612 Port 0
                                   // Args: uint8_t addr, uint8_t val

#define CMD_YM2612_WRITE_A1  0x53  // Write to YM2612 Port 1
                                   // Args: uint8_t addr, uint8_t val

// =============================================================================
// Wait Commands
// =============================================================================

#define CMD_WAIT_FRAMES      0x61  // Wait N samples (little-endian uint16)
                                   // Args: uint16_t samples

#define CMD_WAIT_NTSC        0x62  // Wait 735 samples (1/60 sec NTSC)
                                   // Args: none

#define CMD_WAIT_PAL         0x63  // Wait 882 samples (1/50 sec PAL)
                                   // Args: none

// Short waits 0x70-0x7F: wait (cmd & 0x0F) + 1 samples
// No explicit defines needed, handled by range check

// =============================================================================
// DAC Commands
// =============================================================================

// Commands 0x80-0x8F in VGM: Write DAC + wait (cmd & 0x0F) samples
// These are handled specially with inlined PCM data

// =============================================================================
// Compression Commands
// =============================================================================

#define CMD_RLE_WAIT_FRAME_1 0xC0  // RLE: Wait for N single frames
                                   // Args: uint8_t count (2-255)

// =============================================================================
// Stream Control
// =============================================================================

#define CMD_END_OF_STREAM    0x66  // End of song (matches VGM)
                                   // Args: none

#define CMD_PCM_SEEK         0xE0  // PCM data seek (ignored, for compatibility)
                                   // Args: uint32_t offset (4 bytes, little-endian)

// =============================================================================
// Flow Control
// =============================================================================

// NOTE: These MUST NOT conflict with VGM command bytes!
// Previously 'R' (0x52) conflicted with CMD_YM2612_WRITE_A0 (0x52)
// Using ASCII control codes that aren't used in VGM:
#define FLOW_READY           0x06  // Arduino->Python: Ready for more data (ASCII ACK)
#define FLOW_NAK             0x15  // Arduino->Python: Bad checksum/retry (ASCII NAK)

// =============================================================================
// Timing Constants
// =============================================================================

#define SAMPLE_RATE          44100
#define FRAME_SAMPLES_NTSC   735   // 44100 / 60
#define FRAME_SAMPLES_PAL    882   // 44100 / 50
#define FRAME_DURATION_US    16667 // 1/60 sec in microseconds

#endif // STREAMING_PROTOCOL_H
