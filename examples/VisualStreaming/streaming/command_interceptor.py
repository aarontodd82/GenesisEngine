"""
Command Interceptor for visualization.

This module intercepts chip write commands as they're streamed to the
hardware and routes them to the software emulators for visualization.

Key design principle: Two separate threads:
1. Command thread: Applies chip writes immediately (fast, no sample generation)
2. Sample thread: Generates samples at real-time rate (60fps)

This keeps visualization in sync with actual playback by generating samples
at a constant rate regardless of when commands arrive.

Uses ymfm for YM2612 emulation.
"""

import numpy as np
from typing import Optional, Callable

# Import emulators
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emulators.sn76489 import SN76489
from emulators.ymfm import YM2612ymfm
print("Using ymfm YM2612 emulator")


# Command constants (must match streaming protocol)
CMD_PSG_WRITE = 0x50
CMD_YM2612_WRITE_A0 = 0x52
CMD_YM2612_WRITE_A1 = 0x53
CMD_WAIT_FRAMES = 0x61
CMD_WAIT_NTSC = 0x62
CMD_WAIT_PAL = 0x63
CMD_RLE_WAIT_FRAME_1 = 0xC0
CMD_END_OF_STREAM = 0x66

FRAME_SAMPLES_NTSC = 735
FRAME_SAMPLES_PAL = 882


