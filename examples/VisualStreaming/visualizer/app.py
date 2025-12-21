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
    WAVEFORM_SAMPLES = 1024

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

        # X-axis data (shared)
        self.x_data = np.arange(self.WAVEFORM_SAMPLES, dtype=np.float32)

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

    def update_waveform(self, channel: int, data: np.ndarray):
        """Update waveform data for a channel (thread-safe)."""
        if 0 <= channel < self.TOTAL_CHANNELS:
            with self._lock:
                # Roll existing data and append new
                samples = min(len(data), self.WAVEFORM_SAMPLES)
                self.waveforms[channel] = np.roll(self.waveforms[channel], -samples)
                self.waveforms[channel][-samples:] = data[-samples:]

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
        """Draw a single channel's oscilloscope plot."""
        color = self.channel_colors[channel_idx]
        is_active = self.key_on[channel_idx]

        # Get waveform data with lock
        with self._lock:
            y_data = self.waveforms[channel_idx].copy()

        # Plot flags (Flags_ not PlotFlags_)
        plot_flags = implot.Flags_.no_legend | implot.Flags_.no_mouse_text
        axis_flags = (implot.AxisFlags_.no_tick_labels |
                      implot.AxisFlags_.no_tick_marks |
                      implot.AxisFlags_.no_grid_lines)

        # Push style for this plot
        implot.push_style_var(implot.StyleVar_.plot_padding, imgui.ImVec2(4, 4))

        if implot.begin_plot(f"##{label}", imgui.ImVec2(width, height), plot_flags):
            # Set up axes
            implot.setup_axes("", "", axis_flags, axis_flags)
            implot.setup_axis_limits(implot.ImAxis_.x1, 0, self.WAVEFORM_SAMPLES, implot.Cond_.always)
            implot.setup_axis_limits(implot.ImAxis_.y1, -1.2, 1.2, implot.Cond_.always)

            # Draw waveform
            implot.push_style_color(implot.Col_.line, color)

            # Use thicker line if key is on
            if is_active:
                implot.push_style_var(implot.StyleVar_.line_weight, 2.0)

            implot.plot_line(label, self.x_data, y_data)

            if is_active:
                implot.pop_style_var()

            implot.pop_style_color()
            implot.end_plot()

        implot.pop_style_var()

        # Draw label overlay
        draw_list = imgui.get_window_draw_list()
        pos = imgui.get_item_rect_min()

        # Label background
        label_color = imgui.get_color_u32(color) if is_active else imgui.get_color_u32(self.COLORS['text_dim'])
        draw_list.add_text(imgui.ImVec2(pos.x + 6, pos.y + 4), label_color, label)

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
        t = 0
        while True:
            for ch in range(app.TOTAL_CHANNELS):
                freq = 1 + ch * 0.5
                if ch >= app.FM_CHANNELS:
                    # PSG - square waves
                    wave = np.sign(np.sin(2 * np.pi * freq * (np.arange(64) / 64 + t)))
                else:
                    # FM - more complex waveforms
                    x = np.arange(64) / 64 + t
                    wave = np.sin(2 * np.pi * freq * x + np.sin(4 * np.pi * x))

                app.update_waveform(ch, wave.astype(np.float32) * 0.8)
                app.set_key_on(ch, np.random.random() > 0.3)

            app.set_progress(t * 10 % 100, t)
            t += 0.05
            time.sleep(0.016)  # ~60fps

    # Start test data generator
    test_thread = threading.Thread(target=generate_test_data, daemon=True)
    test_thread.start()

    # Run app
    app.run()


if __name__ == "__main__":
    test_visualizer()
