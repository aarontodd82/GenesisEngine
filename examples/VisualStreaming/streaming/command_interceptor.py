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

    # Pre-allocated buffer size (must be >= MAX_SAMPLES_FOR_UPDATE)
    BUFFER_SIZE = 4096

    def __init__(self):
        # Emulators
        self.ym2612 = YM2612ymfm()
        self.sn76489 = SN76489()

        # Waveform callback
        self.on_waveform_update: Optional[Callable[[int, np.ndarray], None]] = None

        # Audio output callback (stereo samples for speaker output)
        self.on_audio_output: Optional[Callable[[np.ndarray], None]] = None

        # Key-on callback
        self.on_key_change: Optional[Callable[[int, bool], None]] = None

        # DAC mode callback (called when DAC is enabled/disabled)
        self.on_dac_mode_change: Optional[Callable[[bool], None]] = None

        # Pitch change callback (channel, fractional_midi_pitch)
        self.on_pitch_change: Optional[Callable[[int, float], None]] = None

        # DAC mode state (FM channel 6 becomes DAC output)
        self._dac_enabled = False

        # Pre-allocated sample buffers (avoid list allocations)
        self._fm_buffers = [np.zeros(self.BUFFER_SIZE, dtype=np.float32) for _ in range(6)]
        self._psg_buffers = [np.zeros(self.BUFFER_SIZE, dtype=np.float32) for _ in range(4)]
        self._stereo_buffer = np.zeros((self.BUFFER_SIZE, 2), dtype=np.float32)
        self._buffer_pos = 0  # Current write position in buffers

        # FM frequency tracking (fnum, block per channel)
        self._fm_fnum = [0] * 6
        self._fm_block = [0] * 6

        # PSG frequency tracking (10-bit tone value per channel)
        self._psg_tone = [0] * 3
        self._psg_latch_channel = 0
        self._psg_latch_is_volume = False

        # Running state
        self._running = False

    def start(self):
        """Initialize the interceptor."""
        self._running = True
        self.ym2612.reset()
        self.sn76489.reset()
        self._buffer_pos = 0
        self._dac_enabled = False

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
        """Apply a PSG write and check for key/frequency changes."""
        self.sn76489.write(value)

        # Track frequency changes
        self._check_psg_frequency(value)

        # Check for key changes (attenuation commands)
        if value & 0x80 and value & 0x10:  # Attenuation latch
            channel = (value >> 5) & 0x03
            atten = value & 0x0F
            is_on = atten < 15
            if self.on_key_change:
                self.on_key_change(6 + channel, is_on)

    def _apply_ym_write(self, port: int, addr: int, data: int):
        """Apply a YM2612 write and check for key/DAC/frequency changes."""
        self.ym2612.write(port, addr, data)
        self._check_ym_key_change(addr, data)
        self._check_dac_change(port, addr, data)
        self._check_fm_frequency(port, addr, data)

    def _generate_samples(self, num_samples: int):
        """Generate samples and buffer them for visualization and audio."""
        if num_samples <= 0:
            return

        # Check if we need to flush before adding (buffer would overflow)
        if self._buffer_pos + num_samples > self.BUFFER_SIZE:
            self._flush_buffers()

        # Generate from both chips
        fm_waves = self.ym2612.generate_samples(num_samples)
        psg_waves = self.sn76489.generate_samples(num_samples)

        # Copy to pre-allocated buffers (fast numpy slice assignment)
        pos = self._buffer_pos
        end = pos + num_samples
        for ch in range(6):
            self._fm_buffers[ch][pos:end] = fm_waves[ch]
        for ch in range(4):
            self._psg_buffers[ch][pos:end] = psg_waves[ch]

        # Capture stereo output if audio callback is set
        if self.on_audio_output:
            stereo = self.ym2612.get_stereo_buffer()  # Shape: (num_samples, 2)
            # FM stereo sums 6 channels but normalizes by 1 channel max - scale down
            stereo *= 0.45
            # Add PSG to stereo mix (PSG is mono, sum and add to both channels)
            psg_mix = psg_waves[0] + psg_waves[1] + psg_waves[2] + psg_waves[3]
            psg_mix *= 0.10  # Scale PSG relative to FM
            stereo[:, 0] += psg_mix
            stereo[:, 1] += psg_mix
            # Clip and copy to buffer
            np.clip(stereo, -1.0, 1.0, out=self._stereo_buffer[pos:end])

        self._buffer_pos = end

        # Check if we have enough samples to send
        if self._buffer_pos >= self.MIN_SAMPLES_FOR_UPDATE:
            self._flush_buffers()

    def _flush_buffers(self):
        """Send buffered samples to visualizer and audio output."""
        if self._buffer_pos == 0:
            return

        buf_len = self._buffer_pos

        # Send stereo to audio output (single slice, no concatenation)
        if self.on_audio_output:
            self.on_audio_output(self._stereo_buffer[:buf_len].copy())

        if self.on_waveform_update:
            # Send FM channels (slice from pre-allocated buffer)
            for ch in range(6):
                # Send in chunks if too large
                pos = 0
                while pos < buf_len:
                    end = min(pos + self.MAX_SAMPLES_FOR_UPDATE, buf_len)
                    self.on_waveform_update(ch, self._fm_buffers[ch][pos:end])
                    pos = end

            # Send PSG channels
            for ch in range(4):
                pos = 0
                while pos < buf_len:
                    end = min(pos + self.MAX_SAMPLES_FOR_UPDATE, buf_len)
                    self.on_waveform_update(6 + ch, self._psg_buffers[ch][pos:end])
                    pos = end

        # Reset buffer position (no need to clear arrays)
        self._buffer_pos = 0

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

    def _check_dac_change(self, port: int, addr: int, data: int):
        """Check for DAC enable/disable in YM2612 writes."""
        # Register 0x2B on port 0 controls DAC enable (bit 7)
        if port == 0 and addr == 0x2B:
            new_dac_state = bool(data & 0x80)
            if new_dac_state != self._dac_enabled:
                self._dac_enabled = new_dac_state
                if self.on_dac_mode_change:
                    self.on_dac_mode_change(new_dac_state)

    def _check_fm_frequency(self, port: int, addr: int, data: int):
        """Track FM frequency changes and send pitch updates."""
        # Frequency registers: A0-A2 (low byte), A4-A6 (high byte + block)
        # Port 0 = channels 0-2, Port 1 = channels 3-5
        base_ch = 0 if port == 0 else 3

        if 0xA0 <= addr <= 0xA2:
            # Low byte of fnum
            ch = base_ch + (addr - 0xA0)
            self._fm_fnum[ch] = (self._fm_fnum[ch] & 0x700) | data
            self._update_fm_pitch(ch)
        elif 0xA4 <= addr <= 0xA6:
            # High byte of fnum + block
            ch = base_ch + (addr - 0xA4)
            self._fm_fnum[ch] = (self._fm_fnum[ch] & 0xFF) | ((data & 0x07) << 8)
            self._fm_block[ch] = (data >> 3) & 0x07
            self._update_fm_pitch(ch)

    def _update_fm_pitch(self, channel: int):
        """Calculate fractional MIDI pitch from FM fnum/block and send update."""
        import math
        fnum = self._fm_fnum[channel]
        block = self._fm_block[channel]

        if fnum == 0:
            return

        # YM2612 frequency: F = (fnum * 7670453) / (144 * 2^(21-block))
        freq = fnum * 0.02548 * (1 << block)

        # Convert to fractional MIDI pitch: MIDI = 69 + 12 * log2(freq / 440)
        if freq > 20:
            pitch = 69.0 + 12.0 * math.log2(freq / 440.0)
            if self.on_pitch_change:
                self.on_pitch_change(channel, pitch)

    def _check_psg_frequency(self, value: int):
        """Track PSG frequency changes and send pitch updates."""
        if value & 0x80:  # Latch byte
            channel = (value >> 5) & 0x03
            is_volume = bool(value & 0x10)

            self._psg_latch_channel = channel
            self._psg_latch_is_volume = is_volume

            if not is_volume and channel < 3:  # Tone register, not noise
                # Low 4 bits of frequency
                self._psg_tone[channel] = (self._psg_tone[channel] & 0x3F0) | (value & 0x0F)
                self._update_psg_pitch(channel)
        else:  # Data byte (high bits)
            if self._psg_latch_channel < 3 and not self._psg_latch_is_volume:
                self._psg_tone[self._psg_latch_channel] = \
                    (self._psg_tone[self._psg_latch_channel] & 0x00F) | ((value & 0x3F) << 4)
                self._update_psg_pitch(self._psg_latch_channel)

    def _update_psg_pitch(self, channel: int):
        """Calculate fractional MIDI pitch from PSG tone value and send update."""
        import math
        tone = self._psg_tone[channel]

        if tone == 0:
            return

        # PSG frequency: F = 3579545 / (32 * tone)
        freq = 3579545.0 / (32.0 * tone)

        # Convert to fractional MIDI pitch
        if freq > 20:
            pitch = 69.0 + 12.0 * math.log2(freq / 440.0)
            if self.on_pitch_change:
                self.on_pitch_change(6 + channel, pitch)  # PSG channels are 6-8

    def is_dac_enabled(self) -> bool:
        """Check if DAC mode is currently enabled."""
        return self._dac_enabled

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
