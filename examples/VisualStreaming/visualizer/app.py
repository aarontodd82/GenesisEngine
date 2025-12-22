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

        # FFT size for autocorrelation-based pitch detection (Furnace uses 4096)
        self.fft_size = 4096

        # Fixed display window size in samples (Furnace uses user-configurable ms)
        # ~5.8ms at 44100Hz = 256 samples - small enough to see clear waveforms
        self.display_samples = 256

        # Detected periods for each channel (in samples)
        self.detected_periods = [0] * self.TOTAL_CHANNELS

        # Detected phases for each channel (0.0 to 1.0)
        self.detected_phases = [0.0] * self.TOTAL_CHANNELS

        # Smoothed needle positions for each channel (Furnace-style phase-locked display)
        self.needle_pos = [0.0] * self.TOTAL_CHANNELS

        # Total samples received per channel (like Furnace's buf->needle)
        # This continuously advances and provides frame-to-frame continuity
        self.total_samples_received = [0] * self.TOTAL_CHANNELS

        # Previous periods for smoothing
        self.prev_periods = [0.0] * self.TOTAL_CHANNELS

        # Needle smoothing factor (higher = smoother but slower response)
        self.needle_smoothing = 0.92

        # Period smoothing factor
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
                # Track total samples received (like Furnace's buf->needle)
                # This provides frame-to-frame continuity for phase-locking
                self.total_samples_received[channel] += len(data)

    def _furnace_process_fft(self, channel_idx: int, data: np.ndarray, display_size: int):
        """
        Furnace's exact FFT processing for oscilloscope.
        Updates detected period, phase, and needle position for the channel.

        Key insight: The needle is based on total_samples_received which continuously
        advances like Furnace's buf->needle. This provides frame-to-frame continuity.
        """
        FFT_SIZE = self.fft_size  # 4096
        BUFFER_SIZE = self.WAVEFORM_SAMPLES  # 8192

        n = len(data)
        if n < FFT_SIZE:
            return

        # Check if loud enough
        max_val = np.abs(data[-FFT_SIZE:]).max()
        if max_val < 0.001:
            return

        # Get the current stream position (like Furnace's buf->needle)
        stream_pos = self.total_samples_received[channel_idx]

        # Prepare input buffer with Hamming window (Furnace: 0.55 - 0.45*cos)
        in_buf = np.zeros(FFT_SIZE, dtype=np.float64)
        for j in range(FFT_SIZE):
            sample = data[n - FFT_SIZE + j]
            in_buf[j] = float(sample)
            # Hamming window
            in_buf[j] *= 0.55 - 0.45 * np.cos(np.pi * j / (FFT_SIZE >> 1))

        # FFT
        fft_out = np.fft.rfft(in_buf)

        # Power spectrum (magnitude squared)
        power = np.abs(fft_out) ** 2

        # Inverse FFT for autocorrelation
        corr_buf = np.fft.irfft(power, FFT_SIZE)

        # Find size of period - scan BACKWARDS from FFT_SIZE/4 to find lowest
        wave_len_cand_l = float('inf')
        wave_len_bottom = 2
        for j in range(FFT_SIZE >> 2, 2, -1):
            if corr_buf[j] < wave_len_cand_l:
                wave_len_cand_l = corr_buf[j]
                wave_len_bottom = j

        # Find highest point scanning backwards from FFT_SIZE/2 to wave_len_bottom
        wave_len_cand_h = float('-inf')
        wave_len = FFT_SIZE - 1
        for j in range((FFT_SIZE >> 1) - 1, wave_len_bottom, -1):
            if corr_buf[j] > wave_len_cand_h:
                wave_len_cand_h = corr_buf[j]
                wave_len = j

        # Check if we got a valid period
        if wave_len >= FFT_SIZE - 32:
            return  # No valid period found

        # Scale waveLen by displaySize (Furnace: waveLen *= displaySize*2.0/FFT_SIZE)
        wave_len_scaled = wave_len * (display_size * 2.0 / FFT_SIZE)

        # Smooth the period
        prev_period = self.prev_periods[channel_idx]
        if prev_period > 0:
            wave_len_scaled = prev_period * 0.9 + wave_len_scaled * 0.1
        self.prev_periods[channel_idx] = wave_len_scaled
        self.detected_periods[channel_idx] = int(wave_len_scaled)

        # DFT of one period to get phase
        if wave_len_scaled >= 4:
            dft_real = 0.0
            dft_imag = 0.0

            # Sample one period from near the end of buffer
            wave_len_int = int(wave_len_scaled)
            start_pos = n - display_size - wave_len_int

            for k in range(wave_len_int):
                idx = start_pos + k
                if 0 <= idx < n:
                    sample = float(data[idx])
                else:
                    sample = 0.0
                angle = k * (-2.0 * np.pi) / wave_len_scaled
                dft_real += sample * np.cos(angle)
                dft_imag += sample * np.sin(angle)

            # Calculate phase (Furnace: 0.5 + atan2/(2*pi))
            phase = 0.5 + (np.arctan2(dft_imag, dft_real) / (2.0 * np.pi))
            self.detected_phases[channel_idx] = phase

            # Debug: print values for channel 0 every ~1 second
            if channel_idx == 0 and stream_pos % 44100 < 1000:
                import sys
                print(f"CH0: period={wave_len_scaled:.1f}, phase={phase:.3f}, offset={phase * wave_len_scaled:.1f}", flush=True)

            # Calculate needle in STREAM coordinates (like Furnace)
            # Start with current stream position, back up by display size
            needle = float(stream_pos) - display_size

            # Apply phase correction (Furnace: needle -= phase * waveLen)
            needle -= phase * wave_len_scaled

            # The needle position should be smoothed to prevent jumps
            # But we need to handle the fact that stream_pos keeps increasing
            current_needle = self.needle_pos[channel_idx]

            if current_needle == 0:
                # First time - initialize
                self.needle_pos[channel_idx] = needle
            else:
                # Calculate expected needle based on samples received since last update
                # The needle should advance at the same rate as stream_pos
                expected_advance = stream_pos - (current_needle + display_size + phase * wave_len_scaled)

                # Only apply phase correction, don't fight the stream advancement
                target_needle = float(stream_pos) - display_size - phase * wave_len_scaled

                # Smooth only the phase-corrected offset, not the base position
                phase_offset = target_needle - (stream_pos - display_size)
                current_offset = current_needle - (stream_pos - display_size - expected_advance)

                # Smooth the offset
                smoothed_offset = current_offset * self.needle_smoothing + phase_offset * (1 - self.needle_smoothing)

                self.needle_pos[channel_idx] = (stream_pos - display_size) + smoothed_offset

    def _get_display_position(self, channel_idx: int, data: np.ndarray,
                               valid_samples: int, display_samples: int) -> int:
        """
        Get display position using Furnace-style phase-locked display.
        """
        buffer_len = len(data)
        BUFFER_SIZE = self.WAVEFORM_SAMPLES

        if valid_samples < display_samples + self.fft_size:
            return -1

        # Run Furnace's FFT processing
        self._furnace_process_fft(channel_idx, data, display_samples)

        # Get the needle position (in stream coordinates)
        stream_needle = self.needle_pos[channel_idx]
        stream_pos = self.total_samples_received[channel_idx]

        if stream_needle <= 0:
            # Fallback - just show recent samples
            return buffer_len - display_samples

        # Convert stream position to buffer position
        # The buffer holds the last BUFFER_SIZE samples
        # stream_pos points to "just after the last sample in buffer"
        # stream_needle is where we want to start reading

        # How far back from current position?
        samples_back = stream_pos - stream_needle

        # Convert to buffer index (buffer end is at buffer_len)
        buffer_idx = buffer_len - int(samples_back)

        # Clamp to valid range
        valid_start = buffer_len - valid_samples
        buffer_idx = max(valid_start, min(buffer_idx, buffer_len - display_samples))

        return buffer_idx

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

        # Get waveform data and valid count with lock
        with self._lock:
            full_data = self.waveforms[channel_idx].copy()
            valid_count = self.valid_samples[channel_idx]

        # Noise channel (9) and DAC channel (5) use scrolling mode
        is_noise = (channel_idx == 9)
        is_dac = (channel_idx == 5)

        # Fixed display window (Furnace-style: user-configurable, period only for phase alignment)
        display_samples = self.display_samples

        # Use triggered display for tonal FM/PSG channels, scrolling for noise/DAC
        if self.triggered_display and not is_noise and not is_dac:
            # Phase-locked display mode (like Furnace)
            trigger_idx = self._get_display_position(channel_idx, full_data, valid_count, display_samples)
            if trigger_idx >= 0:
                y_data = full_data[trigger_idx:trigger_idx + display_samples].copy()
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
