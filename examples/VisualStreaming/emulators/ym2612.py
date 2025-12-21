"""
YM2612 FM Emulator for visualization.

This is a PLACEHOLDER implementation that tracks register state and generates
approximate waveforms. For accurate FM synthesis, this will be replaced with
a wrapper around the ymfm library.

The YM2612 has:
- 6 FM channels (channel 6 can be used for DAC)
- Each channel has 4 operators
- 8 different FM algorithms
"""

import numpy as np
from typing import Tuple, Optional


class YM2612:
    """YM2612 FM emulator placeholder for waveform visualization."""

    # Clock frequency (NTSC Genesis)
    CLOCK = 7670453

    # Sample rate for output
    SAMPLE_RATE = 44100

    # Number of channels
    NUM_CHANNELS = 6

    def __init__(self):
        # Key-on state for each channel
        self.key_on = [False] * self.NUM_CHANNELS

        # Frequency data for each channel (F-num and block)
        self.fnum = [0] * self.NUM_CHANNELS
        self.block = [0] * self.NUM_CHANNELS

        # Total level (volume) for each operator (4 per channel)
        # Lower TL = louder
        self.total_level = [[127] * 4 for _ in range(self.NUM_CHANNELS)]

        # Algorithm for each channel (0-7)
        self.algorithm = [0] * self.NUM_CHANNELS

        # DAC mode
        self.dac_enabled = False
        self.dac_data = 0x80  # Center value

        # Internal phase accumulators for basic waveform generation
        self.phase = [0.0] * self.NUM_CHANNELS

        # Envelope state (simplified: 0=off, 1=attack, 2=sustain, 3=release)
        self.envelope_state = [0] * self.NUM_CHANNELS
        self.envelope_level = [0.0] * self.NUM_CHANNELS

        # DAC sample buffer for visualization
        self.dac_buffer = np.zeros(64, dtype=np.float32)
        self.dac_buffer_pos = 0

    def write(self, port: int, addr: int, data: int):
        """
        Write to a YM2612 register.

        Args:
            port: 0 for port 0 (channels 1-3), 1 for port 1 (channels 4-6)
            addr: Register address
            data: Register value
        """
        # Channel offset based on port
        ch_offset = 3 if port == 1 else 0

        if addr == 0x28:
            # Key on/off
            channel = data & 0x07
            if channel >= 4:
                channel = channel - 4 + 3  # Map 4-6 to 3-5

            if channel < self.NUM_CHANNELS:
                key_on = (data & 0xF0) != 0
                self.key_on[channel] = key_on

                if key_on:
                    self.envelope_state[channel] = 1  # Attack
                    self.envelope_level[channel] = 0.0
                else:
                    self.envelope_state[channel] = 3  # Release

        elif addr == 0x2A:
            # DAC data
            self.dac_data = data
            # Add to DAC buffer for visualization
            normalized = (data - 128) / 128.0  # Normalize to [-1, 1]
            self.dac_buffer[self.dac_buffer_pos] = normalized
            self.dac_buffer_pos = (self.dac_buffer_pos + 1) % len(self.dac_buffer)

        elif addr == 0x2B:
            # DAC enable
            self.dac_enabled = bool(data & 0x80)

        elif 0xA0 <= addr <= 0xA2:
            # F-num low byte
            channel = (addr & 0x03) + ch_offset
            if channel < self.NUM_CHANNELS:
                self.fnum[channel] = (self.fnum[channel] & 0x700) | data

        elif 0xA4 <= addr <= 0xA6:
            # F-num high + block
            channel = (addr & 0x03) + ch_offset
            if channel < self.NUM_CHANNELS:
                self.fnum[channel] = (self.fnum[channel] & 0x0FF) | ((data & 0x07) << 8)
                self.block[channel] = (data >> 3) & 0x07

        elif 0xB0 <= addr <= 0xB2:
            # Algorithm + Feedback
            channel = (addr & 0x03) + ch_offset
            if channel < self.NUM_CHANNELS:
                self.algorithm[channel] = data & 0x07

        elif 0x40 <= addr <= 0x4F:
            # Total Level (4 operators per channel)
            op = (addr >> 2) & 0x03
            ch = (addr & 0x03) + ch_offset
            if ch < self.NUM_CHANNELS:
                self.total_level[ch][op] = data & 0x7F

    def get_frequency(self, channel: int) -> float:
        """Get the frequency of a channel in Hz."""
        if channel < 0 or channel >= self.NUM_CHANNELS:
            return 0.0

        fnum = self.fnum[channel]
        block = self.block[channel]

        if fnum == 0:
            return 0.0

        # Frequency = (CLOCK * fnum * 2^block) / (144 * 2^20)
        return (self.CLOCK * fnum * (1 << block)) / (144.0 * (1 << 20))

    def get_volume(self, channel: int) -> float:
        """Get approximate volume of a channel (0.0 to 1.0)."""
        if channel < 0 or channel >= self.NUM_CHANNELS:
            return 0.0

        if not self.key_on[channel]:
            return 0.0

        # Use carrier operator's total level based on algorithm
        # This is a simplification - different algorithms have different carriers
        algorithm = self.algorithm[channel]

        # Carrier operators by algorithm (simplified)
        carriers = {
            0: [3],           # Only op4
            1: [3],           # Only op4
            2: [3],           # Only op4
            3: [3],           # Only op4
            4: [1, 3],        # Op2 and op4
            5: [1, 2, 3],     # Op2, op3, op4
            6: [1, 2, 3],     # Op2, op3, op4
            7: [0, 1, 2, 3],  # All operators
        }

        carrier_ops = carriers.get(algorithm, [3])
        min_tl = min(self.total_level[channel][op] for op in carrier_ops)

        # Convert TL to linear volume (TL 0 = max, TL 127 = min)
        # Each unit is 0.75 dB
        db = min_tl * 0.75
        return 10.0 ** (-db / 20.0)

    def is_active(self, channel: int) -> bool:
        """Check if a channel is producing sound."""
        if channel < 0 or channel >= self.NUM_CHANNELS:
            return False

        # DAC overrides channel 6
        if channel == 5 and self.dac_enabled:
            return True

        return self.key_on[channel] and self.fnum[channel] > 0

    def generate_samples(self, num_samples: int) -> Tuple[np.ndarray, ...]:
        """
        Generate waveform samples for all channels.

        This is a PLACEHOLDER that generates simple sine waves at the
        correct frequencies. For accurate FM synthesis, use ymfm.

        Returns:
            Tuple of 6 numpy arrays (channels 0-5), each with num_samples float32 values
            normalized to [-1.0, 1.0].
        """
        outputs = []

        for ch in range(self.NUM_CHANNELS):
            samples = np.zeros(num_samples, dtype=np.float32)

            # Special handling for DAC on channel 6
            if ch == 5 and self.dac_enabled:
                # Return recent DAC samples
                for i in range(num_samples):
                    idx = (self.dac_buffer_pos - num_samples + i) % len(self.dac_buffer)
                    samples[i] = self.dac_buffer[idx]
                outputs.append(samples)
                continue

            volume = self.get_volume(ch)
            if volume > 0 and self.fnum[ch] > 0:
                freq = self.get_frequency(ch)
                phase_inc = 2.0 * np.pi * freq / self.SAMPLE_RATE

                # Update envelope (simplified)
                if self.envelope_state[ch] == 1:  # Attack
                    self.envelope_level[ch] = min(1.0, self.envelope_level[ch] + 0.1)
                    if self.envelope_level[ch] >= 1.0:
                        self.envelope_state[ch] = 2  # Sustain
                elif self.envelope_state[ch] == 3:  # Release
                    self.envelope_level[ch] = max(0.0, self.envelope_level[ch] - 0.05)

                env = self.envelope_level[ch]

                # Generate sine wave (placeholder for FM)
                # In reality, FM synthesis is much more complex
                for i in range(num_samples):
                    # Simple 2-operator FM approximation
                    mod = np.sin(self.phase[ch] * 2.0) * 0.5
                    samples[i] = np.sin(self.phase[ch] + mod) * volume * env
                    self.phase[ch] += phase_inc

                # Keep phase in reasonable range
                if self.phase[ch] > 2.0 * np.pi * 1000:
                    self.phase[ch] -= 2.0 * np.pi * 1000

            outputs.append(samples)

        return tuple(outputs)

    def reset(self):
        """Reset the YM2612 to initial state."""
        self.key_on = [False] * self.NUM_CHANNELS
        self.fnum = [0] * self.NUM_CHANNELS
        self.block = [0] * self.NUM_CHANNELS
        self.total_level = [[127] * 4 for _ in range(self.NUM_CHANNELS)]
        self.algorithm = [0] * self.NUM_CHANNELS
        self.dac_enabled = False
        self.dac_data = 0x80
        self.phase = [0.0] * self.NUM_CHANNELS
        self.envelope_state = [0] * self.NUM_CHANNELS
        self.envelope_level = [0.0] * self.NUM_CHANNELS
        self.dac_buffer = np.zeros(64, dtype=np.float32)
        self.dac_buffer_pos = 0


# Simple test
if __name__ == "__main__":
    ym = YM2612()

    # Set up channel 0 with a frequency and key on
    # F-num for ~440 Hz: approximately 1100 at block 4
    ym.write(0, 0xA4, (4 << 3) | (1100 >> 8))  # Block + F-num high
    ym.write(0, 0xA0, 1100 & 0xFF)              # F-num low
    ym.write(0, 0x40, 20)                       # TL for op1
    ym.write(0, 0x28, 0xF0)                     # Key on channel 0

    print(f"Channel 0 frequency: {ym.get_frequency(0):.1f} Hz")
    print(f"Channel 0 volume: {ym.get_volume(0):.3f}")
    print(f"Channel 0 active: {ym.is_active(0)}")

    # Generate some samples
    samples = ym.generate_samples(100)
    print(f"Generated {len(samples[0])} samples for channel 0")
    print(f"Sample range: {samples[0].min():.3f} to {samples[0].max():.3f}")
