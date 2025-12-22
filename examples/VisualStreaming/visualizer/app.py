"""
Main application class for the Visual Streaming visualizer.
"""

import numpy as np
from typing import Optional, Callable
import threading
import queue

try:
    from imgui_bundle import imgui, implot, hello_imgui, ImVec4
except ImportError:
    print("ERROR: imgui-bundle not installed. Run: pip install imgui-bundle")
    import sys
    sys.exit(1)


class VisualizerApp:
    """Main visualizer application with oscilloscope-style waveform display."""

    # Channel configuration
    FM_CHANNELS = 6
    PSG_CHANNELS = 4  # 3 tone + 1 noise
    TOTAL_CHANNELS = FM_CHANNELS + PSG_CHANNELS

    # Waveform buffer size (samples per channel)
    # Furnace uses 65536 - we use 8192 for reasonable memory usage
    WAVEFORM_SAMPLES = 8192

    # Colors (RGBA) - 90s Neon aesthetic
    COLORS = {
        # FM channels - neon/synthwave colors
        'fm1': ImVec4(1.0, 0.1, 0.5, 1.0),   # Hot pink
        'fm2': ImVec4(1.0, 0.4, 0.0, 1.0),   # Neon orange
        'fm3': ImVec4(1.0, 1.0, 0.0, 1.0),   # Electric yellow
        'fm4': ImVec4(0.0, 1.0, 0.4, 1.0),   # Hacker green
        'fm5': ImVec4(0.0, 0.8, 1.0, 1.0),   # Electric cyan
        'fm6': ImVec4(1.0, 0.0, 0.3, 1.0),   # Neon red (DAC)
        # PSG channels - more neon
        'psg1': ImVec4(0.0, 1.0, 1.0, 1.0),  # Cyan
        'psg2': ImVec4(0.4, 0.4, 1.0, 1.0),  # Neon blue
        'psg3': ImVec4(0.8, 0.0, 1.0, 1.0),  # Neon purple/magenta
        'noise': ImVec4(0.0, 1.0, 0.0, 1.0), # Matrix green
        # UI colors - darker for contrast
        'background': ImVec4(0.05, 0.05, 0.08, 1.0),
        'grid': ImVec4(0.15, 0.15, 0.18, 1.0),
        'text': ImVec4(0.9, 0.9, 0.9, 1.0),
        'text_dim': ImVec4(0.4, 0.4, 0.5, 1.0),
    }

    def __init__(self):
        # Waveform data for each channel
        self.waveforms = [np.zeros(self.WAVEFORM_SAMPLES, dtype=np.float32)
                          for _ in range(self.TOTAL_CHANNELS)]

        # Channel labels
        self.channel_labels = [
            "FM 1", "FM 2", "FM 3", "FM 4", "FM 5", "FM 6 (DAC)",
            "PSG 1", "PSG 2", "PSG 3", "Noise"
        ]

        # Channel colors (as list for indexing)
        self.channel_colors = [
            self.COLORS['fm1'], self.COLORS['fm2'], self.COLORS['fm3'],
            self.COLORS['fm4'], self.COLORS['fm5'], self.COLORS['fm6'],
            self.COLORS['psg1'], self.COLORS['psg2'], self.COLORS['psg3'],
            self.COLORS['noise'],
        ]

        # Key-on state for each channel (for visual indicators)
        self.key_on = [False] * self.TOTAL_CHANNELS

        # Playback state
        self.is_playing = False
        self.is_paused = False
        self.current_file = ""
        self.progress = 0.0  # 0-100
        self.elapsed_time = 0.0
        self.total_duration = 0.0

        # Status message
        self.status_message = "Ready"

        # Thread-safe command queue for chip writes
        self.command_queue: queue.Queue = queue.Queue()

        # Callbacks
        self.on_stop: Optional[Callable] = None
        self.on_pause: Optional[Callable] = None

        # Lock for waveform data
        self._lock = threading.Lock()

        # Display mode: True = triggered (stationary), False = scrolling
        self.triggered_display = True

        # Target number of waveform cycles to display (auto-scales width to frequency)
        self.target_cycles = 3

        # Fallback display samples when frequency can't be detected
        self.default_display_samples = 256

        # Min/max samples to display (prevents too stretched or too compressed)
        self.min_display_samples = 64
        self.max_display_samples = 512

        # Track how much valid data is in each buffer (starts at 0, grows to WAVEFORM_SAMPLES)
        self.valid_samples = [0] * self.TOTAL_CHANNELS

        # Fixed display window size in samples
        # ~5.8ms at 44100Hz = 256 samples
        self.display_samples = 256

        # --- Trigger state ---

        # Trigger offset from END of buffer (stable reference frame)
        self.trigger_offset = [self.display_samples + 50] * self.TOTAL_CHANNELS

        # Samples added since last frame (for continuity tracking)
        self.samples_since_last_frame = [0] * self.TOTAL_CHANNELS

        # Smoothed period estimate (in samples)
        self.smoothed_period = [20.0] * self.TOTAL_CHANNELS

        # Period smoothing factor (higher = more stable)
        self.period_smoothing = 0.95

        # Amplitude scaling per channel (for auto-gain)
        self.amplitude_scale = [1.0] * self.TOTAL_CHANNELS

        # Target amplitude for display (normalized) - keep some headroom
        self.target_amplitude = 0.6

        # Amplitude smoothing factor
        self.amplitude_smoothing = 0.95

        # Max amplitude scale (prevent extreme boosting that causes clipping look)
        self.max_amplitude_scale = 4.0

        # DAC mode state (FM channel 6 becomes PCM output when enabled)
        self.dac_enabled = False

        # Larger display window for scrolling channels (DAC, Noise)
        self.scroll_display_samples = 512

        # Smoothed glow intensity per channel (for fade in/out)
        self.glow_intensity = [0.0] * self.TOTAL_CHANNELS
        self.glow_fade_speed = 0.15  # How fast glow fades in/out (0-1, higher = faster)

        # Keyboard indicator glow intensity per channel (for smooth on/off transitions)
        self.indicator_glow = [0.0] * self.TOTAL_CHANNELS

        # Pitch tracking for keyboard display (continuous pitch value, 0 = no note)
        # Stored as fractional MIDI note (e.g., 60.5 = between C4 and C#4)
        # FM channels 0-5, PSG channels 6-8 (noise channel 9 doesn't have pitch)
        self.channel_pitch = [0.0] * self.TOTAL_CHANNELS

        # Keyboard range (MIDI notes) - full range for thin keys
        self.keyboard_low_note = 21   # A0 (piano low)
        self.keyboard_high_note = 108 # C8 (piano high)

    def set_dac_mode(self, enabled: bool):
        """Set DAC mode state (called when DAC enable changes)."""
        self.dac_enabled = enabled

    def set_channel_pitch(self, channel: int, pitch: float):
        """Set the current pitch for a channel (fractional MIDI note for keyboard display)."""
        if 0 <= channel < self.TOTAL_CHANNELS:
            self.channel_pitch[channel] = pitch

    def update_waveform(self, channel: int, data: np.ndarray):
        """Update waveform data for a channel (thread-safe)."""
        if 0 <= channel < self.TOTAL_CHANNELS:
            with self._lock:
                # Roll existing data and append new
                samples = min(len(data), self.WAVEFORM_SAMPLES)
                self.waveforms[channel] = np.roll(self.waveforms[channel], -samples)
                self.waveforms[channel][-samples:] = data[-samples:]

                # Track valid data (caps at buffer size)
                self.valid_samples[channel] = min(
                    self.valid_samples[channel] + samples,
                    self.WAVEFORM_SAMPLES
                )

                # Accumulate samples for frame-to-frame continuity
                self.samples_since_last_frame[channel] += samples

    def _estimate_period(self, channel_idx: int, data: np.ndarray) -> float:
        """
        Estimate waveform period using zero-crossing analysis.
        Returns smoothed period estimate, or 0 if can't detect.

        Simpler than FFT autocorrelation but works well for FM/PSG.
        """
        n = len(data)
        if n < 64:
            return self.smoothed_period[channel_idx]

        # Use recent samples for period detection
        chunk = data[-1024:] if n >= 1024 else data

        # Check if loud enough
        max_val = np.abs(chunk).max()
        if max_val < 0.01:
            return self.smoothed_period[channel_idx]

        # Find zero crossings (rising edges: negative to positive)
        crossings = []
        for i in range(1, len(chunk)):
            if chunk[i-1] <= 0 and chunk[i] > 0:
                crossings.append(i)

        if len(crossings) < 2:
            return self.smoothed_period[channel_idx]

        # Calculate average period from zero-crossing spacing
        periods = []
        for i in range(1, len(crossings)):
            p = crossings[i] - crossings[i-1]
            if 4 <= p <= 500:  # Reasonable period range
                periods.append(p)

        if not periods:
            return self.smoothed_period[channel_idx]

        # Use median for robustness against outliers
        raw_period = float(np.median(periods))

        # Octave stabilization: if new period is ~half or ~double, snap to harmonic
        prev = self.smoothed_period[channel_idx]
        if prev > 0:
            ratio = raw_period / prev
            if 0.4 < ratio < 0.6:
                raw_period *= 2.0  # Was detecting octave up, correct it
            elif 1.7 < ratio < 2.3:
                raw_period *= 0.5  # Was detecting octave down, correct it

            # Heavy smoothing for stability
            smoothed = prev * self.period_smoothing + raw_period * (1 - self.period_smoothing)
        else:
            smoothed = raw_period

        self.smoothed_period[channel_idx] = smoothed
        return smoothed

    def _find_trigger(self, channel_idx: int, data: np.ndarray, display_samples: int, samples_advanced: int) -> int:
        """
        Frame-continuous trigger: track position and find nearest zero crossing.

        The display smoothly advances with the audio, but snaps to zero crossings
        for stability. When jumping, picks a crossing with similar waveform shape.
        """
        n = len(data)
        compare_len = 64  # Samples to compare for shape matching

        # Get last trigger offset (from end of buffer)
        last_offset = self.trigger_offset[channel_idx]

        # The buffer rolled by samples_advanced, so our trigger moved back
        expected_offset = last_offset + samples_advanced

        max_offset = display_samples * 4
        min_offset = display_samples

        # Check if we need to jump
        needs_jump = expected_offset > max_offset

        if needs_jump:
            # Capture template of current waveform shape
            current_idx = n - int(min(expected_offset, n - compare_len - 10))
            current_idx = max(0, min(current_idx, n - compare_len))
            template = data[current_idx:current_idx + compare_len]
            template_norm = np.linalg.norm(template)

            # Search for best matching zero crossing
            best_idx = n - display_samples - 50  # fallback
            best_score = -1

            search_start = n - max_offset
            search_end = n - min_offset

            for i in range(max(1, search_start), min(n - display_samples - compare_len, search_end)):
                if data[i-1] <= 0 < data[i]:
                    # Compare waveform shape after this crossing
                    candidate = data[i:i + compare_len]
                    candidate_norm = np.linalg.norm(candidate)

                    if template_norm > 0.01 and candidate_norm > 0.01:
                        # Normalized correlation
                        score = np.dot(template, candidate) / (template_norm * candidate_norm)
                    else:
                        score = 0

                    if score > best_score:
                        best_score = score
                        best_idx = i

            # Only use the match if it's reasonably good
            if best_score > 0.5:
                new_offset = n - best_idx
            else:
                # No good match - just find any rising crossing
                for i in range(search_end, search_start, -1):
                    if i > 0 and data[i-1] <= 0 < data[i]:
                        new_offset = n - i
                        break
                else:
                    new_offset = display_samples + 50
        else:
            # Normal case: find nearest zero crossing to expected position
            expected_offset = max(min_offset, min(expected_offset, max_offset))
            expected_idx = n - int(expected_offset)

            search_radius = 50
            best_idx = expected_idx
            best_dist = float('inf')

            for i in range(max(1, expected_idx - search_radius), min(n - display_samples, expected_idx + search_radius)):
                if data[i-1] <= 0 < data[i]:
                    dist = abs(i - expected_idx)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = i

            new_offset = n - best_idx

        # Clamp and store
        new_offset = max(min_offset, min(new_offset, max_offset))
        self.trigger_offset[channel_idx] = new_offset

        trigger_idx = n - int(new_offset)
        return max(0, min(trigger_idx, n - display_samples))

    def set_key_on(self, channel: int, on: bool):
        """Set key-on state for a channel."""
        if 0 <= channel < self.TOTAL_CHANNELS:
            self.key_on[channel] = on

    def set_status(self, message: str):
        """Set status message."""
        self.status_message = message

    def set_playback_info(self, filename: str, duration: float):
        """Set current file info."""
        self.current_file = filename
        self.total_duration = duration

    def set_progress(self, progress: float, elapsed: float):
        """Set playback progress (0-100) and elapsed time."""
        self.progress = progress
        self.elapsed_time = elapsed

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}:{secs:02d}"

    def _draw_channel_plot(self, label: str, channel_idx: int, width: float, height: float):
        """Draw a single channel's oscilloscope plot with frequency-scaled width."""
        color = self.channel_colors[channel_idx]
        is_active = self.key_on[channel_idx]

        # Get waveform data with lock
        with self._lock:
            full_data = self.waveforms[channel_idx].copy()
            valid_count = self.valid_samples[channel_idx]
            samples_advanced = self.samples_since_last_frame[channel_idx]
            self.samples_since_last_frame[channel_idx] = 0  # Reset for next frame

        # Noise channel (9) always scrolls
        # DAC mode (channel 5) scrolls only when DAC is enabled
        is_noise = (channel_idx == 9)
        is_dac_active = (channel_idx == 5 and self.dac_enabled)

        # Scrolling channels get a larger display window
        use_scrolling = is_noise or is_dac_active
        display_samples = self.scroll_display_samples if use_scrolling else self.display_samples

        # Use triggered display for tonal FM/PSG channels, scrolling for noise/DAC
        if self.triggered_display and not use_scrolling:
            if valid_count >= display_samples * 2:
                # Frame-continuous trigger with zero-crossing lock
                trigger_idx = int(self._find_trigger(channel_idx, full_data, display_samples, samples_advanced))
                end_idx = int(trigger_idx + display_samples)
                y_data = full_data[trigger_idx:end_idx].copy()
            elif valid_count >= display_samples:
                y_data = full_data[-display_samples:].copy()
            else:
                y_data = np.zeros(display_samples, dtype=np.float32)
        else:
            # Simple scrolling mode for noise/DAC or when triggered display is off
            if valid_count >= display_samples:
                y_data = full_data[-display_samples:].copy()
            elif valid_count > 0:
                y_data = np.zeros(display_samples, dtype=np.float32)
                y_data[-valid_count:] = full_data[-valid_count:]
            else:
                y_data = np.zeros(display_samples, dtype=np.float32)

        # Auto-scale amplitude (like Furnace's amplitude control)
        max_amp = np.abs(y_data).max()
        if max_amp > 0.001:
            # Calculate desired scale to reach target amplitude
            desired_scale = self.target_amplitude / max_amp

            # Smooth the scale factor
            current_scale = self.amplitude_scale[channel_idx]
            new_scale = current_scale * self.amplitude_smoothing + desired_scale * (1 - self.amplitude_smoothing)

            # Clamp scale to reasonable range
            new_scale = max(1.0, min(self.max_amplitude_scale, new_scale))
            self.amplitude_scale[channel_idx] = new_scale

            # Apply scaling (no hard clipping - let it show naturally)
            y_data = y_data * new_scale

        # Create x-axis normalized to 0-1 range for consistent display
        x_data = np.linspace(0, 1, len(y_data), dtype=np.float32)

        # Plot flags
        plot_flags = implot.Flags_.no_legend | implot.Flags_.no_mouse_text
        axis_flags = (implot.AxisFlags_.no_tick_labels |
                      implot.AxisFlags_.no_tick_marks |
                      implot.AxisFlags_.no_grid_lines)

        # Push style for this plot
        implot.push_style_var(implot.StyleVar_.plot_padding, imgui.ImVec2(8, 8))

        if implot.begin_plot(f"##{label}", imgui.ImVec2(width, height), plot_flags):
            # Set up axes
            implot.setup_axes("", "", axis_flags, axis_flags)
            implot.setup_axis_limits(implot.ImAxis_.x1, 0, 1, implot.Cond_.always)
            implot.setup_axis_limits(implot.ImAxis_.y1, -1.1, 1.1, implot.Cond_.always)

            # Get plot area for ambient glow
            plot_pos = implot.get_plot_pos()
            plot_size = implot.get_plot_size()
            draw_list = implot.get_plot_draw_list()

            # Draw gradient glow based on amplitude with smooth transitions
            max_amp = np.abs(y_data).max()

            # Calculate target glow intensity
            target_intensity = 0.0
            if is_active and max_amp > 0.02:
                target_intensity = min(1.0, max_amp * 1.5)

            # Smoothly transition glow intensity
            current_intensity = self.glow_intensity[channel_idx]
            if target_intensity > current_intensity:
                # Fade in (faster)
                new_intensity = current_intensity + (target_intensity - current_intensity) * self.glow_fade_speed * 2
            else:
                # Fade out (slower for nice decay)
                new_intensity = current_intensity + (target_intensity - current_intensity) * self.glow_fade_speed
            self.glow_intensity[channel_idx] = new_intensity

            # Draw gradient if there's any glow
            if new_intensity > 0.01:
                center_alpha = 0.18 * new_intensity
                edge_alpha = 0.0

                # Top half: gradient from top (transparent) to center (colored)
                top_color = imgui.get_color_u32(ImVec4(color.x, color.y, color.z, edge_alpha))
                center_color_u32 = imgui.get_color_u32(ImVec4(color.x, color.y, color.z, center_alpha))
                mid_y = plot_pos.y + plot_size.y / 2

                draw_list.add_rect_filled_multi_color(
                    imgui.ImVec2(plot_pos.x, plot_pos.y),
                    imgui.ImVec2(plot_pos.x + plot_size.x, mid_y),
                    top_color, top_color,  # top-left, top-right
                    center_color_u32, center_color_u32  # bottom-right, bottom-left
                )

                # Bottom half: gradient from center (colored) to bottom (transparent)
                draw_list.add_rect_filled_multi_color(
                    imgui.ImVec2(plot_pos.x, mid_y),
                    imgui.ImVec2(plot_pos.x + plot_size.x, plot_pos.y + plot_size.y),
                    center_color_u32, center_color_u32,  # top-left, top-right
                    top_color, top_color  # bottom-right, bottom-left
                )

            # Pure white center line
            center_color = ImVec4(1.0, 1.0, 1.0, 0.3)
            implot.push_style_color(implot.Col_.line, center_color)
            implot.push_style_var(implot.StyleVar_.line_weight, 1.0)
            implot.plot_line(f"{label}_center", np.array([0.0, 1.0], dtype=np.float32),
                            np.array([0.0, 0.0], dtype=np.float32))
            implot.pop_style_var()
            implot.pop_style_color()

            # Draw main waveform - clean thin line
            implot.push_style_color(implot.Col_.line, color)
            line_weight = 1.5 if is_active else 1.0
            implot.push_style_var(implot.StyleVar_.line_weight, line_weight)

            implot.plot_line(label, x_data, y_data)

            implot.pop_style_var()
            implot.pop_style_color()
            implot.end_plot()

        implot.pop_style_var()

        # Draw label overlay with subtle background
        draw_list = imgui.get_window_draw_list()
        pos = imgui.get_item_rect_min()

        # Build display label with mode indicator
        display_label = label
        if is_dac_active:
            display_label = f"{label} [DAC]"
        elif is_noise:
            display_label = f"{label} [~]"  # Noise indicator

        # Label with background for readability
        label_color = imgui.get_color_u32(color) if is_active else imgui.get_color_u32(self.COLORS['text_dim'])
        bg_color = imgui.get_color_u32(ImVec4(0.0, 0.0, 0.0, 0.5))
        text_size = imgui.calc_text_size(display_label)
        draw_list.add_rect_filled(
            imgui.ImVec2(pos.x + 4, pos.y + 2),
            imgui.ImVec2(pos.x + text_size.x + 12, pos.y + text_size.y + 6),
            bg_color, 3.0
        )
        draw_list.add_text(imgui.ImVec2(pos.x + 8, pos.y + 4), label_color, display_label)

    def _draw_keyboard(self, x: float, y: float, width: float, height: float):
        """Draw a vertical piano keyboard with floating pitch indicators."""
        draw_list = imgui.get_window_draw_list()

        # White keys per octave: C, D, E, F, G, A, B (indices 0-6)
        # Map MIDI note to which white key it is or sits between
        # Note in octave -> white key index (0-6), or boundary position for black keys
        white_key_indices = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}  # C,D,E,F,G,A,B

        # Black keys sit at boundaries: C#=between 0&1, D#=between 1&2, F#=between 3&4, G#=between 4&5, A#=between 5&6
        black_key_boundary = {1: 1, 3: 2, 6: 4, 8: 5, 10: 6}  # note_in_octave -> boundary position

        # Count white keys in range
        num_white_keys = 0
        for n in range(self.keyboard_low_note, self.keyboard_high_note + 1):
            if (n % 12) in white_key_indices:
                num_white_keys += 1

        pixels_per_white_key = height / num_white_keys

        # Colors
        white_key_color = imgui.get_color_u32(ImVec4(0.92, 0.92, 0.94, 1.0))
        black_key_color = imgui.get_color_u32(ImVec4(0.1, 0.1, 0.12, 1.0))
        key_border = imgui.get_color_u32(ImVec4(0.5, 0.5, 0.55, 0.6))

        # Black keys are 55% the length of white keys
        black_key_length = width * 0.55

        # Build a mapping from MIDI note to Y position (center of key or boundary)
        def midi_to_y(midi_note_float):
            """Convert MIDI note (can be fractional) to Y position."""
            midi_note = int(midi_note_float)
            frac = midi_note_float - midi_note

            # Count white keys from low note to this note
            white_key_count = 0
            for n in range(self.keyboard_low_note, min(midi_note, self.keyboard_high_note) + 1):
                if (n % 12) in white_key_indices:
                    white_key_count += 1

            note_in_octave = midi_note % 12

            if note_in_octave in white_key_indices:
                # White key - position at center of this white key
                # white_key_count is 1-based (includes this key), so center is at (count - 0.5)
                pos = white_key_count - 0.5
            else:
                # Black key - position at boundary between white keys
                # The boundary is at the white_key_count (after the previous white key)
                pos = white_key_count

            # Handle fractional notes (pitch bends)
            if frac > 0 and midi_note < self.keyboard_high_note:
                next_y = midi_to_y(midi_note + 1)
                curr_y = y + (num_white_keys - pos) * pixels_per_white_key
                return curr_y + frac * (next_y - curr_y)

            # Y from top (high notes at top, low at bottom)
            return y + (num_white_keys - pos) * pixels_per_white_key

        # Draw white key background
        draw_list.add_rect_filled(
            imgui.ImVec2(x, y),
            imgui.ImVec2(x + width, y + height),
            white_key_color
        )

        # Draw borders between white keys
        white_key_count = 0
        for midi_note in range(self.keyboard_low_note, self.keyboard_high_note + 1):
            note_in_octave = midi_note % 12
            if note_in_octave in white_key_indices:
                white_key_count += 1
                # Draw border at bottom of this white key (except for the last one)
                if midi_note < self.keyboard_high_note:
                    border_y = y + (num_white_keys - white_key_count) * pixels_per_white_key
                    draw_list.add_line(
                        imgui.ImVec2(x, border_y),
                        imgui.ImVec2(x + width, border_y),
                        key_border, 1.0
                    )

        # Draw black keys on top
        for midi_note in range(self.keyboard_low_note, self.keyboard_high_note + 1):
            note_in_octave = midi_note % 12
            if note_in_octave in black_key_boundary:
                key_y = midi_to_y(midi_note)
                black_height = pixels_per_white_key * 0.65

                draw_list.add_rect_filled(
                    imgui.ImVec2(x, key_y - black_height / 2),
                    imgui.ImVec2(x + black_key_length, key_y + black_height / 2),
                    black_key_color
                )

        # Draw floating pitch indicators for active channels with glow transitions
        for ch in range(self.TOTAL_CHANNELS - 1):  # Exclude noise
            pitch = self.channel_pitch[ch]
            is_active = pitch > 0 and self.key_on[ch] and self.keyboard_low_note <= pitch <= self.keyboard_high_note

            # Smooth glow transition (glow only, not the indicator itself)
            target_glow = 1.0 if is_active else 0.0
            current_glow = self.indicator_glow[ch]
            if target_glow > current_glow:
                new_glow = current_glow + (target_glow - current_glow) * 0.3
            else:
                new_glow = current_glow + (target_glow - current_glow) * 0.08
            self.indicator_glow[ch] = new_glow

            # Draw glow even when fading out (as long as there's some glow left)
            if new_glow > 0.01 and pitch > 0 and self.keyboard_low_note <= pitch <= self.keyboard_high_note:
                indicator_y = midi_to_y(pitch)
                color = self.channel_colors[ch]

                # Outer glow (larger, more visible)
                outer_glow_alpha = 0.35 * new_glow
                outer_glow_color = imgui.get_color_u32(ImVec4(color.x, color.y, color.z, outer_glow_alpha))
                draw_list.add_rect_filled(
                    imgui.ImVec2(x, indicator_y - 10),
                    imgui.ImVec2(x + width, indicator_y + 10),
                    outer_glow_color
                )

                # Inner glow trail (brighter)
                inner_glow_alpha = 0.6 * new_glow
                glow_color = imgui.get_color_u32(ImVec4(color.x, color.y, color.z, inner_glow_alpha))
                draw_list.add_rect_filled(
                    imgui.ImVec2(x, indicator_y - 4),
                    imgui.ImVec2(x + width, indicator_y + 4),
                    glow_color
                )

            # Draw indicator line and circle only when active (instant on/off)
            if is_active:
                indicator_y = midi_to_y(pitch)
                color = self.channel_colors[ch]

                # Draw main indicator line (full opacity)
                line_color = imgui.get_color_u32(ImVec4(color.x, color.y, color.z, 1.0))
                draw_list.add_line(
                    imgui.ImVec2(x, indicator_y),
                    imgui.ImVec2(x + width, indicator_y),
                    line_color, 2.0
                )

                # Draw bigger circle at the indicator
                draw_list.add_circle_filled(
                    imgui.ImVec2(x + width - 6, indicator_y),
                    6.0, line_color
                )

    def gui(self):
        """Main GUI rendering function - called every frame."""
        # Get window size
        viewport = imgui.get_main_viewport()
        window_size = viewport.size

        # Calculate layout
        padding = 10
        status_height = 60
        keyboard_width = 50  # Width of piano keyboard on left

        available_height = window_size.y - status_height - padding * 2
        available_width = window_size.x - padding * 2 - keyboard_width - padding

        # FM channels: 2 columns x 3 rows
        # PSG channels: 4 columns x 1 row
        fm_rows = 3
        fm_cols = 2
        psg_cols = 4

        fm_height_per_channel = (available_height * 0.75) / fm_rows
        psg_height = available_height * 0.25

        fm_width = (available_width - padding) / fm_cols
        psg_width = (available_width - padding * 3) / psg_cols

        # Main window
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(window_size)

        window_flags = (imgui.WindowFlags_.no_title_bar |
                       imgui.WindowFlags_.no_resize |
                       imgui.WindowFlags_.no_move |
                       imgui.WindowFlags_.no_collapse)

        imgui.begin("Visualizer", None, window_flags)

        # Status bar at top
        self._draw_status_bar(available_width + keyboard_width + padding)

        imgui.dummy(imgui.ImVec2(0, padding))

        # Draw keyboard on left side
        keyboard_y = imgui.get_cursor_screen_pos().y
        self._draw_keyboard(
            padding,
            keyboard_y,
            keyboard_width,
            available_height - padding
        )

        # Offset content to the right of keyboard
        imgui.set_cursor_pos(imgui.ImVec2(keyboard_width + padding * 2, imgui.get_cursor_pos().y))

        # FM Channels (2x3 grid)
        for row in range(fm_rows):
            imgui.set_cursor_pos_x(keyboard_width + padding * 2)
            for col in range(fm_cols):
                channel_idx = row * fm_cols + col
                if channel_idx < self.FM_CHANNELS:
                    if col > 0:
                        imgui.same_line()
                    self._draw_channel_plot(
                        self.channel_labels[channel_idx],
                        channel_idx,
                        fm_width - padding,
                        fm_height_per_channel - padding
                    )

        imgui.dummy(imgui.ImVec2(0, padding))

        # PSG Channels (1x4 row)
        imgui.set_cursor_pos_x(keyboard_width + padding * 2)
        for i in range(psg_cols):
            channel_idx = self.FM_CHANNELS + i
            if i > 0:
                imgui.same_line()
            self._draw_channel_plot(
                self.channel_labels[channel_idx],
                channel_idx,
                psg_width,
                psg_height - padding
            )

        imgui.end()

    def _draw_status_bar(self, width: float):
        """Draw the status bar with playback info."""
        # File info
        if self.current_file:
            imgui.text(f"Playing: {self.current_file}")
        else:
            imgui.text_colored(self.COLORS['text_dim'], "No file loaded")

        # Progress bar
        if self.total_duration > 0:
            imgui.same_line(width - 200)
            elapsed_str = self._format_time(self.elapsed_time)
            total_str = self._format_time(self.total_duration)
            imgui.text(f"{elapsed_str} / {total_str}")

        # Status message
        imgui.text_colored(self.COLORS['text_dim'], self.status_message)

    def run(self, title: str = "Genesis Engine Visualizer", width: int = 1280, height: int = 720):
        """Run the visualizer application."""
        # Create ImPlot context (required before using ImPlot)
        implot.create_context()

        # Configure hello_imgui
        params = hello_imgui.RunnerParams()
        params.app_window_params.window_title = title
        params.app_window_params.window_geometry.size = (width, height)

        # Center window on screen and make sure it's visible
        params.app_window_params.window_geometry.position_mode = (
            hello_imgui.WindowPositionMode.monitor_center
        )

        params.imgui_window_params.default_imgui_window_type = (
            hello_imgui.DefaultImGuiWindowType.no_default_window
        )
        params.callbacks.show_gui = self.gui

        # Set dark theme
        params.imgui_window_params.tweaked_theme = hello_imgui.ImGuiTweakedTheme()
        params.imgui_window_params.tweaked_theme.theme = hello_imgui.ImGuiTheme_.darcula_darker

        # Enable FPS limiting - this is critical for smooth rendering
        params.fps_idling.enable_idling = False  # Don't reduce FPS when idle
        params.fps_idling.fps_idle = 60.0        # Even when idle, run at 60fps

        # Run
        hello_imgui.run(params)

        # Cleanup
        implot.destroy_context()


