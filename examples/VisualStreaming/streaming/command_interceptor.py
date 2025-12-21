"""
Command Interceptor for visualization.

This module intercepts chip write commands as they're streamed to the
hardware and routes them to the software emulators for visualization.
"""

import threading
import queue
import time
import numpy as np
from typing import Optional, Callable, Tuple

# Import emulators
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emulators.ym2612 import YM2612
from emulators.sn76489 import SN76489


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

    This runs in its own thread, processing commands from a queue
    and generating waveform data at regular intervals.
    """

    # Samples to generate per update (affects waveform smoothness)
    SAMPLES_PER_UPDATE = 128

    # Update rate in Hz
    UPDATE_RATE = 60

    def __init__(self):
        # Emulators
        self.ym2612 = YM2612()
        self.sn76489 = SN76489()

        # Command queue (thread-safe)
        self.command_queue: queue.Queue = queue.Queue()

        # Waveform callback
        self.on_waveform_update: Optional[Callable[[int, np.ndarray], None]] = None

        # Key-on callback
        self.on_key_change: Optional[Callable[[int, bool], None]] = None

        # Running state
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Timing
        self._accumulated_samples = 0

    def start(self):
        """Start the interceptor thread."""
        if self._running:
            return

        self._running = True
        self.ym2612.reset()
        self.sn76489.reset()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the interceptor thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def queue_command(self, cmd: int, args: bytes):
        """Queue a command for processing."""
        self.command_queue.put((cmd, args))

    def _run(self):
        """Main interceptor loop."""
        update_interval = 1.0 / self.UPDATE_RATE
        last_update = time.time()

        while self._running:
            # Process all pending commands
            while True:
                try:
                    cmd, args = self.command_queue.get_nowait()
                    self._process_command(cmd, args)
                except queue.Empty:
                    break

            # Generate waveforms at regular intervals
            now = time.time()
            if now - last_update >= update_interval:
                self._generate_waveforms()
                last_update = now

            # Small sleep to avoid busy-waiting
            time.sleep(0.001)

    def _process_command(self, cmd: int, args: bytes):
        """Process a single command."""
        if cmd == CMD_PSG_WRITE:
            # PSG write
            if args:
                self.sn76489.write(args[0])

                # Check for key changes (attenuation commands)
                psg_byte = args[0]
                if psg_byte & 0x80:  # Latch byte
                    if psg_byte & 0x10:  # Attenuation
                        channel = (psg_byte >> 5) & 0x03
                        atten = psg_byte & 0x0F
                        # Map PSG channels to global channel indices (6-9)
                        global_ch = 6 + channel
                        is_on = atten < 15
                        if self.on_key_change:
                            self.on_key_change(global_ch, is_on)

        elif cmd == CMD_YM2612_WRITE_A0:
            # YM2612 Port 0
            if len(args) >= 2:
                self.ym2612.write(0, args[0], args[1])
                self._check_ym_key_change(args[0], args[1])

        elif cmd == CMD_YM2612_WRITE_A1:
            # YM2612 Port 1
            if len(args) >= 2:
                self.ym2612.write(1, args[0], args[1])
                self._check_ym_key_change(args[0], args[1])

        elif 0x80 <= cmd <= 0x8F:
            # DAC + wait
            if args:
                self.ym2612.write(0, 0x2A, args[0])  # DAC data

        elif cmd == CMD_WAIT_NTSC:
            self._accumulated_samples += FRAME_SAMPLES_NTSC

        elif cmd == CMD_WAIT_PAL:
            self._accumulated_samples += FRAME_SAMPLES_PAL

        elif cmd == CMD_WAIT_FRAMES:
            if len(args) >= 2:
                samples = args[0] | (args[1] << 8)
                self._accumulated_samples += samples

        elif 0x70 <= cmd <= 0x7F:
            # Short wait
            self._accumulated_samples += (cmd & 0x0F) + 1

        elif cmd == CMD_RLE_WAIT_FRAME_1:
            if args:
                self._accumulated_samples += args[0] * FRAME_SAMPLES_NTSC

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

    def _generate_waveforms(self):
        """Generate and send waveform updates."""
        if not self.on_waveform_update:
            return

        # Generate YM2612 waveforms
        fm_waves = self.ym2612.generate_samples(self.SAMPLES_PER_UPDATE)
        for ch in range(6):
            self.on_waveform_update(ch, fm_waves[ch])

        # Generate PSG waveforms
        psg_waves = self.sn76489.generate_samples(self.SAMPLES_PER_UPDATE)
        for ch in range(4):
            self.on_waveform_update(6 + ch, psg_waves[ch])

    def get_channel_active(self, channel: int) -> bool:
        """Check if a channel is currently active."""
        if channel < 6:
            return self.ym2612.is_active(channel)
        elif channel < 10:
            return self.sn76489.is_active(channel - 6)
        return False


# Test
if __name__ == "__main__":
    interceptor = CommandInterceptor()

    def on_waveform(ch, data):
        if data.max() > 0.01:
            print(f"Channel {ch}: max={data.max():.3f}")

    def on_key(ch, on):
        print(f"Channel {ch} key {'ON' if on else 'OFF'}")

    interceptor.on_waveform_update = on_waveform
    interceptor.on_key_change = on_key

    interceptor.start()

    # Simulate some commands
    # PSG channel 0: tone + volume
    interceptor.queue_command(CMD_PSG_WRITE, bytes([0x80 | 0x0F]))  # Freq low
    interceptor.queue_command(CMD_PSG_WRITE, bytes([0x00]))          # Freq high
    interceptor.queue_command(CMD_PSG_WRITE, bytes([0x90 | 0x00]))  # Volume max

    # YM2612 channel 0: key on
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0xA0, 0x50]))  # Freq
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0xA4, 0x22]))  # Block
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0x40, 0x10]))  # TL
    interceptor.queue_command(CMD_YM2612_WRITE_A0, bytes([0x28, 0xF0]))  # Key on

    time.sleep(0.5)
    interceptor.stop()
    print("Test complete")