class CommandInterceptor:
    """
    Intercepts streaming commands and feeds them to emulators.

    DESIGN: Process commands SYNCHRONOUSLY - no queues!
    When process_chunk() is called, we immediately:
    1. Parse all commands in the chunk
    2. Apply chip writes to emulators
    3. Generate samples for wait commands
    4. Send waveforms to visualizer

    This keeps visualization in perfect sync with hardware streaming
    because both happen in the same call, at the same time.
    """

    # Minimum samples to buffer before sending to visualizer
    MIN_SAMPLES_FOR_UPDATE = 256

    # Maximum samples per visualizer update
    MAX_SAMPLES_FOR_UPDATE = 2048

    def __init__(self):
        # Emulators
        self.ym2612 = YM2612ymfm()
        self.sn76489 = SN76489()

        # Waveform callback
        self.on_waveform_update: Optional[Callable[[int, np.ndarray], None]] = None

        # Key-on callback
        self.on_key_change: Optional[Callable[[int, bool], None]] = None

        # Sample buffers for each channel (accumulate before sending)
        self._fm_buffers = [[] for _ in range(6)]
        self._psg_buffers = [[] for _ in range(4)]

        # Running state
        self._running = False

    def start(self):
        """Initialize the interceptor."""
        self._running = True
        self.ym2612.reset()
        self.sn76489.reset()
        self._fm_buffers = [[] for _ in range(6)]
        self._psg_buffers = [[] for _ in range(4)]

    def stop(self):
        """Stop the interceptor."""
        self._running = False
        # Flush any remaining samples
        self._flush_buffers()

    def process_chunk(self, data: bytes):
        """
        Process a chunk of commands SYNCHRONOUSLY.

        Call this from the streaming thread immediately after sending
        the chunk to hardware. This ensures perfect sync.
        """
        if not self._running:
            return

        i = 0
        while i < len(data):
            cmd = data[i]

            if cmd == CMD_PSG_WRITE:
                if i + 1 < len(data):
                    self._apply_psg_write(data[i + 1])
                i += 2

            elif cmd == CMD_YM2612_WRITE_A0:
                if i + 2 < len(data):
                    self._apply_ym_write(0, data[i + 1], data[i + 2])
                i += 3

            elif cmd == CMD_YM2612_WRITE_A1:
                if i + 2 < len(data):
                    self._apply_ym_write(1, data[i + 1], data[i + 2])
                i += 3

            elif cmd == CMD_WAIT_FRAMES:
                if i + 2 < len(data):
                    samples = data[i + 1] | (data[i + 2] << 8)
                    self._generate_samples(samples)
                i += 3

            elif cmd == CMD_WAIT_NTSC:
                self._generate_samples(FRAME_SAMPLES_NTSC)
                i += 1

            elif cmd == CMD_WAIT_PAL:
                self._generate_samples(FRAME_SAMPLES_PAL)
                i += 1

            elif 0x70 <= cmd <= 0x7F:
                # Short wait (1-16 samples)
                samples = (cmd & 0x0F) + 1
                self._generate_samples(samples)
                i += 1

            elif 0x80 <= cmd <= 0x8F:
                # DAC + wait
                if i + 1 < len(data):
                    self.ym2612.write(0, 0x2A, data[i + 1])
                wait_samples = cmd & 0x0F
                if wait_samples > 0:
                    self._generate_samples(wait_samples)
                i += 2

            elif cmd == CMD_RLE_WAIT_FRAME_1:
                if i + 1 < len(data):
                    total_samples = data[i + 1] * FRAME_SAMPLES_NTSC
                    self._generate_samples(total_samples)
                i += 2

            elif cmd == CMD_END_OF_STREAM:
                i += 1

            else:
                i += 1

    def process_command(self, cmd: int, args: bytes):
        """
        Process a single command (for offline mode).

        This is the parsed command format, not raw bytes.
        """
        if not self._running:
            return

        if cmd == CMD_PSG_WRITE:
            if args:
                self._apply_psg_write(args[0])

        elif cmd == CMD_YM2612_WRITE_A0:
            if len(args) >= 2:
                self._apply_ym_write(0, args[0], args[1])

        elif cmd == CMD_YM2612_WRITE_A1:
            if len(args) >= 2:
                self._apply_ym_write(1, args[0], args[1])

        elif cmd == CMD_WAIT_FRAMES:
            if len(args) >= 2:
                samples = args[0] | (args[1] << 8)
                self._generate_samples(samples)

        elif cmd == CMD_WAIT_NTSC:
            self._generate_samples(FRAME_SAMPLES_NTSC)

        elif cmd == CMD_WAIT_PAL:
            self._generate_samples(FRAME_SAMPLES_PAL)

        elif 0x70 <= cmd <= 0x7F:
            samples = (cmd & 0x0F) + 1
            self._generate_samples(samples)

        elif 0x80 <= cmd <= 0x8F:
            if args:
                self.ym2612.write(0, 0x2A, args[0])
            wait_samples = cmd & 0x0F
            if wait_samples > 0:
                self._generate_samples(wait_samples)

        elif cmd == CMD_RLE_WAIT_FRAME_1:
            if args:
                total_samples = args[0] * FRAME_SAMPLES_NTSC
                self._generate_samples(total_samples)

    def _apply_psg_write(self, value: int):
        """Apply a PSG write and check for key changes."""
        self.sn76489.write(value)

        # Check for key changes (attenuation commands)
        if value & 0x80 and value & 0x10:  # Attenuation latch
            channel = (value >> 5) & 0x03
            atten = value & 0x0F
            is_on = atten < 15
            if self.on_key_change:
                self.on_key_change(6 + channel, is_on)

    def _apply_ym_write(self, port: int, addr: int, data: int):
        """Apply a YM2612 write and check for key changes."""
        self.ym2612.write(port, addr, data)
        self._check_ym_key_change(addr, data)

    def _generate_samples(self, num_samples: int):
        """Generate samples and buffer them for visualization."""
        if num_samples <= 0:
            return

        # Generate from both chips
        fm_waves = self.ym2612.generate_samples(num_samples)
        psg_waves = self.sn76489.generate_samples(num_samples)

        # Add to buffers
        for ch in range(6):
            self._fm_buffers[ch].extend(fm_waves[ch])
        for ch in range(4):
            self._psg_buffers[ch].extend(psg_waves[ch])

        # Check if we have enough samples to send
        buffer_len = len(self._fm_buffers[0])
        if buffer_len >= self.MIN_SAMPLES_FOR_UPDATE:
            self._flush_buffers()

    def _flush_buffers(self):
        """Send buffered samples to visualizer."""
        if not self.on_waveform_update:
            # Clear buffers if no callback
            self._fm_buffers = [[] for _ in range(6)]
            self._psg_buffers = [[] for _ in range(4)]
            return

        # Send FM channels
        for ch in range(6):
            if self._fm_buffers[ch]:
                samples = np.array(self._fm_buffers[ch], dtype=np.float32)
                # Send in chunks if too large
                while len(samples) > 0:
                    chunk = samples[:self.MAX_SAMPLES_FOR_UPDATE]
                    samples = samples[self.MAX_SAMPLES_FOR_UPDATE:]
                    self.on_waveform_update(ch, chunk)
                self._fm_buffers[ch] = []

        # Send PSG channels
        for ch in range(4):
            if self._psg_buffers[ch]:
                samples = np.array(self._psg_buffers[ch], dtype=np.float32)
                while len(samples) > 0:
                    chunk = samples[:self.MAX_SAMPLES_FOR_UPDATE]
                    samples = samples[self.MAX_SAMPLES_FOR_UPDATE:]
                    self.on_waveform_update(6 + ch, chunk)
                self._psg_buffers[ch] = []

    def _check_ym_key_change(self, addr: int, data: int):
        """Check for key-on/off changes in YM2612 writes."""
        if addr == 0x28:  # Key on/off register
            channel = data & 0x07
            if channel >= 4:
                channel = channel - 4 + 3  # Map 4-6 to 3-5

            if channel < 6:
                key_on = (data & 0xF0) != 0
                if self.on_key_change:
                    self.on_key_change(channel, key_on)

    def get_channel_active(self, channel: int) -> bool:
        """Check if a channel is currently active."""
        if channel < 6:
            return self.ym2612.is_active(channel)
        elif channel < 10:
            return self.sn76489.is_active(channel - 6)
        return False


