"""
YM2612 FM Emulator for visualization.

Based on Genesis Plus GX ym2612.c implementation by Eke-Eke,
which is derived from MAME fm.c by Jarek Burczynski & Tatsuyuki Satoh.

This emulator prioritizes accuracy for waveform visualization.
"""

import numpy as np
from typing import Tuple
import math

# Envelope generator states
EG_ATTACK = 0
EG_DECAY = 1
EG_SUSTAIN = 2
EG_RELEASE = 3
EG_OFF = 4

# Maximum envelope attenuation (10-bit, 0 = max volume, 1023 = silent)
MAX_ATT = 1023
ENV_BITS = 10
ENV_LEN = 1 << ENV_BITS  # 1024

# Sine table size (10-bit phase index)
SIN_BITS = 10
SIN_LEN = 1 << SIN_BITS  # 1024
SIN_MASK = SIN_LEN - 1

# TL table size
TL_RES_LEN = 256
TL_TAB_LEN = 13 * 2 * TL_RES_LEN  # 13 octaves * 2 (for sign) * 256

# Global tables (initialized once)
SIN_TAB = None  # Logarithmic sine table
TL_TAB = None   # Power/amplitude table


def _init_tables():
    """Initialize sine and power tables based on Genesis Plus GX."""
    global SIN_TAB, TL_TAB

    if SIN_TAB is not None:
        return

    # Environment step size (each step = 0.09375 dB, 96dB range / 1024)
    ENV_STEP = 0.09375

    # Build TL table (power table for envelope->amplitude conversion)
    # This converts logarithmic attenuation to linear amplitude
    TL_TAB = np.zeros(TL_TAB_LEN, dtype=np.int32)

    for x in range(TL_RES_LEN):
        # Compute linear amplitude from log attenuation
        m = (1 << 16) / (2.0 ** ((x + 1) * (ENV_STEP / 4.0) / 8.0))
        n = int(m) >> 4  # 12 bits

        # Positive and negative entries
        TL_TAB[x * 2 + 0] = n << 2  # 14 bits, positive
        TL_TAB[x * 2 + 1] = -(n << 2)  # negative

        # Replicate for 13 octaves (envelope ranges)
        for i in range(1, 13):
            TL_TAB[i * 2 * TL_RES_LEN + x * 2 + 0] = TL_TAB[x * 2 + 0] >> i
            TL_TAB[i * 2 * TL_RES_LEN + x * 2 + 1] = -(TL_TAB[i * 2 * TL_RES_LEN + x * 2 + 0])

    # Build sine table (logarithmic, stores dB values)
    # Format: each entry is (attenuation * 2) + sign_bit
    SIN_TAB = np.zeros(SIN_LEN, dtype=np.uint32)

    for i in range(SIN_LEN):
        # Compute sine value (only first quarter is computed, rest mirrored)
        m = math.sin(((i * 2) + 1) * math.pi / SIN_LEN)

        # Convert to logarithmic (decibels)
        if m > 0:
            o = 8 * math.log(1.0 / m) / math.log(2)  # log base 2
            o = o / (ENV_STEP / 4.0)
            n = int(o)
            SIN_TAB[i] = n * 2 + 0  # positive
        else:
            o = 8 * math.log(1.0 / -m) / math.log(2)
            o = o / (ENV_STEP / 4.0)
            n = int(o)
            SIN_TAB[i] = n * 2 + 1  # negative


# Envelope timing constants
# At 44100 Hz sample rate, the YM2612's internal update rate is ~53kHz
# We simplify by using fixed update intervals based on rate
# Rate 0 = never update, Rate 31 = update every sample
# The real chip has complex counter-based timing; we approximate

def get_envelope_period(rate: int) -> int:
    """Get number of samples between envelope updates for a given rate."""
    if rate == 0:
        return 999999  # Never update
    # Approximate: rate 31 updates every sample, rate 1 updates every ~4000 samples
    # Using exponential scaling to match feel of real chip
    if rate >= 28:
        return 1
    elif rate >= 24:
        return 2
    elif rate >= 20:
        return 4
    elif rate >= 16:
        return 8
    elif rate >= 12:
        return 16
    elif rate >= 8:
        return 32
    elif rate >= 4:
        return 64
    else:
        return 128


