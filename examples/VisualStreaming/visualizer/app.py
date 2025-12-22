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

    # Colors (RGBA)
    COLORS = {
        # FM channels - warm colors
        'fm1': ImVec4(1.0, 0.4, 0.2, 1.0),   # Orange-red
        'fm2': ImVec4(1.0, 0.6, 0.2, 1.0),   # Orange
        'fm3': ImVec4(1.0, 0.8, 0.2, 1.0),   # Yellow-orange
        'fm4': ImVec4(0.9, 0.9, 0.3, 1.0),   # Yellow
        'fm5': ImVec4(1.0, 0.5, 0.5, 1.0),   # Salmon
        'fm6': ImVec4(1.0, 0.3, 0.4, 1.0),   # Red-pink (DAC)
        # PSG channels - cool colors
        'psg1': ImVec4(0.3, 0.8, 1.0, 1.0),  # Cyan
        'psg2': ImVec4(0.4, 0.6, 1.0, 1.0),  # Blue
        'psg3': ImVec4(0.5, 0.4, 1.0, 1.0),  # Purple
        'noise': ImVec4(0.6, 0.6, 0.6, 1.0), # Gray
        # UI colors
        'background': ImVec4(0.1, 0.1, 0.12, 1.0),
        'grid': ImVec4(0.2, 0.2, 0.22, 1.0),
        'text': ImVec4(0.9, 0.9, 0.9, 1.0),
        'text_dim': ImVec4(0.5, 0.5, 0.5, 1.0),
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
        compare_len = 32  # Samples to compare for shape matching

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

        # Noise channel (9) and DAC channel (5) use scrolling mode
        is_noise = (channel_idx == 9)
        is_dac = (channel_idx == 5)

        # Fixed display window
        display_samples = self.display_samples

        # Use triggered display for tonal FM/PSG channels, scrolling for noise/DAC
        if self.triggered_display and not is_noise and not is_dac:
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
            # Simple scrolling mode for noise or when triggered display is off
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

            # Draw glow effect (thicker, semi-transparent line behind)
            if is_active and np.abs(y_data).max() > 0.05:
                glow_color = ImVec4(color.x, color.y, color.z, 0.3)
                implot.push_style_color(implot.Col_.line, glow_color)
                implot.push_style_var(implot.StyleVar_.line_weight, 4.0)
                implot.plot_line(f"{label}_glow", x_data, y_data)
                implot.pop_style_var()
                implot.pop_style_color()

            # Draw main waveform
            implot.push_style_color(implot.Col_.line, color)
            line_weight = 2.0 if is_active else 1.5
            implot.push_style_var(implot.StyleVar_.line_weight, line_weight)

            implot.plot_line(label, x_data, y_data)

            implot.pop_style_var()
            implot.pop_style_color()
            implot.end_plot()

        implot.pop_style_var()

        # Draw label overlay with subtle background
        draw_list = imgui.get_window_draw_list()
        pos = imgui.get_item_rect_min()

        # Label with background for readability
        label_color = imgui.get_color_u32(color) if is_active else imgui.get_color_u32(self.COLORS['text_dim'])
        bg_color = imgui.get_color_u32(ImVec4(0.0, 0.0, 0.0, 0.5))
        text_size = imgui.calc_text_size(label)
        draw_list.add_rect_filled(
            imgui.ImVec2(pos.x + 4, pos.y + 2),
            imgui.ImVec2(pos.x + text_size.x + 12, pos.y + text_size.y + 6),
            bg_color, 3.0
        )
        draw_list.add_text(imgui.ImVec2(pos.x + 8, pos.y + 4), label_color, label)

    def gui(self):
        """Main GUI rendering function - called every frame."""
        # Get window size
        viewport = imgui.get_main_viewport()
        window_size = viewport.size

        # Calculate layout
        padding = 10
        status_height = 60
        available_height = window_size.y - status_height - padding * 2
        available_width = window_size.x - padding * 2

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
        self._draw_status_bar(available_width)

        imgui.dummy(imgui.ImVec2(0, padding))

        # FM Channels (2x3 grid)
        for row in range(fm_rows):
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
