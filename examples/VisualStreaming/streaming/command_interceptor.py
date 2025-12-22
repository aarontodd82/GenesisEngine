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

import threading
import queue
import time
import numpy as np
from typing import Optional, Callable, List, Tuple

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

    NEW DESIGN: Commands are applied immediately (just chip writes).
    Sample generation runs continuously at real-time rate in a separate thread.
    This keeps visualization in sync with actual playback.
    """

    # Samples per update (roughly 60fps = 735 samples per frame)
    SAMPLES_PER_UPDATE = 735

    # Sample rate
    SAMPLE_RATE = 44100

    def __init__(self):
        # Emulators
        self.ym2612 = YM2612ymfm()
        self.sn76489 = SN76489()

        # Command queue (thread-safe) - for chip writes only
        self.command_queue: queue.Queue = queue.Queue()

        # Waveform callback
        self.on_waveform_update: Optional[Callable[[int, np.ndarray], None]] = None

        # Key-on callback
        self.on_key_change: Optional[Callable[[int, bool], None]] = None

        # Running state
        self._running = False
        self._command_thread: Optional[threading.Thread] = None
        self._sample_thread: Optional[threading.Thread] = None

        # Lock for emulator access
        self._emu_lock = threading.Lock()

    def start(self):
        """Start the interceptor threads."""
        if self._running:
            return

        self._running = True
        with self._emu_lock:
            self.ym2612.reset()
            self.sn76489.reset()

        # Thread 1: Apply commands (fast, no sample generation)
        self._command_thread = threading.Thread(target=self._run_commands, daemon=True)
        self._command_thread.start()

        # Thread 2: Generate samples at real-time rate
        self._sample_thread = threading.Thread(target=self._run_samples, daemon=True)
        self._sample_thread.start()

    def stop(self):
        """Stop the interceptor threads."""
        self._running = False
        if self._command_thread:
            self._command_thread.join(timeout=1.0)
            self._command_thread = None
        if self._sample_thread:
            self._sample_thread.join(timeout=1.0)
            self._sample_thread = None

    def queue_command(self, cmd: int, args: bytes):
        """Queue a command for processing."""
        self.command_queue.put((cmd, args))

    def _run_commands(self):
        """Process commands as fast as possible - just apply chip writes."""
        while self._running:
            try:
                cmd, args = self.command_queue.get(timeout=0.01)
                self._apply_command(cmd, args)
            except queue.Empty:
                continue

    def _apply_command(self, cmd: int, args: bytes):
        """
        Apply a chip write command to the emulators.

        This ONLY handles chip writes - no sample generation.
        Wait commands are ignored since sample generation runs at real-time rate.
        """
        with self._emu_lock:
            if cmd == CMD_PSG_WRITE:
                if args:
                    self.sn76489.write(args[0])
                    # Check for key changes
                    psg_byte = args[0]
                    if psg_byte & 0x80 and psg_byte & 0x10:  # Attenuation latch
                        channel = (psg_byte >> 5) & 0x03
                        atten = psg_byte & 0x0F
                        is_on = atten < 15
                        if self.on_key_change:
                            self.on_key_change(6 + channel, is_on)

            elif cmd == CMD_YM2612_WRITE_A0:
                if len(args) >= 2:
                    self.ym2612.write(0, args[0], args[1])
                    self._check_ym_key_change(args[0], args[1])

            elif cmd == CMD_YM2612_WRITE_A1:
                if len(args) >= 2:
                    self.ym2612.write(1, args[0], args[1])
                    self._check_ym_key_change(args[0], args[1])

            elif 0x80 <= cmd <= 0x8F:
                # DAC write - apply immediately
                if args:
                    self.ym2612.write(0, 0x2A, args[0])

            # All wait commands (0x61, 0x62, 0x63, 0x70-0x7F, 0xC0) are ignored
            # because sample generation runs at constant real-time rate

    def _run_samples(self):
        """Generate samples at real-time rate (60 fps)."""
        import time
        frame_time = self.SAMPLES_PER_UPDATE / self.SAMPLE_RATE  # ~16.7ms

        while self._running:
            start = time.perf_counter()

            # Generate one frame of samples
            with self._emu_lock:
                fm_waves = self.ym2612.generate_samples(self.SAMPLES_PER_UPDATE)
                psg_waves = self.sn76489.generate_samples(self.SAMPLES_PER_UPDATE)

            # Send to visualizer
            if self.on_waveform_update:
                for ch in range(6):
                    self.on_waveform_update(ch, fm_waves[ch])
                for ch in range(4):
                    self.on_waveform_update(6 + ch, psg_waves[ch])

            # Sleep to maintain real-time rate
            elapsed = time.perf_counter() - start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

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