class Operator:
    """Single FM operator with accurate envelope generator."""

    # Detune table from Genesis Plus GX (simplified - key code 0 only)
    DETUNE_TABLE = [
        [0, 0, 1, 2],   # DT=0-3 for lower freqs
        [0, 0, 1, 2],   # Higher variations omitted for brevity
    ]

    def __init__(self):
        # Register values
        self.detune = 0  # 0-7
        self.multiple = 1  # 0-15 (0 = 0.5)
        self.total_level = 127  # 0-127 (0 = max volume)
        self.key_scale = 0  # 0-3
        self.attack_rate = 0  # 0-31
        self.decay_rate = 0  # 0-31
        self.sustain_rate = 0  # 0-31 (D2R)
        self.sustain_level = 0  # 0-15
        self.release_rate = 0  # 0-15
        self.ssg_eg = 0

        # Envelope state
        self.eg_state = EG_OFF
        self.eg_level = MAX_ATT  # 0 = max volume, 1023 = silent
        self.eg_counter = 0  # Per-operator envelope counter

        # Phase state (20-bit accumulator)
        self.phase = 0
        self.phase_inc = 0  # Phase increment per sample

        # Key code for rate scaling (derived from block/fnum)
        self.key_code = 0

        # Pre-calculated TL in envelope units (TL << 3)
        self.tl = 127 << 3

        # Envelope increment per update (calculated from rate)
        self._eg_period = 1
        self._eg_inc = 1

    def key_on(self):
        """Trigger note on."""
        self.eg_state = EG_ATTACK
        self.eg_level = MAX_ATT  # Start from max attenuation
        self.phase = 0

    def key_off(self):
        """Trigger note off."""
        if self.eg_state != EG_OFF:
            self.eg_state = EG_RELEASE

    def set_total_level(self, tl: int):
        """Set total level with proper scaling."""
        self.total_level = tl & 0x7F
        self.tl = self.total_level << 3  # Scale to envelope units

    def calc_phase_inc(self, fnum: int, block: int, clock: float, sample_rate: float):
        """Calculate phase increment based on frequency registers."""
        if fnum == 0:
            self.phase_inc = 0
            return

        # Apply multiple
        mult = self.multiple if self.multiple > 0 else 0.5

        # Base frequency calculation
        # phase_inc = (fnum * 2^block * multiple) / (144 * 2^20) * (2^20 / sample_rate) * clock
        # Simplified: phase_inc = fnum * 2^block * mult * clock / (144 * sample_rate)
        freq = (clock * fnum * (1 << block) * mult) / (144.0 * (1 << 20))

        # Convert to 20-bit phase increment
        self.phase_inc = int((freq / sample_rate) * (1 << 20))

        # Key code for envelope rate scaling (block * 2 + fnum MSB)
        self.key_code = min(31, (block << 1) | ((fnum >> 10) & 1))

    def _get_rate(self, base_rate: int) -> int:
        """Calculate effective rate with key scaling."""
        if base_rate == 0:
            return 0

        # Effective rate = base_rate * 2 + key_scale_shift
        ks_shift = 3 - self.key_scale if self.key_scale < 3 else 0
        rate = (base_rate << 1) + (self.key_code >> ks_shift)
        return min(63, rate)

    def update_envelope(self, eg_cnt: int):
        """Update envelope generator."""
        if self.eg_state == EG_OFF:
            self.eg_level = MAX_ATT
            return

        # Increment per-operator counter
        self.eg_counter += 1

        # Get rate for current state
        if self.eg_state == EG_ATTACK:
            rate = min(31, self.attack_rate)
        elif self.eg_state == EG_DECAY:
            rate = min(31, self.decay_rate)
        elif self.eg_state == EG_SUSTAIN:
            rate = min(31, self.sustain_rate)
        else:  # EG_RELEASE
            rate = min(31, (self.release_rate << 1) + 1)

        # Get update period for this rate
        period = get_envelope_period(rate)

        # Only update on period boundaries
        if self.eg_counter < period:
            return
        self.eg_counter = 0

        if self.eg_state == EG_ATTACK:
            # Attack: exponential (level decreases toward 0)
            # Faster attack when further from target
            step = max(1, self.eg_level >> 4)
            self.eg_level = max(0, self.eg_level - step)
            if self.eg_level <= 0:
                self.eg_level = 0
                self.eg_state = EG_DECAY

        elif self.eg_state == EG_DECAY:
            # Decay to sustain level
            self.eg_level += 1

            # Sustain level: SL is 4-bit (0-15), each unit = 64 attenuation
            sl = (self.sustain_level << 6) if self.sustain_level < 15 else MAX_ATT
            if self.eg_level >= sl:
                self.eg_level = sl
                self.eg_state = EG_SUSTAIN

        elif self.eg_state == EG_SUSTAIN:
            # Sustain decay (D2R)
            if self.sustain_rate > 0:
                self.eg_level += 1
                if self.eg_level >= MAX_ATT:
                    self.eg_level = MAX_ATT

        else:  # EG_RELEASE
            # Release
            self.eg_level += 2
            if self.eg_level >= MAX_ATT:
                self.eg_level = MAX_ATT
                self.eg_state = EG_OFF

    def get_output(self, modulation: int = 0) -> float:
        """
        Generate one sample of operator output.

        Args:
            modulation: Phase modulation input (in phase units)

        Returns:
            Output value (-1.0 to 1.0)
        """
        if self.eg_state == EG_OFF or SIN_TAB is None or TL_TAB is None:
            return 0.0

        # Calculate phase with modulation
        phase_idx = ((self.phase >> (20 - SIN_BITS)) + (modulation >> 1)) & SIN_MASK

        # Get sine table value (logarithmic)
        sin_val = SIN_TAB[phase_idx]

        # Combine with envelope: env (10-bit) << 3 = 13-bit, + sin (already scaled)
        env = self.eg_level + self.tl
        if env >= MAX_ATT:
            return 0.0

        p = (env << 3) + sin_val

        # Lookup in power table
        if p >= TL_TAB_LEN:
            return 0.0

        output = TL_TAB[p]

        # Normalize to -1.0 to 1.0 (TL_TAB outputs 14-bit values)
        return output / 16384.0

    def advance_phase(self):
        """Advance phase accumulator."""
        self.phase = (self.phase + self.phase_inc) & 0xFFFFF  # 20-bit wrap


