"""
ymfm-based YM2612 emulator with per-channel output.
"""

import numpy as np
from typing import Tuple
import os
import sys

# Add parent directory to path to find the compiled extension
_this_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from _ymfm import YM2612 as _YM2612


class YM2612ymfm:
    """
    YM2612 emulator using ymfm with per-channel output.
    Drop-in replacement for other YM2612 emulators.
    """

    NUM_CHANNELS = 6
    SAMPLE_RATE = 44100

    def __init__(self):
        self._chip = _YM2612()

    def reset(self):
        """Reset the chip to initial state."""
        self._chip.reset()

    def write(self, port: int, addr: int, data: int):
        """
        Write to a YM2612 register.

        Args:
            port: Port number (0 or 1)
            addr: Register address
            data: Data byte
        """
        self._chip.write(port, addr, data)

    def generate_samples(self, num_samples: int) -> Tuple[np.ndarray, ...]:
        """
        Generate audio samples for all channels (per-channel mono for visualization).
        Also captures stereo mix internally - call get_stereo_buffer() to retrieve.

        Args:
            num_samples: Number of samples to generate

        Returns:
            Tuple of 6 numpy arrays (one per FM channel), each float32
        """
        if num_samples <= 0:
            return tuple(np.zeros(0, dtype=np.float32) for _ in range(self.NUM_CHANNELS))

        result = self._chip.generate_samples(num_samples)

        # Convert to numpy arrays with correct type
        return tuple(np.array(result[ch], dtype=np.float32) for ch in range(self.NUM_CHANNELS))

    def get_stereo_buffer(self) -> np.ndarray:
        """
        Get stereo audio captured during the last generate_samples() call.
        This is the full mix with proper panning - use for audio output.

        Returns:
            numpy array of shape (num_samples, 2) with L/R channels, float32
        """
        return self._chip.get_stereo_buffer()

    def is_active(self, channel: int) -> bool:
        """Check if a channel is currently producing output."""
        return self._chip.is_active(channel)

    def is_dac_enabled(self) -> bool:
        """Check if DAC mode is enabled (replaces FM channel 6)."""
        return self._chip.is_dac_enabled()


# Test
if __name__ == "__main__":
    print("Testing YM2612ymfm...")

    ym = YM2612ymfm()

    # Set up a test tone on channel 0
    ym.write(0, 0xB0, 0x07)  # Algo 7
    ym.write(0, 0xA4, 0x22)  # Block 4
    ym.write(0, 0xA0, 0x69)  # Fnum

    for slot in range(4):
        base = 0x30 + slot * 4
        ym.write(0, base, 0x01)          # MUL=1
        ym.write(0, base + 0x10, 0x00)   # TL=0
        ym.write(0, base + 0x20, 0x1F)   # AR=31
        ym.write(0, base + 0x30, 0x00)   # D1R=0
        ym.write(0, base + 0x40, 0x00)   # D2R=0
        ym.write(0, base + 0x50, 0x0F)   # SL=0, RR=15

    ym.write(0, 0x28, 0xF0)  # Key on

    samples = ym.generate_samples(2048)
    print(f"Generated {len(samples)} channels")
    for ch in range(6):
        arr = samples[ch]
        print(f"  Ch{ch}: max={np.abs(arr).max():.4f}, samples={len(arr)}")

        # Check for zero crossings (periodic waveform)
        crossings = np.sum(np.diff(np.sign(arr)) != 0)
        print(f"         zero crossings: {crossings}")

    print("\nTest complete!")
