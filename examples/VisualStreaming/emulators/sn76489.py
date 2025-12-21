"""
SN76489 PSG Emulator for visualization.

The SN76489 is a simple 4-channel sound chip:
- Channels 0-2: Square wave tone generators
- Channel 3: Noise generator (periodic or white noise)

This emulator tracks register state and generates approximate waveforms
for visualization purposes.
"""

import numpy as np
from typing import Tuple


class SN76489:
    """SN76489 PSG emulator for waveform visualization."""

    # Clock frequency (NTSC Genesis)
    CLOCK = 3579545

    # Sample rate for output
    SAMPLE_RATE = 44100

    # Number of channels
    NUM_CHANNELS = 4  # 3 tone + 1 noise

    def __init__(self):
        # Tone registers (10-bit frequency dividers)
        self.tone_regs = [0, 0, 0, 0]

        # Attenuation registers (4-bit, 0=loudest, 15=silent)
        self.attenuation = [15, 15, 15, 15]  # Start silent

        # Noise register
        self.noise_reg = 0  # Bits 0-1: shift rate, Bit 2: 0=periodic, 1=white

        # Internal state for waveform generation
        self.tone_counters = [0.0, 0.0, 0.0]
        self.tone_outputs = [1, 1, 1]  # Current output state (1 or -1)

        # Noise state
        self.noise_shift = 0x8000  # 16-bit LFSR
        self.noise_counter = 0.0
        self.noise_output = 1

        # Current latch (which register is being addressed)
        self._latch_type = 0  # 0=tone, 1=attenuation
        self._latch_channel = 0

    def write(self, data: int):
        """
        Write a byte to the PSG.

        Latch byte format: 1 cc t dddd
            - cc = channel (0-3)
            - t = type (0=tone/noise, 1=attenuation)
            - dddd = data (low 4 bits)

        Data byte format: 0 x dddddd
            - dddddd = data (high 6 bits for tone, or full value for data byte)
        """
        if data & 0x80:
            # Latch byte
            self._latch_channel = (data >> 5) & 0x03
            self._latch_type = (data >> 4) & 0x01

            if self._latch_type == 1:
                # Attenuation
                self.attenuation[self._latch_channel] = data & 0x0F
            else:
                if self._latch_channel == 3:
                    # Noise register
                    self.noise_reg = data & 0x07
                else:
                    # Tone register (low 4 bits)
                    self.tone_regs[self._latch_channel] = \
                        (self.tone_regs[self._latch_channel] & 0x3F0) | (data & 0x0F)
        else:
            # Data byte
            if self._latch_type == 0 and self._latch_channel < 3:
                # Tone register (high 6 bits)
                self.tone_regs[self._latch_channel] = \
                    (self.tone_regs[self._latch_channel] & 0x00F) | ((data & 0x3F) << 4)

    def get_frequency(self, channel: int) -> float:
        """Get the frequency of a tone channel in Hz."""
        if channel < 0 or channel >= 3:
            return 0.0

        reg = self.tone_regs[channel]
        if reg == 0:
            return 0.0

        # Frequency = Clock / (32 * reg)
        return self.CLOCK / (32.0 * reg)

    def get_volume(self, channel: int) -> float:
        """Get the volume of a channel (0.0 to 1.0)."""
        if channel < 0 or channel >= self.NUM_CHANNELS:
            return 0.0

        atten = self.attenuation[channel]
        if atten == 15:
            return 0.0

        # Each step is approximately 2dB
        # Volume = 10^(-atten * 2 / 20) = 10^(-atten / 10)
        return 10.0 ** (-atten / 10.0)

    def is_active(self, channel: int) -> bool:
        """Check if a channel is producing sound."""
        if channel < 0 or channel >= self.NUM_CHANNELS:
            return False

        # Channel is active if not fully attenuated
        if self.attenuation[channel] >= 15:
            return False

        # Tone channels need a valid frequency
        if channel < 3:
            return self.tone_regs[channel] > 0

        # Noise channel is always "active" if not attenuated
        return True

    def generate_samples(self, num_samples: int) -> Tuple[np.ndarray, ...]:
        """
        Generate waveform samples for all channels.

        Returns:
            Tuple of 4 numpy arrays (channels 0-3), each with num_samples float32 values
            normalized to [-1.0, 1.0].
        """
        outputs = []

        # Generate tone channels
        for ch in range(3):
            samples = np.zeros(num_samples, dtype=np.float32)
            volume = self.get_volume(ch)

            if volume > 0 and self.tone_regs[ch] > 0:
                freq = self.get_frequency(ch)
                # Samples per half-period
                half_period = self.SAMPLE_RATE / (2.0 * freq) if freq > 0 else 0

                if half_period > 0:
                    for i in range(num_samples):
                        samples[i] = self.tone_outputs[ch] * volume
                        self.tone_counters[ch] += 1.0
                        if self.tone_counters[ch] >= half_period:
                            self.tone_counters[ch] -= half_period
                            self.tone_outputs[ch] = -self.tone_outputs[ch]

            outputs.append(samples)

        # Generate noise channel
        noise_samples = np.zeros(num_samples, dtype=np.float32)
        volume = self.get_volume(3)

        if volume > 0:
            # Noise shift rate
            shift_rate = self.noise_reg & 0x03
            is_white = bool(self.noise_reg & 0x04)

            # Calculate noise frequency
            if shift_rate == 3:
                # Use channel 2's frequency
                noise_freq = self.get_frequency(2)
            else:
                # Fixed frequencies
                noise_freq = self.CLOCK / (32.0 * (16 << shift_rate))

            half_period = self.SAMPLE_RATE / (2.0 * noise_freq) if noise_freq > 0 else 0

            if half_period > 0:
                for i in range(num_samples):
                    noise_samples[i] = (1 if self.noise_shift & 1 else -1) * volume
                    self.noise_counter += 1.0

                    if self.noise_counter >= half_period:
                        self.noise_counter -= half_period
                        # Shift LFSR
                        if is_white:
                            # White noise: XOR bits 0 and 3
                            feedback = ((self.noise_shift & 1) ^
                                       ((self.noise_shift >> 3) & 1))
                        else:
                            # Periodic noise: just bit 0
                            feedback = self.noise_shift & 1

                        self.noise_shift = (self.noise_shift >> 1) | (feedback << 15)

        outputs.append(noise_samples)

        return tuple(outputs)

    def reset(self):
        """Reset the PSG to initial state."""
        self.tone_regs = [0, 0, 0, 0]
        self.attenuation = [15, 15, 15, 15]
        self.noise_reg = 0
        self.tone_counters = [0.0, 0.0, 0.0]
        self.tone_outputs = [1, 1, 1]
        self.noise_shift = 0x8000
        self.noise_counter = 0.0
        self.noise_output = 1
        self._latch_type = 0
        self._latch_channel = 0


# Simple test
if __name__ == "__main__":
    psg = SN76489()

    # Set channel 0 to A4 (440 Hz), full volume
    # Frequency divider for 440 Hz: 3579545 / (32 * 440) = 254
    psg.write(0x80 | (254 & 0x0F))  # Latch + low 4 bits
    psg.write(254 >> 4)             # High 6 bits
    psg.write(0x90 | 0)             # Volume = 0 (max)

    print(f"Channel 0 frequency: {psg.get_frequency(0):.1f} Hz")
    print(f"Channel 0 volume: {psg.get_volume(0):.3f}")
    print(f"Channel 0 active: {psg.is_active(0)}")

    # Generate some samples
    samples = psg.generate_samples(100)
    print(f"Generated {len(samples[0])} samples for channel 0")
    print(f"Sample range: {samples[0].min():.3f} to {samples[0].max():.3f}")