class Channel:
    """Single FM channel with 4 operators."""

    # Algorithm connection patterns
    ALGORITHMS = [
        # Algo 0: OP1 -> OP2 -> OP3 -> OP4 -> out
        {'carriers': [3], 'mods': [(0, 1), (1, 2), (2, 3)]},
        # Algo 1: (OP1 + OP2) -> OP3 -> OP4 -> out
        {'carriers': [3], 'mods': [(0, 2), (1, 2), (2, 3)]},
        # Algo 2: (OP1 + (OP2 -> OP3)) -> OP4 -> out
        {'carriers': [3], 'mods': [(1, 2), (0, 3), (2, 3)]},
        # Algo 3: ((OP1 -> OP2) + OP3) -> OP4 -> out
        {'carriers': [3], 'mods': [(0, 1), (1, 3), (2, 3)]},
        # Algo 4: (OP1 -> OP2) + (OP3 -> OP4) -> out
        {'carriers': [1, 3], 'mods': [(0, 1), (2, 3)]},
        # Algo 5: OP1 -> (OP2 + OP3 + OP4) -> out
        {'carriers': [1, 2, 3], 'mods': [(0, 1), (0, 2), (0, 3)]},
        # Algo 6: (OP1 -> OP2) + OP3 + OP4 -> out
        {'carriers': [1, 2, 3], 'mods': [(0, 1)]},
        # Algo 7: OP1 + OP2 + OP3 + OP4 -> out
        {'carriers': [0, 1, 2, 3], 'mods': []},
    ]

    def __init__(self):
        self.ops = [Operator() for _ in range(4)]
        self.algorithm = 0
        self.feedback = 0
        self.fnum = 0
        self.block = 0
        self.key_on_state = False

        # Feedback history (for self-feedback on OP1)
        self.fb_out = [0, 0]

        # Global envelope counter (shared across operators)
        self.eg_counter = 0

    def key_on(self, op_mask: int):
        """Trigger key on for specified operators."""
        for i in range(4):
            if op_mask & (1 << (4 + i)):
                self.ops[i].key_on()
        self.key_on_state = op_mask != 0
        if self.key_on_state:
            self.fb_out = [0, 0]

    def key_off(self):
        """Trigger key off for all operators."""
        for op in self.ops:
            op.key_off()
        self.key_on_state = False

    def get_frequency(self, clock: float = 7670453) -> float:
        """Calculate base frequency in Hz."""
        if self.fnum == 0:
            return 0.0
        return (clock * self.fnum * (1 << self.block)) / (144.0 * (1 << 20))

    def update_phase_incs(self, sample_rate: float, clock: float = 7670453):
        """Update phase increments for all operators."""
        for op in self.ops:
            op.calc_phase_inc(self.fnum, self.block, clock, sample_rate)

    def generate_sample(self) -> float:
        """Generate one sample of channel output."""
        if self.fnum == 0:
            return 0.0

        algo = self.ALGORITHMS[self.algorithm]

        # Update envelope counter
        self.eg_counter += 1

        # Update envelopes for all operators
        for op in self.ops:
            op.update_envelope(self.eg_counter)

        # Calculate operator outputs with modulation
        op_out = [0] * 4

        # OP1 with feedback
        fb_mod = 0
        if self.feedback > 0:
            fb_mod = (self.fb_out[0] + self.fb_out[1]) >> (10 - self.feedback)

        op_out[0] = self.ops[0].get_output(fb_mod)
        self.fb_out[1] = self.fb_out[0]
        self.fb_out[0] = int(op_out[0] * 16384)  # Store as 14-bit for feedback

        # Process remaining operators in order with modulation
        for i in range(1, 4):
            mod = 0
            for src, dst in algo['mods']:
                if dst == i:
                    # Modulation amount scaled to phase units
                    mod += int(op_out[src] * 16384)  # 14-bit modulation
            op_out[i] = self.ops[i].get_output(mod)

        # Advance all phases
        for op in self.ops:
            op.advance_phase()

        # Mix carrier outputs
        output = sum(op_out[c] for c in algo['carriers'])
        num_carriers = len(algo['carriers'])
        if num_carriers > 1:
            output /= num_carriers

        return output