def test_visualizer():
    """Test the visualizer with dummy waveforms."""
    import time
    import math

    app = VisualizerApp()
    app.set_playback_info("test_song.vgm", 180.0)
    app.set_status("Testing...")

    # Generate test waveforms in a thread
    def generate_test_data():
        sample_rate = 44100
        samples_per_update = 128
        # Phase accumulators for each channel (0.0 to 1.0 per cycle)
        phases = [0.0] * app.TOTAL_CHANNELS
        # Frequencies for each channel
        freqs = [220 * (1 + ch * 0.3) for ch in range(app.TOTAL_CHANNELS)]

        elapsed = 0.0
        while True:
            for ch in range(app.TOTAL_CHANNELS):
                freq = freqs[ch]
                phase_inc = freq / sample_rate
                wave = np.zeros(samples_per_update, dtype=np.float32)

                phase = phases[ch]
                for i in range(samples_per_update):
                    if ch >= app.FM_CHANNELS:
                        # PSG - square wave
                        wave[i] = 0.8 if phase < 0.5 else -0.8
                    else:
                        # FM - sine with modulation
                        wave[i] = np.sin(2 * np.pi * phase + 0.5 * np.sin(4 * np.pi * phase)) * 0.8

                    phase += phase_inc
                    if phase >= 1.0:
                        phase -= 1.0

                phases[ch] = phase
                app.update_waveform(ch, wave)
                app.set_key_on(ch, True)  # All channels active for test

            elapsed += samples_per_update / sample_rate
            app.set_progress((elapsed * 10) % 100, elapsed)
            time.sleep(0.016)  # ~60fps

    # Start test data generator
    test_thread = threading.Thread(target=generate_test_data, daemon=True)
    test_thread.start()

    # Run app
    app.run()


if __name__ == "__main__":
    test_visualizer()
