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

    # Noise tap bits for LFSR feedback
    # SN76489 uses a 16-bit LFSR with taps at bits 0 and 3 for white noise
    NOISE_TAPS_WHITE = 0x0009  # Bits 0 and 3
    NOISE_TAPS_PERIODIC = 0x0001  # Bit 0 only (periodic noise)

    def __init__(self):
        # Tone registers (10-bit frequency dividers)
        self.tone_regs = [0, 0, 0, 0]

        # Attenuation registers (4-bit, 0=loudest, 15=silent)
        self.attenuation = [15, 15, 15, 15]  # Start silent

        # Noise register
        self.noise_reg = 0  # Bits 0-1: shift rate, Bit 2: 0=periodic, 1=white

        # Phase accumulators for waveform generation (0.0 to 1.0)
        self.phase = [0.0, 0.0, 0.0]
        self.tone_outputs = [1, 1, 1]  # Current output state (1 or -1)

        # Noise state - proper LFSR implementation
        self.noise_lfsr = 0x8000  # 16-bit LFSR, start with bit 15 set
        self.noise_counter = 0.0
        self.noise_output = 1  # Current output state (1 or -1)

        # Current latch (which register is being addressed)
        self._latch_type = 0  # 0=tone, 1=attenuation
        self._latch_channel = 0

    def _get_noise_shift_rate(self) -> float:
        """Get the noise shift rate in Hz based on noise register."""
        shift_rate_bits = self.noise_reg & 0x03

        if shift_rate_bits == 0:
            # N/512 - highest frequency noise
            return self.CLOCK / 512.0
        elif shift_rate_bits == 1:
            # N/1024
            return self.CLOCK / 1024.0
        elif shift_rate_bits == 2:
            # N/2048
            return self.CLOCK / 2048.0
        else:
            # Use channel 2's frequency (shift_rate_bits == 3)
            if self.tone_regs[2] > 0:
                return self.CLOCK / (32.0 * self.tone_regs[2])
            return 0.0

    def _shift_lfsr(self):
        """Shift the LFSR and return new output bit."""
        # Determine tap mask based on noise type
        is_white = (self.noise_reg & 0x04) != 0
        taps = self.NOISE_TAPS_WHITE if is_white else self.NOISE_TAPS_PERIODIC

        # Calculate feedback bit (XOR of tapped bits, or just bit 0 for periodic)
        if is_white:
            # Parity of bits 0 and 3
            feedback = ((self.noise_lfsr & taps) != 0) and \
                      (bin(self.noise_lfsr & taps).count('1') & 1)
            feedback = 1 if (bin(self.noise_lfsr & taps).count('1') & 1) else 0
        else:
            # Just bit 0 for periodic noise
            feedback = self.noise_lfsr & 1

        # Shift right and insert feedback at bit 15
        self.noise_lfsr = ((self.noise_lfsr >> 1) | (feedback << 15)) & 0xFFFF

        # Output is bit 0
        self.noise_output = 1 if (self.noise_lfsr & 1) else -1

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
                    # Noise register - writing resets the LFSR
                    self.noise_reg = data & 0x07
                    self.noise_lfsr = 0x8000  # Reset LFSR
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

        # Generate tone channels (vectorized for performance)
        for ch in range(3):
            volume = self.get_volume(ch)

            if volume > 0 and self.tone_regs[ch] > 0:
                freq = self.get_frequency(ch)
                phase_inc = freq / self.SAMPLE_RATE

                # Vectorized phase calculation
                t = np.arange(num_samples, dtype=np.float32)
                phases = (self.phase[ch] + t * phase_inc) % 1.0

                # Square wave: +volume for phase < 0.5, -volume otherwise
                samples = np.where(phases < 0.5, volume, -volume).astype(np.float32)

                # Update phase for next call
                self.phase[ch] = (self.phase[ch] + num_samples * phase_inc) % 1.0
            else:
                samples = np.zeros(num_samples, dtype=np.float32)

            outputs.append(samples)

        # Generate noise channel (simplified for performance)
        # Full LFSR emulation is too slow - use cached random for visualization
        volume = self.get_volume(3)
        if volume > 0:
            shift_rate = self._get_noise_shift_rate()
            if shift_rate > 0:
                phase_inc = shift_rate / self.SAMPLE_RATE

                # Calculate how many LFSR shifts happen in this batch
                total_phase = self.noise_counter + num_samples * phase_inc
                num_shifts = int(total_phase)

                # Generate noise using vectorized approach
                # Each sample holds until next shift
                if num_shifts > 0 and num_samples > 0:
                    # Pre-generate shift points
                    t = np.arange(num_samples, dtype=np.float32)
                    shift_indices = ((self.noise_counter + t * phase_inc) // 1.0).astype(np.int32)

                    # Generate noise values for each unique shift
                    unique_shifts = np.unique(shift_indices)
                    noise_values = np.zeros(len(unique_shifts) + 1, dtype=np.float32)
                    noise_values[0] = self.noise_output * volume

                    for i, _ in enumerate(unique_shifts):
                        self._shift_lfsr()
                        if i + 1 < len(noise_values):
                            noise_values[i + 1] = self.noise_output * volume

                    # Map shift indices to noise values
                    noise_samples = noise_values[np.minimum(shift_indices, len(noise_values) - 1)]
                else:
                    noise_samples = np.full(num_samples, self.noise_output * volume, dtype=np.float32)

                self.noise_counter = total_phase % 1.0
            else:
                noise_samples = np.full(num_samples, self.noise_output * volume, dtype=np.float32)
        else:
            noise_samples = np.zeros(num_samples, dtype=np.float32)

        outputs.append(noise_samples)

        return tuple(outputs)

    def reset(self):
        """Reset the PSG to initial state."""
        self.tone_regs = [0, 0, 0, 0]
        self.attenuation = [15, 15, 15, 15]
        self.noise_reg = 0
        self.phase = [0.0, 0.0, 0.0]
        self.tone_outputs = [1, 1, 1]
        self.noise_lfsr = 0x8000  # Reset LFSR
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