class YM2612:
    """YM2612 FM synthesizer emulator."""

    CLOCK = 7670453  # NTSC Genesis clock
    SAMPLE_RATE = 44100
    NUM_CHANNELS = 6

    def __init__(self):
        # Initialize global tables
        _init_tables()

        self.channels = [Channel() for _ in range(self.NUM_CHANNELS)]

        # DAC state
        self.dac_enabled = False
        self.dac_data = 0x80
        self.dac_buffer = np.zeros(1024, dtype=np.float32)
        self.dac_buffer_pos = 0

        # LFO state
        self.lfo_enabled = False
        self.lfo_freq = 0

    def write(self, port: int, addr: int, data: int):
        """Write to YM2612 register."""
        ch_offset = 3 if port == 1 else 0

        # Global registers
        if addr == 0x22:
            self.lfo_enabled = bool(data & 0x08)
            self.lfo_freq = data & 0x07

        elif addr == 0x28:
            # Key on/off
            ch = data & 0x07
            if ch >= 4:
                ch = ch - 4 + 3
            if ch < self.NUM_CHANNELS:
                if data & 0xF0:
                    self.channels[ch].key_on(data)
                    self.channels[ch].update_phase_incs(self.SAMPLE_RATE, self.CLOCK)
                else:
                    self.channels[ch].key_off()

        elif addr == 0x2A:
            # DAC data
            self.dac_data = data
            normalized = (data - 128) / 128.0
            self.dac_buffer[self.dac_buffer_pos] = normalized
            self.dac_buffer_pos = (self.dac_buffer_pos + 1) % len(self.dac_buffer)

        elif addr == 0x2B:
            self.dac_enabled = bool(data & 0x80)

        # Per-operator registers
        elif 0x30 <= addr <= 0x9F:
            ch = (addr & 0x03) + ch_offset
            if ch >= self.NUM_CHANNELS or (addr & 0x03) == 3:
                return

            # Operator mapping: slot 0,1,2,3 -> op 0,2,1,3
            op_idx = (addr >> 2) & 0x03
            op_map = [0, 2, 1, 3]
            op = op_map[op_idx]

            reg_group = (addr >> 4) & 0x0F

            if reg_group == 3:  # DT/MUL
                self.channels[ch].ops[op].detune = (data >> 4) & 0x07
                mult = data & 0x0F
                self.channels[ch].ops[op].multiple = mult if mult > 0 else 0.5

            elif reg_group == 4:  # TL
                self.channels[ch].ops[op].set_total_level(data & 0x7F)

            elif reg_group == 5:  # RS/AR
                self.channels[ch].ops[op].key_scale = (data >> 6) & 0x03
                self.channels[ch].ops[op].attack_rate = data & 0x1F

            elif reg_group == 6:  # AM/D1R
                self.channels[ch].ops[op].decay_rate = data & 0x1F

            elif reg_group == 7:  # D2R
                self.channels[ch].ops[op].sustain_rate = data & 0x1F

            elif reg_group == 8:  # SL/RR
                self.channels[ch].ops[op].sustain_level = (data >> 4) & 0x0F
                self.channels[ch].ops[op].release_rate = data & 0x0F

            elif reg_group == 9:  # SSG-EG
                self.channels[ch].ops[op].ssg_eg = data & 0x0F

        # Per-channel registers
        elif 0xA0 <= addr <= 0xA2:
            ch = (addr & 0x03) + ch_offset
            if ch < self.NUM_CHANNELS:
                self.channels[ch].fnum = (self.channels[ch].fnum & 0x700) | data

        elif 0xA4 <= addr <= 0xA6:
            ch = (addr & 0x03) + ch_offset
            if ch < self.NUM_CHANNELS:
                self.channels[ch].fnum = (self.channels[ch].fnum & 0xFF) | ((data & 0x07) << 8)
                self.channels[ch].block = (data >> 3) & 0x07

        elif 0xB0 <= addr <= 0xB2:
            ch = (addr & 0x03) + ch_offset
            if ch < self.NUM_CHANNELS:
                self.channels[ch].feedback = (data >> 3) & 0x07
                self.channels[ch].algorithm = data & 0x07

    def get_frequency(self, channel: int) -> float:
        """Get frequency of a channel."""
        if 0 <= channel < self.NUM_CHANNELS:
            return self.channels[channel].get_frequency(self.CLOCK)
        return 0.0

    def get_volume(self, channel: int) -> float:
        """Get approximate volume of a channel."""
        if channel < 0 or channel >= self.NUM_CHANNELS:
            return 0.0

        ch = self.channels[channel]
        if not ch.key_on_state:
            return 0.0

        algo = Channel.ALGORITHMS[ch.algorithm]
        carriers = algo['carriers']

        total_vol = 0.0
        for op_idx in carriers:
            op = ch.ops[op_idx]
            tl = op.total_level
            db = tl * 0.75
            total_vol += 10.0 ** (-db / 20.0)

        return total_vol / len(carriers)

    def is_active(self, channel: int) -> bool:
        """Check if channel is producing sound."""
        if channel < 0 or channel >= self.NUM_CHANNELS:
            return False
        if channel == 5 and self.dac_enabled:
            return True
        ch = self.channels[channel]
        return ch.key_on_state and ch.fnum > 0

    @property
    def key_on(self):
        return [ch.key_on_state for ch in self.channels]

    def generate_samples(self, num_samples: int) -> Tuple[np.ndarray, ...]:
        """Generate samples for all channels."""
        outputs = []

        for ch_idx in range(self.NUM_CHANNELS):
            # DAC handling for channel 6
            if ch_idx == 5 and self.dac_enabled:
                indices = np.arange(num_samples)
                start = (self.dac_buffer_pos - num_samples) % len(self.dac_buffer)
                indices = (start + indices) % len(self.dac_buffer)
                samples = self.dac_buffer[indices.astype(int)]
                outputs.append(samples.astype(np.float32))
                continue

            ch = self.channels[ch_idx]
            samples = np.zeros(num_samples, dtype=np.float32)

            if ch.fnum > 0:
                for i in range(num_samples):
                    samples[i] = ch.generate_sample()

            samples = np.clip(samples, -1.0, 1.0)
            outputs.append(samples)

        return tuple(outputs)

    def reset(self):
        """Reset to initial state."""
        self.channels = [Channel() for _ in range(self.NUM_CHANNELS)]
        self.dac_enabled = False
        self.dac_data = 0x80
        self.dac_buffer = np.zeros(1024, dtype=np.float32)
        self.dac_buffer_pos = 0