# Test
if __name__ == "__main__":
    print("YM2612 emulator: ymfm")

    interceptor = CommandInterceptor()

    received_samples = {ch: 0 for ch in range(10)}

    def on_waveform(ch, data):
        received_samples[ch] += len(data)
        if np.abs(data).max() > 0.01:
            print(f"Channel {ch}: {len(data)} samples, max={np.abs(data).max():.3f}")

    def on_key(ch, on):
        print(f"Channel {ch} key {'ON' if on else 'OFF'}")

    interceptor.on_waveform_update = on_waveform
    interceptor.on_key_change = on_key

    interceptor.start()

    # Simulate some commands
    print("\nSending PSG commands...")
    interceptor.queue_command(CMD_PSG_WRITE, bytes([0x80 | 0x0F]))  # Freq low
    interceptor.queue_command(CMD_PSG_WRITE, bytes([0x00]))          # Freq high
    interceptor.queue_command(CMD_PSG_WRITE, bytes([0x90 | 0x00]))  # Volume max
    interceptor.queue_command(CMD_WAIT_NTSC, b'')  # Wait 1 frame

    print("\nSending YM2612 commands...")
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0xB0, 0x07]))  # Algo 7
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0xA4, 0x22]))  # Block
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0xA0, 0x69]))  # Fnum
    for slot in range(4):
        base = 0x30 + slot * 4
        interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([base, 0x01]))  # MUL
        interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([base + 0x10, 0x00]))  # TL
        interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([base + 0x20, 0x1F]))  # AR
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0x28, 0xF0]))  # Key on
    interceptor.queue_command(CMD_WAIT_NTSC, b'')  # Wait 1 frame

    time.sleep(0.3)
    interceptor.stop()

    print("\n--- Results ---")
    for ch, count in received_samples.items():
        print(f"Channel {ch}: {count} samples received")
    print("Test complete")