# Test
if __name__ == "__main__":
    ym = YM2612()

    # Set up channel 0
    ym.write(0, 0xB0, 0x00)  # Algo 0, no feedback

    # Frequency
    ym.write(0, 0xA4, (4 << 3) | 0x04)  # Block 4
    ym.write(0, 0xA0, 0x45)

    # Configure operators
    for slot in range(4):
        base = 0x30 + slot * 4
        ym.write(0, base, 0x01)  # DT=0, MUL=1
        ym.write(0, base + 0x10, 20 + slot * 10)  # TL
        ym.write(0, base + 0x20, 0x1F)  # AR=31
        ym.write(0, base + 0x30, 0x05)  # D1R=5
        ym.write(0, base + 0x40, 0x00)  # D2R=0 (no decay in sustain)
        ym.write(0, base + 0x50, 0x1F)  # SL=1, RR=15

    # Key on
    ym.write(0, 0x28, 0xF0)

    print(f"Frequency: {ym.get_frequency(0):.1f} Hz")
    print(f"Active: {ym.is_active(0)}")

    # Generate samples
    for i in range(10):
        samples = ym.generate_samples(100)
        max_val = np.abs(samples[0]).max()
        eg_level = ym.channels[0].ops[3].eg_level
        eg_state = ym.channels[0].ops[3].eg_state
        states = ['ATT', 'DEC', 'SUS', 'REL', 'OFF']
        print(f"Frame {i}: max={max_val:.4f}, eg={eg_level}, state={states[eg_state]}")
