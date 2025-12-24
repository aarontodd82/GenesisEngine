"""
Pygame + OpenGL visualizer with CRT shader effects.
Drop-in replacement for app.py with enhanced visual capabilities.
"""

import os
import subprocess
import tempfile
import numpy as np
from typing import Optional, Callable
import threading
import queue

import pygame
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GL import shaders

# Optional cv2 for video recording
try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


# Pass-through shader (zoom effect removed)
ZOOM_FRAGMENT_SHADER = """
#version 130

varying vec2 TexCoord;

uniform sampler2D screenTexture;
uniform float pulseIntensity;

void main() {
    gl_FragColor = texture2D(screenTexture, TexCoord);
}
"""

# CRT Fragment Shader - applied AFTER zoom so scanlines/phosphors stay fixed
CRT_FRAGMENT_SHADER = """
#version 130

varying vec2 TexCoord;

uniform sampler2D screenTexture;
uniform float time;
uniform vec2 resolution;

// Original bloom - 11 texture samples per pixel
vec3 sampleBloom(sampler2D tex, vec2 uv, vec2 pixelSize) {
    vec3 bloom = vec3(0.0);
    float weights[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);

    bloom += texture2D(tex, uv).rgb * weights[0];

    for (int i = 1; i < 5; i++) {
        vec2 offset = vec2(float(i) * pixelSize.x * 2.0, 0.0);
        bloom += texture2D(tex, uv + offset).rgb * weights[i];
        bloom += texture2D(tex, uv - offset).rgb * weights[i];
    }

    return bloom;
}

void main() {
    vec2 uv = TexCoord;
    vec2 center = vec2(0.5, 0.5);

    // Barrel distortion (CRT curvature) - this is physical, stays here
    vec2 dc = uv - center;
    float dist = length(dc);
    float distortion = 1.0 + dist * dist * 0.08;
    uv = center + dc * distortion;

    // Check bounds
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    vec2 pixelSize = 1.0 / resolution;

    // Chromatic aberration
    float aberration = 0.001;
    float r = texture2D(screenTexture, uv + vec2(aberration, 0.0)).r;
    float g = texture2D(screenTexture, uv).g;
    float b = texture2D(screenTexture, uv - vec2(aberration, 0.0)).b;
    vec3 color = vec3(r, g, b);

    // TESTING: Original bloom restored
    vec3 bloom = sampleBloom(screenTexture, uv, pixelSize);
    float brightness = dot(color, vec3(0.299, 0.587, 0.114));
    color += bloom * brightness * 0.3;

    // Scanlines - 2.5 screen pixels per scanline, 25% contrast
    // Uses distorted uv so scanlines warp with barrel distortion (authentic CRT)
    float scanlinePos = uv.y * resolution.y / 2.5;
    float scanline = sin(scanlinePos * 3.14159);
    float scanlineMask = 0.75 + 0.25 * smoothstep(-0.5, 0.5, scanline);
    scanlineMask = mix(scanlineMask, 1.0, brightness * 0.4);  // Bright areas show less scanline
    color *= scanlineMask;

    // Phosphor/aperture grille - 6 pixel triads, 28% blend
    // Uses distorted uv so phosphors warp with barrel distortion (authentic CRT)
    float pixelX = uv.x * resolution.x / 2.0;
    int subpixel = int(mod(pixelX, 3.0));
    vec3 phosphorMask;
    if (subpixel == 0) {
        phosphorMask = vec3(1.0, 0.75, 0.75);
    } else if (subpixel == 1) {
        phosphorMask = vec3(0.75, 1.0, 0.75);
    } else {
        phosphorMask = vec3(0.75, 0.75, 1.0);
    }
    color *= mix(vec3(1.0), phosphorMask, 0.28);

    // Vignette
    float vignette = 1.0 - dist * 0.2;
    color *= vignette;

    // Brightness boost
    color *= 1.1;

    color = clamp(color, 0.0, 1.0);

    gl_FragColor = vec4(color, 1.0);
}
"""

CRT_VERTEX_SHADER = """
#version 130

varying vec2 TexCoord;

void main() {
    gl_Position = gl_Vertex;
    TexCoord = gl_MultiTexCoord0.xy;
}
"""


class VisualizerApp:
    """Pygame + OpenGL visualizer with CRT shader effects."""

    # Channel configuration
    FM_CHANNELS = 6
    PSG_CHANNELS = 4
    TOTAL_CHANNELS = FM_CHANNELS + PSG_CHANNELS
    WAVEFORM_SAMPLES = 8192

    # 90s Neon colors (RGBA floats) - brightened for CRT shader
    COLORS = {
        'fm1': (1.0, 0.2, 0.6, 1.0),   # Hot pink
        'fm2': (1.0, 0.5, 0.1, 1.0),   # Neon orange
        'fm3': (1.0, 1.0, 0.2, 1.0),   # Electric yellow
        'fm4': (0.2, 1.0, 0.5, 1.0),   # Hacker green
        'fm5': (0.2, 0.9, 1.0, 1.0),   # Electric cyan
        'fm6': (1.0, 0.2, 0.4, 1.0),   # Neon red (DAC)
        'psg1': (0.2, 1.0, 1.0, 1.0),  # Cyan
        'psg2': (0.5, 0.5, 1.0, 1.0),  # Neon blue
        'psg3': (0.9, 0.2, 1.0, 1.0),  # Neon purple
        'noise': (0.2, 1.0, 0.2, 1.0), # Matrix green
        'background': (0.08, 0.08, 0.12, 1.0),  # Slightly brighter
        'panel': (0.12, 0.12, 0.18, 1.0),  # Waveform panel background
        'white': (1.0, 1.0, 1.0, 1.0),
        'dim': (0.5, 0.5, 0.6, 1.0),
    }

    def __init__(self, crt_enabled: bool = True):
        # CRT shader toggle (on by default)
        self.crt_enabled = crt_enabled

        # Waveform data
        self.waveforms = [np.zeros(self.WAVEFORM_SAMPLES, dtype=np.float32)
                          for _ in range(self.TOTAL_CHANNELS)]

        self.channel_labels = [
            "FM 1", "FM 2", "FM 3", "FM 4", "FM 5", "FM 6",
            "PSG 1", "PSG 2", "PSG 3", "Noise"
        ]

        self.channel_colors = [
            self.COLORS['fm1'], self.COLORS['fm2'], self.COLORS['fm3'],
            self.COLORS['fm4'], self.COLORS['fm5'], self.COLORS['fm6'],
            self.COLORS['psg1'], self.COLORS['psg2'], self.COLORS['psg3'],
            self.COLORS['noise'],
        ]

        self.key_on = [False] * self.TOTAL_CHANNELS
        self.is_playing = False
        self.current_file = ""
        self.total_duration = 0.0
        self.elapsed_time = 0.0
        self.status_message = "Ready"
        self.current_fps = 0.0
        self.composer = ""
        self.game = ""

        self._lock = threading.Lock()
        self.valid_samples = [0] * self.TOTAL_CHANNELS
        self.default_display_samples = 256  # Normal window - shows frequency changes
        self.max_display_samples = 1024  # Cap for very low frequencies
        self.scroll_display_samples = 4096  # ~93ms at 44.1kHz - human-perceivable scroll rate

        # Per-channel adaptive display samples (only expands for low frequencies)
        self.channel_display_samples = [self.default_display_samples] * self.TOTAL_CHANNELS
        self.channel_period = [128.0] * self.TOTAL_CHANNELS  # Smoothed period estimate

        # Trigger state
        self.trigger_offset = [self.default_display_samples + 50] * self.TOTAL_CHANNELS
        self.samples_since_last_frame = [0] * self.TOTAL_CHANNELS
        self.smoothed_period = [20.0] * self.TOTAL_CHANNELS
        self.triggered_display = True  # Use zero-crossing trigger
        self.period_smoothing = 0.85

        # Amplitude scaling
        self.amplitude_scale = [1.0] * self.TOTAL_CHANNELS
        self.target_amplitude = 0.6
        self.amplitude_smoothing = 0.95
        self.max_amplitude_scale = 4.0

        # DAC mode
        self.dac_enabled = False

        # Glow intensity
        self.glow_intensity = [0.0] * self.TOTAL_CHANNELS
        self.glow_fade_speed = 0.15

        # Pitch tracking
        self.channel_pitch = [0.0] * self.TOTAL_CHANNELS
        self.indicator_glow = [0.0] * self.TOTAL_CHANNELS
        self.keyboard_low_note = 21
        self.keyboard_high_note = 108

        # Pulse effect - envelope follower with fast attack, slow decay
        self.pulse_intensity = 0.0
        self.pulse_attack = 0.4   # Fast attack - respond quickly to loud
        self.pulse_decay = 0.03   # Slow decay - smooth fade out

        # OpenGL objects (initialized in run())
        self.screen = None
        self.width = 1280
        self.height = 720
        self.framebuffer = None
        self.fb_texture = None
        self.crt_shader = None
        self.quad_vbo = None
        self.running = False

        # Callbacks
        self.on_stop: Optional[Callable] = None

    def set_dac_mode(self, enabled: bool):
        self.dac_enabled = enabled

    def set_channel_pitch(self, channel: int, pitch: float):
        if 0 <= channel < self.TOTAL_CHANNELS:
            self.channel_pitch[channel] = pitch

    def update_waveform(self, channel: int, data: np.ndarray):
        if 0 <= channel < self.TOTAL_CHANNELS:
            with self._lock:
                samples = min(len(data), self.WAVEFORM_SAMPLES)
                self.waveforms[channel] = np.roll(self.waveforms[channel], -samples)
                self.waveforms[channel][-samples:] = data[-samples:]
                self.valid_samples[channel] = min(
                    self.valid_samples[channel] + samples,
                    self.WAVEFORM_SAMPLES
                )
                self.samples_since_last_frame[channel] += samples

    def set_key_on(self, channel: int, on: bool):
        if 0 <= channel < self.TOTAL_CHANNELS:
            self.key_on[channel] = on

    def set_status(self, message: str):
        self.status_message = message

    def set_playback_info(self, filename: str, duration: float, title: str = '', composer: str = '', game: str = ''):
        # Use title if available, otherwise use filename
        self.current_file = title if title else filename
        self.total_duration = duration
        self.composer = composer
        self.game = game

    def set_progress(self, progress: float, elapsed: float):
        self.elapsed_time = elapsed

    def _get_display_samples_for_channel(self, channel_idx: int) -> int:
        """
        Get display samples for a channel based on its pitch.
        Uses default window, but expands for low frequencies where a full cycle won't fit.
        """
        midi_note = self.channel_pitch[channel_idx]
        if midi_note <= 0:
            return self.default_display_samples

        # Convert MIDI note to frequency: freq = 440 * 2^((note - 69) / 12)
        freq = 440.0 * (2.0 ** ((midi_note - 69.0) / 12.0))
        # Period in samples at 44100 Hz
        period = 44100.0 / freq

        # If period fits in default window, use default (preserves frequency visualization)
        if period <= self.default_display_samples * 0.9:
            return self.default_display_samples

        # For low frequencies, expand to show ~1.5 cycles
        expanded = int(period * 1.5)
        return min(expanded, self.max_display_samples)

    def _find_trigger(self, channel_idx: int, data: np.ndarray, display_samples: int, samples_advanced: int) -> int:
        """
        Frame-continuous trigger: track position and find nearest zero crossing.
        Copied from app.py for identical behavior.
        """
        n = len(data)
        compare_len = 64

        last_offset = self.trigger_offset[channel_idx]
        expected_offset = last_offset + samples_advanced

        max_offset = display_samples * 4
        min_offset = display_samples

        needs_jump = expected_offset > max_offset

        if needs_jump:
            current_idx = n - int(min(expected_offset, n - compare_len - 10))
            current_idx = max(0, min(current_idx, n - compare_len))
            template = data[current_idx:current_idx + compare_len]
            template_norm = np.linalg.norm(template)

            best_idx = n - display_samples - 50
            best_score = -1

            search_start = n - max_offset
            search_end = n - min_offset

            for i in range(max(1, search_start), min(n - display_samples - compare_len, search_end)):
                if data[i-1] <= 0 < data[i]:
                    candidate = data[i:i + compare_len]
                    candidate_norm = np.linalg.norm(candidate)

                    if template_norm > 0.01 and candidate_norm > 0.01:
                        score = np.dot(template, candidate) / (template_norm * candidate_norm)
                    else:
                        score = 0

                    if score > best_score:
                        best_score = score
                        best_idx = i

            if best_score > 0.5:
                new_offset = n - best_idx
            else:
                for i in range(search_end, search_start, -1):
                    if i > 0 and data[i-1] <= 0 < data[i]:
                        new_offset = n - i
                        break
                else:
                    new_offset = display_samples + 50
        else:
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

        new_offset = max(min_offset, min(new_offset, max_offset))
        self.trigger_offset[channel_idx] = new_offset

        trigger_idx = n - int(new_offset)
        return max(0, min(trigger_idx, n - display_samples))

    def _init_gl(self):
        """Initialize OpenGL resources."""
        # Initialize fonts - scale by DPI for HiDPI displays
        pygame.font.init()
        dpi = getattr(self, 'dpi_scale', 1.0)
        self.font = pygame.font.SysFont('consolas', int(14 * dpi))
        self.font_small = pygame.font.SysFont('consolas', int(12 * dpi))
        # Specific fonts for branding
        # FM-90s uses Neuropol, Genesis Engine uses NiseGenesis
        try:
            self.font_fm90s = pygame.font.SysFont('Neuropol', int(26 * dpi))
            print("Using Neuropol for FM-90s")
        except:
            self.font_fm90s = pygame.font.SysFont('Impact', int(26 * dpi), bold=True)
            print("Neuropol not found, using Impact")

        try:
            self.font_genesis = pygame.font.SysFont('NiseGenesis', int(24 * dpi))
            print("Using NiseGenesis for Genesis Engine")
        except:
            self.font_genesis = pygame.font.SysFont('Impact', int(24 * dpi), bold=True)
            print("NiseGenesis not found, using Impact")

        # Fallback brand font
        self.font_brand = pygame.font.SysFont('Impact', int(24 * dpi), bold=True)

        self.text_cache = {}  # Cache rendered text textures

        # Compile zoom shader (applied before CRT effects)
        try:
            zoom_vert = shaders.compileShader(CRT_VERTEX_SHADER, GL_VERTEX_SHADER)
            zoom_frag = shaders.compileShader(ZOOM_FRAGMENT_SHADER, GL_FRAGMENT_SHADER)
            self.zoom_shader = shaders.compileProgram(zoom_vert, zoom_frag)
            print("Zoom shader compiled successfully")
        except Exception as e:
            print(f"Zoom shader compile error: {e}")
            self.zoom_shader = None

        # Compile CRT shader (applied after zoom)
        try:
            crt_vert = shaders.compileShader(CRT_VERTEX_SHADER, GL_VERTEX_SHADER)
            crt_frag = shaders.compileShader(CRT_FRAGMENT_SHADER, GL_FRAGMENT_SHADER)
            self.crt_shader = shaders.compileProgram(crt_vert, crt_frag)
            print("CRT shader compiled successfully")
        except Exception as e:
            print(f"CRT shader compile error: {e}")
            self.crt_shader = None

        # Create first framebuffer for scene rendering
        self.framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        self.fb_texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.fb_texture)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, self.width, self.height, 0, GL_RGB, GL_UNSIGNED_BYTE, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.fb_texture, 0)
        if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
            print("Framebuffer 1 not complete!")

        # Create second framebuffer for zoom pass
        self.framebuffer2 = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer2)
        self.fb_texture2 = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.fb_texture2)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, self.width, self.height, 0, GL_RGB, GL_UNSIGNED_BYTE, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.fb_texture2, 0)
        if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
            print("Framebuffer 2 not complete!")

        glBindFramebuffer(GL_FRAMEBUFFER, 0)

        # Set up initial projection matrix
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, self.width, self.height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        # Enable blending
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # Enable line smoothing
        glEnable(GL_LINE_SMOOTH)
        glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)

        # Track fullscreen state and viewport for letterboxing
        self.fullscreen = False
        self.windowed_size = (self.width, self.height)
        self.screen_width = self.width
        self.screen_height = self.height
        # Viewport for letterboxing (x, y, w, h)
        self.viewport = (0, 0, self.width, self.height)

    def _resize_framebuffers(self, width, height):
        """Resize framebuffers when window size changes."""
        self.width = width
        self.height = height

        # Clear text cache since positions may change
        for tex_id, _, _ in self.text_cache.values():
            glDeleteTextures(1, [tex_id])
        self.text_cache.clear()

        # Resize first framebuffer texture
        glBindTexture(GL_TEXTURE_2D, self.fb_texture)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, width, height, 0, GL_RGB, GL_UNSIGNED_BYTE, None)

        # Resize second framebuffer texture
        glBindTexture(GL_TEXTURE_2D, self.fb_texture2)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, width, height, 0, GL_RGB, GL_UNSIGNED_BYTE, None)

        # Update projection matrix
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, width, height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    def _toggle_fullscreen(self):
        """Toggle between fullscreen and windowed mode."""
        self.fullscreen = not self.fullscreen

        if self.fullscreen:
            # Save current window size
            self.windowed_size = (self.width, self.height)

            # Get window position to determine which monitor we're on
            try:
                import ctypes
                from ctypes import wintypes

                # Make process DPI aware to get real physical pixels
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
                except:
                    try:
                        ctypes.windll.user32.SetProcessDPIAware()
                    except:
                        pass

                hwnd = pygame.display.get_wm_info()['window']
                # Get monitor that contains the window
                MONITOR_DEFAULTTONEAREST = 2
                monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)

                # Get monitor info with physical pixels
                class MONITORINFOEX(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD),
                               ("rcMonitor", wintypes.RECT),
                               ("rcWork", wintypes.RECT),
                               ("dwFlags", wintypes.DWORD),
                               ("szDevice", wintypes.WCHAR * 32)]

                mi = MONITORINFOEX()
                mi.cbSize = ctypes.sizeof(MONITORINFOEX)
                ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(mi))

                # Get the actual physical resolution using EnumDisplaySettings
                class DEVMODE(ctypes.Structure):
                    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32),
                               ("dmSpecVersion", wintypes.WORD),
                               ("dmDriverVersion", wintypes.WORD),
                               ("dmSize", wintypes.WORD),
                               ("dmDriverExtra", wintypes.WORD),
                               ("dmFields", wintypes.DWORD),
                               ("dmPositionX", wintypes.LONG),
                               ("dmPositionY", wintypes.LONG),
                               ("dmDisplayOrientation", wintypes.DWORD),
                               ("dmDisplayFixedOutput", wintypes.DWORD),
                               ("dmColor", wintypes.SHORT),
                               ("dmDuplex", wintypes.SHORT),
                               ("dmYResolution", wintypes.SHORT),
                               ("dmTTOption", wintypes.SHORT),
                               ("dmCollate", wintypes.SHORT),
                               ("dmFormName", wintypes.WCHAR * 32),
                               ("dmLogPixels", wintypes.WORD),
                               ("dmBitsPerPel", wintypes.DWORD),
                               ("dmPelsWidth", wintypes.DWORD),
                               ("dmPelsHeight", wintypes.DWORD),
                               ("dmDisplayFlags", wintypes.DWORD),
                               ("dmDisplayFrequency", wintypes.DWORD)]

                dm = DEVMODE()
                dm.dmSize = ctypes.sizeof(DEVMODE)
                ENUM_CURRENT_SETTINGS = -1

                if ctypes.windll.user32.EnumDisplaySettingsW(mi.szDevice, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
                    native_w = dm.dmPelsWidth
                    native_h = dm.dmPelsHeight
                else:
                    # Fallback to monitor info (may be scaled)
                    native_w = mi.rcMonitor.right - mi.rcMonitor.left
                    native_h = mi.rcMonitor.bottom - mi.rcMonitor.top

                print(f"Current monitor physical resolution: {native_w}x{native_h}")
            except Exception as e:
                print(f"Could not detect monitor: {e}")
                # Fallback to first desktop
                desktop_sizes = pygame.display.get_desktop_sizes()
                native_w, native_h = desktop_sizes[0] if desktop_sizes else (1920, 1080)

            # Go fullscreen using borderless window (works better with OBS/screen capture)
            self.screen = pygame.display.set_mode((native_w, native_h), DOUBLEBUF | OPENGL | NOFRAME)

            # Position window at monitor origin using Windows API and bring to front
            try:
                hwnd = pygame.display.get_wm_info()['window']
                monitor_x = mi.rcMonitor.left
                monitor_y = mi.rcMonitor.top
                HWND_TOP = 0
                SWP_SHOWWINDOW = 0x0040
                ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOP, monitor_x, monitor_y, native_w, native_h, SWP_SHOWWINDOW)
            except:
                pass  # Fall back to default position

            self.screen_width = native_w
            self.screen_height = native_h

            # Calculate 4:3 viewport centered on screen (letterboxing)
            target_aspect = 4.0 / 3.0
            screen_aspect = self.screen_width / self.screen_height

            if screen_aspect > target_aspect:
                # Screen is wider than 4:3 - black bars on sides
                vp_h = self.screen_height
                vp_w = int(vp_h * target_aspect)
            else:
                # Screen is taller than 4:3 - black bars top/bottom
                vp_w = self.screen_width
                vp_h = int(vp_w / target_aspect)

            vp_x = (self.screen_width - vp_w) // 2
            vp_y = (self.screen_height - vp_h) // 2
            self.viewport = (vp_x, vp_y, vp_w, vp_h)

            # Resize framebuffers to viewport size (4:3)
            self._resize_framebuffers(vp_w, vp_h)

            print(f"Viewport: {vp_w}x{vp_h} at ({vp_x},{vp_y})")
        else:
            # Restore windowed mode
            self.screen = pygame.display.set_mode(
                self.windowed_size,
                DOUBLEBUF | OPENGL | RESIZABLE
            )
            self._resize_framebuffers(*self.windowed_size)
            self.screen_width = self.windowed_size[0]
            self.screen_height = self.windowed_size[1]
            self.viewport = (0, 0, self.width, self.height)

        # Enable blending
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # Enable line smoothing
        glEnable(GL_LINE_SMOOTH)
        glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)

    def _draw_line(self, x1, y1, x2, y2, color, width=1.0):
        """Draw a line using immediate mode (simple fallback)."""
        glColor4f(*color)
        glLineWidth(width)
        glBegin(GL_LINES)
        glVertex2f(x1, y1)
        glVertex2f(x2, y2)
        glEnd()

    def _draw_rect(self, x, y, w, h, color):
        """Draw a filled rectangle."""
        glColor4f(*color)
        glBegin(GL_QUADS)
        glVertex2f(x, y)
        glVertex2f(x + w, y)
        glVertex2f(x + w, y + h)
        glVertex2f(x, y + h)
        glEnd()

    def _draw_circle(self, cx, cy, radius, color, segments=16):
        """Draw a filled circle."""
        import math
        glColor4f(*color)
        glBegin(GL_TRIANGLE_FAN)
        glVertex2f(cx, cy)
        for i in range(segments + 1):
            angle = 2.0 * math.pi * i / segments
            glVertex2f(cx + radius * math.cos(angle), cy + radius * math.sin(angle))
        glEnd()

    def _draw_text(self, text, x, y, color=(1.0, 1.0, 1.0, 1.0), font=None):
        """Draw text at position using pygame font rendered to texture."""
        if font is None:
            font = self.font

        # Check cache
        cache_key = (text, id(font), color[:3])
        if cache_key not in self.text_cache:
            # Render text to surface
            rgb = tuple(int(c * 255) for c in color[:3])
            text_surface = font.render(text, True, rgb)
            text_data = pygame.image.tostring(text_surface, "RGBA", True)
            width, height = text_surface.get_size()

            # Create texture
            tex_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, text_data)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)

            self.text_cache[cache_key] = (tex_id, width, height)

        tex_id, width, height = self.text_cache[cache_key]

        # Draw textured quad
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, tex_id)
        glColor4f(1, 1, 1, color[3])
        glBegin(GL_QUADS)
        glTexCoord2f(0, 1); glVertex2f(x, y)
        glTexCoord2f(1, 1); glVertex2f(x + width, y)
        glTexCoord2f(1, 0); glVertex2f(x + width, y + height)
        glTexCoord2f(0, 0); glVertex2f(x, y + height)
        glEnd()
        glDisable(GL_TEXTURE_2D)

    def _draw_text_glowing(self, text, x, y, color, glow_color=None, font=None, glow_strength=1.0):
        """Draw text with a subtle neon glow effect."""
        if glow_color is None:
            glow_color = color

        # Use additive blending for glow
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)

        # Draw subtle glow layers
        for offset in [3, 2]:
            glow_alpha = 0.06 * glow_strength * (4 - offset) / 2
            gc = (glow_color[0], glow_color[1], glow_color[2], glow_alpha)
            for dx in [-offset, 0, offset]:
                for dy in [-offset, 0, offset]:
                    if dx != 0 or dy != 0:
                        self._draw_text(text, x + dx, y + dy, gc, font)

        # Restore normal blending
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # Draw main text on top
        self._draw_text(text, x, y, color, font)

    def _draw_waveform(self, x, y, w, h, channel_idx):
        """Draw a single channel's waveform."""
        color = self.channel_colors[channel_idx]
        is_active = self.key_on[channel_idx]

        # DAC channel is active when DAC is enabled (FM key_on doesn't apply)
        if channel_idx == 5 and self.dac_enabled:
            is_active = True

        with self._lock:
            full_data = self.waveforms[channel_idx].copy()
            valid_count = self.valid_samples[channel_idx]
            samples_advanced = self.samples_since_last_frame[channel_idx]
            self.samples_since_last_frame[channel_idx] = 0

        is_noise = (channel_idx == 9)
        is_dac_active = (channel_idx == 5 and self.dac_enabled)
        use_scrolling = is_noise or is_dac_active
        # Adaptive display: scrolling for noise/DAC, pitch-based for tonal channels
        if use_scrolling:
            display_samples = self.scroll_display_samples
        else:
            display_samples = self._get_display_samples_for_channel(channel_idx)

        # Get display data - use triggered display for tonal channels (same as app.py)
        if self.triggered_display and not use_scrolling:
            if valid_count >= display_samples * 2:
                trigger_idx = int(self._find_trigger(channel_idx, full_data, display_samples, samples_advanced))
                end_idx = int(trigger_idx + display_samples)
                y_data = full_data[trigger_idx:end_idx].copy()
            elif valid_count >= display_samples:
                y_data = full_data[-display_samples:].copy()
            else:
                y_data = np.zeros(display_samples, dtype=np.float32)
        else:
            # Simple scrolling for noise/DAC
            if valid_count >= display_samples:
                y_data = full_data[-display_samples:].copy()
            elif valid_count > 0:
                y_data = np.zeros(display_samples, dtype=np.float32)
                y_data[-valid_count:] = full_data[-valid_count:]
            else:
                y_data = np.zeros(display_samples, dtype=np.float32)

        # Auto-scale
        max_amp = np.abs(y_data).max()
        if max_amp > 0.001:
            desired_scale = self.target_amplitude / max_amp
            safe_scale = 0.95 / max_amp
            target_scale = min(desired_scale, safe_scale, self.max_amplitude_scale)

            current_scale = self.amplitude_scale[channel_idx]
            if target_scale < current_scale:
                new_scale = current_scale * 0.7 + target_scale * 0.3
            else:
                new_scale = current_scale * self.amplitude_smoothing + target_scale * (1 - self.amplitude_smoothing)

            self.amplitude_scale[channel_idx] = new_scale
            y_data = y_data * new_scale

        # Draw background panel
        self._draw_rect(x, y, w, h, self.COLORS['panel'])

        # Draw subtle border
        self._draw_line(x, y, x + w, y, self.COLORS['dim'], 1.0)
        self._draw_line(x, y, x, y + h, self.COLORS['dim'], 1.0)

        # Enable scissor test to clip glow and waveform to box
        glEnable(GL_SCISSOR_TEST)
        # Note: OpenGL scissor uses bottom-left origin, pygame uses top-left
        glScissor(int(x), int(self.height - y - h), int(w), int(h))

        # Draw glow (gradient effect simulated with rectangles)
        if is_active and max_amp > 0.005:
            # Use sqrt for perceptually linear response - small signals still visible
            target_intensity = min(1.0, np.sqrt(max_amp) * 1.2)
        else:
            target_intensity = 0.0

        current_intensity = self.glow_intensity[channel_idx]
        if target_intensity > current_intensity:
            new_intensity = current_intensity + (target_intensity - current_intensity) * self.glow_fade_speed * 2
        else:
            new_intensity = current_intensity + (target_intensity - current_intensity) * self.glow_fade_speed
        self.glow_intensity[channel_idx] = new_intensity

        # Draw center line (white)
        center_y = y + h / 2
        self._draw_line(x, center_y, x + w, center_y, (1.0, 1.0, 1.0, 1.0), 1.0)

        # Draw smooth gradient glow based on amplitude using vertex colors
        if new_intensity > 0.01:
            glBlendFunc(GL_SRC_ALPHA, GL_ONE)  # Additive blending
            glow_height = h * 0.4 * new_intensity  # Height based on intensity
            glow_alpha = 0.5 * new_intensity  # Stronger glow

            # Draw gradient quad - bright at center, fading to edges
            glBegin(GL_QUADS)
            # Top edge (transparent)
            glColor4f(color[0], color[1], color[2], 0.0)
            glVertex2f(x, center_y - glow_height)
            glVertex2f(x + w, center_y - glow_height)
            # Center (bright)
            glColor4f(color[0], color[1], color[2], glow_alpha)
            glVertex2f(x + w, center_y)
            glVertex2f(x, center_y)
            glEnd()

            glBegin(GL_QUADS)
            # Center (bright)
            glColor4f(color[0], color[1], color[2], glow_alpha)
            glVertex2f(x, center_y)
            glVertex2f(x + w, center_y)
            # Bottom edge (transparent)
            glColor4f(color[0], color[1], color[2], 0.0)
            glVertex2f(x + w, center_y + glow_height)
            glVertex2f(x, center_y + glow_height)
            glEnd()

            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # Draw waveform
        is_dac_channel = (channel_idx == 5 and self.dac_enabled)
        if len(y_data) > 1:
            line_color = color if is_active else (color[0] * 0.5, color[1] * 0.5, color[2] * 0.5, 1.0)
            glColor4f(*line_color)
            glLineWidth(2.0 if is_active else 1.0)

            # Downsample if needed for performance (target ~256 points for drawing)
            draw_data = y_data
            if len(y_data) > 256:
                step = len(y_data) // 256
                draw_data = y_data[::step]

            glBegin(GL_LINE_STRIP)
            for i, val in enumerate(draw_data):
                px = x + (i / len(draw_data)) * w
                py = center_y - val * (h / 2) * 0.9
                glVertex2f(px, py)
            glEnd()

        # Disable scissor test
        glDisable(GL_SCISSOR_TEST)

        # Draw label - show DAC when in DAC mode for channel 5
        if channel_idx == 5 and self.dac_enabled:
            label = "DAC"
        else:
            label = self.channel_labels[channel_idx]
        self._draw_text(label, x + 4, y + 2, color)

    def _draw_keyboard(self, x, y, w, h):
        """Draw the piano keyboard."""
        white_key_indices = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}
        black_key_boundary = {1: 1, 3: 2, 6: 4, 8: 5, 10: 6}

        num_white_keys = 0
        for n in range(self.keyboard_low_note, self.keyboard_high_note + 1):
            if (n % 12) in white_key_indices:
                num_white_keys += 1

        pixels_per_white_key = h / num_white_keys
        black_key_length = w * 0.55

        # Draw white key background
        self._draw_rect(x, y, w, h, (0.92, 0.92, 0.94, 1.0))

        # Draw white key borders
        white_key_count = 0
        for midi_note in range(self.keyboard_low_note, self.keyboard_high_note + 1):
            if (midi_note % 12) in white_key_indices:
                white_key_count += 1
                if midi_note < self.keyboard_high_note:
                    border_y = y + (num_white_keys - white_key_count) * pixels_per_white_key
                    self._draw_line(x, border_y, x + w, border_y, (0.5, 0.5, 0.55, 0.6), 1.0)

        # Draw black keys
        for midi_note in range(self.keyboard_low_note, self.keyboard_high_note + 1):
            note_in_octave = midi_note % 12
            if note_in_octave in black_key_boundary:
                # Count white keys up to this point
                wk_count = 0
                for n in range(self.keyboard_low_note, midi_note + 1):
                    if (n % 12) in white_key_indices:
                        wk_count += 1

                key_y = y + (num_white_keys - wk_count) * pixels_per_white_key
                black_height = pixels_per_white_key * 0.65

                self._draw_rect(x, key_y - black_height / 2, black_key_length, black_height, (0.1, 0.1, 0.12, 1.0))

        # Draw pitch indicators
        for ch in range(self.TOTAL_CHANNELS - 1):
            pitch = self.channel_pitch[ch]
            is_active = pitch > 0 and self.key_on[ch] and self.keyboard_low_note <= pitch <= self.keyboard_high_note

            # Glow transition
            target_glow = 1.0 if is_active else 0.0
            current_glow = self.indicator_glow[ch]
            if target_glow > current_glow:
                new_glow = current_glow + (target_glow - current_glow) * 0.3
            else:
                new_glow = current_glow + (target_glow - current_glow) * 0.08
            self.indicator_glow[ch] = new_glow

            if new_glow > 0.01 and pitch > 0 and self.keyboard_low_note <= pitch <= self.keyboard_high_note:
                # Calculate Y position
                wk_count = 0
                for n in range(self.keyboard_low_note, int(pitch) + 1):
                    if (n % 12) in white_key_indices:
                        wk_count += 1

                note_in_octave = int(pitch) % 12
                if note_in_octave in white_key_indices:
                    pos = wk_count - 0.5
                else:
                    pos = wk_count

                indicator_y = y + (num_white_keys - pos) * pixels_per_white_key
                color = self.channel_colors[ch]

                # Glow
                glow_color = (color[0], color[1], color[2], 0.25 * new_glow)
                self._draw_rect(x, indicator_y - 5, w + 8, 10, glow_color)

                if is_active:
                    # Line
                    self._draw_line(x, indicator_y, x + w, indicator_y, color, 2.0)
                    # Circle hanging off edge
                    self._draw_circle(x + w + 4, indicator_y, 5.0, color)

    def _render_scene(self):
        """Render the main scene to framebuffer."""
        dpi = getattr(self, 'dpi_scale', 1.0)
        padding = int(10 * dpi)
        status_height = int(60 * dpi)
        keyboard_width = int(50 * dpi)

        available_height = self.height - status_height - padding * 2
        available_width = self.width - padding * 2 - keyboard_width - padding

        fm_rows = 3
        fm_cols = 2
        psg_cols = 4

        fm_height_per_channel = (available_height * 0.75) / fm_rows
        psg_height = available_height * 0.25

        # Calculate widths so FM and PSG span the same total width
        # FM: 2 boxes with 1 gap = 2 boxes + 1 padding
        # PSG: 4 boxes with 3 gaps = 4 boxes + 3 paddings
        # Both should span: available_width
        fm_width = (available_width - padding) / fm_cols  # 2 boxes, 1 gap
        psg_width = (available_width - padding * 3) / psg_cols  # 4 boxes, 3 gaps

        # Calculate global amplitude for pulse using envelope follower
        # Use RMS of loudest active channels for better musical response
        max_rms = 0.0
        with self._lock:
            for ch in range(self.TOTAL_CHANNELS):
                if self.valid_samples[ch] > 100 and self.key_on[ch]:
                    chunk = self.waveforms[ch][-512:]
                    # RMS is smoother than peak
                    rms = np.sqrt(np.mean(chunk ** 2))
                    max_rms = max(max_rms, rms)

        # Target pulse based on loudest channel
        target_pulse = min(1.0, max_rms * 4.0)

        # Envelope follower: fast attack, slow decay
        if target_pulse > self.pulse_intensity:
            # Attack - respond quickly to loud sounds
            self.pulse_intensity += (target_pulse - self.pulse_intensity) * self.pulse_attack
        else:
            # Decay - fade out smoothly
            self.pulse_intensity += (target_pulse - self.pulse_intensity) * self.pulse_decay

        # Clear with background color
        bg = self.COLORS['background']
        glClearColor(*bg)
        glClear(GL_COLOR_BUFFER_BIT)

        # Set up orthographic projection
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, self.width, self.height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        # Draw keyboard
        keyboard_y = status_height + padding
        self._draw_keyboard(padding, keyboard_y, keyboard_width, available_height - padding)

        # Draw FM channels (2x3)
        for row in range(fm_rows):
            for col in range(fm_cols):
                ch = row * fm_cols + col
                if ch < self.FM_CHANNELS:
                    cx = keyboard_width + padding * 2 + col * fm_width
                    cy = status_height + padding + row * fm_height_per_channel
                    self._draw_waveform(cx, cy, fm_width - padding, fm_height_per_channel - padding, ch)

        # Draw PSG channels (1x4) - 2 PSG boxes under each FM column
        psg_y = status_height + padding + available_height * 0.75
        x_start = keyboard_width + padding * 2
        fm_draw_width = fm_width - padding  # Actual drawn width of FM box

        # Each FM column fits 2 PSG boxes with 1 gap between them
        psg_box_width = (fm_draw_width - padding) / 2

        for i in range(psg_cols):
            ch = self.FM_CHANNELS + i
            if i < 2:
                # Left column (under FM left)
                cx = x_start + i * (psg_box_width + padding)
            else:
                # Right column (under FM right) - starts at fm_width
                cx = x_start + fm_width + (i - 2) * (psg_box_width + padding)
            self._draw_waveform(cx, psg_y, psg_box_width, psg_height - padding, ch)

        # Draw status bar
        self._draw_status_bar(padding, padding, self.width - padding * 2, status_height - padding)

    def _draw_status_bar(self, x, y, w, h):
        """Draw status bar with branding and info."""
        dpi = getattr(self, 'dpi_scale', 1.0)
        pad = 8
        line_height = 18  # Spacing between metadata rows

        # Background
        self._draw_rect(x, y, w, h, self.COLORS['panel'])

        label_color = (0.5, 0.5, 0.5, 1.0)
        fm90s_color = (0.0, 0.24, 0.67, 1.0)
        engine_color = (1.0, 0.2, 0.2, 1.0)

        # === LEFT COLUMN ===
        # FM-90s branding (top row)
        self._draw_text_glowing("FM-90s", x + pad, y + 2, fm90s_color, font=self.font_fm90s, glow_strength=0.8)
        # Status message + FPS (bottom row)
        self._draw_text(self.status_message, x + pad, y + 44, self.COLORS['white'], self.font)
        fps_str = f"({self.current_fps:.0f})"
        fps_color = (0.4, 0.4, 0.4, 1.0) if self.current_fps >= 55 else (1.0, 0.3, 0.3, 1.0)
        status_w = self.font.size(self.status_message)[0]
        self._draw_text(fps_str, x + pad + status_w + 4, y + 44, fps_color, self.font)

        # === CENTER COLUMN ===
        # GENESIS ENGINE (top row)
        engine_text = "GENESIS ENGINE"
        engine_width = self.font_genesis.size(engine_text)[0]
        engine_x = x + (w - engine_width) // 2
        self._draw_text_glowing(engine_text, engine_x, y + 4, engine_color, font=self.font_genesis, glow_strength=0.8)

        # Progress bar and time (bottom row)
        if self.total_duration > 0:
            progress = min(1.0, self.elapsed_time / self.total_duration)
            bar_width = 180
            bar_height = 10
            bar_x = x + (w - bar_width) // 2
            bar_y = y + 48

            mins = int(self.elapsed_time) // 60
            secs = int(self.elapsed_time) % 60
            total_mins = int(self.total_duration) // 60
            total_secs = int(self.total_duration) % 60
            time_str = f"{mins}:{secs:02d}/{total_mins}:{total_secs:02d}"
            time_w = self.font.size(time_str)[0]
            self._draw_text(time_str, bar_x - time_w - 8, y + 44, self.COLORS['dim'], self.font)

            self._draw_rect(bar_x, bar_y, bar_width, bar_height, (0.2, 0.2, 0.25, 1.0))
            if progress > 0:
                self._draw_rect(bar_x, bar_y, bar_width * progress, bar_height, (0.2, 0.8, 1.0, 1.0))

        # === RIGHT COLUMN: Title, Composer, Game (stacked, right-aligned) ===
        right_x = x + w - pad
        cur_y = y + 4

        if self.current_file:
            label = "Title: "
            label_w = self.font_small.size(label)[0]
            value_w = self.font_small.size(self.current_file)[0]
            self._draw_text(label, right_x - label_w - value_w, cur_y, label_color, self.font_small)
            self._draw_text(self.current_file, right_x - value_w, cur_y, self.COLORS['white'], self.font_small)
            cur_y += line_height

        if self.composer:
            label = "Composer: "
            label_w = self.font_small.size(label)[0]
            value_w = self.font_small.size(self.composer)[0]
            self._draw_text(label, right_x - label_w - value_w, cur_y, label_color, self.font_small)
            self._draw_text(self.composer, right_x - value_w, cur_y, self.COLORS['white'], self.font_small)
            cur_y += line_height

        if self.game:
            label = "Game: "
            label_w = self.font_small.size(label)[0]
            value_w = self.font_small.size(self.game)[0]
            self._draw_text(label, right_x - label_w - value_w, cur_y, label_color, self.font_small)
            self._draw_text(self.game, right_x - value_w, cur_y, self.COLORS['white'], self.font_small)

    def _apply_zoom_shader(self):
        """Apply zoom/pulse effect to content (before CRT effects)."""
        # Render from fb_texture to fb_texture2 with zoom
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer2)
        glViewport(0, 0, self.width, self.height)
        glClear(GL_COLOR_BUFFER_BIT)

        if self.zoom_shader is None:
            # Fallback: just copy without zoom
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, self.fb_texture)
            glColor4f(1, 1, 1, 1)
            glBegin(GL_QUADS)
            glTexCoord2f(0, 0); glVertex2f(-1, -1)
            glTexCoord2f(1, 0); glVertex2f(1, -1)
            glTexCoord2f(1, 1); glVertex2f(1, 1)
            glTexCoord2f(0, 1); glVertex2f(-1, 1)
            glEnd()
            glDisable(GL_TEXTURE_2D)
            return

        glUseProgram(self.zoom_shader)

        # Set uniforms
        glUniform1f(glGetUniformLocation(self.zoom_shader, "pulseIntensity"), self.pulse_intensity)

        # Bind scene texture
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self.fb_texture)
        glUniform1i(glGetUniformLocation(self.zoom_shader, "screenTexture"), 0)

        # Draw fullscreen quad
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(-1, -1)
        glTexCoord2f(1, 0); glVertex2f(1, -1)
        glTexCoord2f(1, 1); glVertex2f(1, 1)
        glTexCoord2f(0, 1); glVertex2f(-1, 1)
        glEnd()

        glUseProgram(0)

    def _apply_crt_shader(self):
        """Apply CRT post-processing shader (reads from zoomed framebuffer)."""
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

        # Clear full screen with black (for letterbox bars)
        glViewport(0, 0, self.screen_width, self.screen_height)
        glClearColor(0, 0, 0, 1)
        glClear(GL_COLOR_BUFFER_BIT)

        # Set viewport for output (letterboxed in fullscreen, full window otherwise)
        vp_x, vp_y, vp_w, vp_h = self.viewport
        glViewport(vp_x, vp_y, vp_w, vp_h)

        # Choose shader: CRT effects or passthrough (zoom shader is a passthrough)
        shader = self.crt_shader if self.crt_enabled else self.zoom_shader
        if shader is None:
            # Fallback if no shader available
            glActiveTexture(GL_TEXTURE0)
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, self.fb_texture2)
            glColor4f(1, 1, 1, 1)
            glBegin(GL_QUADS)
            glTexCoord2f(0, 0); glVertex2f(-1, -1)
            glTexCoord2f(1, 0); glVertex2f(1, -1)
            glTexCoord2f(1, 1); glVertex2f(1, 1)
            glTexCoord2f(0, 1); glVertex2f(-1, 1)
            glEnd()
            glDisable(GL_TEXTURE_2D)
            return

        glUseProgram(shader)

        # Set uniforms based on which shader we're using
        if self.crt_enabled:
            glUniform1f(glGetUniformLocation(shader, "time"), pygame.time.get_ticks() / 1000.0)
            glUniform2f(glGetUniformLocation(shader, "resolution"), float(self.width), float(self.height))
        else:
            # Zoom shader just needs pulseIntensity (set to 0 for no effect)
            glUniform1f(glGetUniformLocation(shader, "pulseIntensity"), 0.0)

        # Bind zoomed framebuffer texture
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self.fb_texture2)
        glUniform1i(glGetUniformLocation(shader, "screenTexture"), 0)

        # Draw fullscreen quad (viewport handles letterboxing)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(-1, -1)
        glTexCoord2f(1, 0); glVertex2f(1, -1)
        glTexCoord2f(1, 1); glVertex2f(1, 1)
        glTexCoord2f(0, 1); glVertex2f(-1, 1)
        glEnd()

        glUseProgram(0)

    def _start_ffmpeg(self):
        """Start ffmpeg process for real-time encoding."""
        ffmpeg_path = self._find_ffmpeg()
        if not ffmpeg_path:
            print("WARNING: ffmpeg not found, recording disabled")
            self.recording = False
            return

        # We'll mux audio later since it's generated during playback
        # For now, encode video to a temp file
        self.temp_video = os.path.splitext(self.record_file)[0] + "_video.mp4"

        cmd = [
            ffmpeg_path, '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{self.width}x{self.height}',
            '-pix_fmt', 'rgb24',
            '-r', '60',
            '-i', 'pipe:0',
            '-c:v', 'libx264',
            '-preset', 'fast',  # Fast for real-time
            '-crf', '18',
            '-pix_fmt', 'yuv420p',
            self.temp_video
        ]

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            self.ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags
            )
            print(f"Recording to: {self.record_file}")
        except Exception as e:
            print(f"WARNING: Failed to start ffmpeg: {e}")
            self.recording = False

    def _capture_frame(self):
        """Capture current frame and pipe to ffmpeg in real-time."""
        # Only capture after playback has started (for audio sync)
        if not self.recording_started:
            return

        # Start ffmpeg on first frame
        if self.ffmpeg_proc is None:
            self._start_ffmpeg()
            if not self.recording:
                return

        # Read from screen (after CRT shader is applied)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        pixels = glReadPixels(0, 0, self.width, self.height, GL_RGB, GL_UNSIGNED_BYTE)
        frame = np.frombuffer(pixels, dtype=np.uint8).reshape(self.height, self.width, 3)

        # OpenGL origin is bottom-left, flip vertically
        frame = np.flipud(frame)

        # Pipe to ffmpeg
        try:
            self.ffmpeg_proc.stdin.write(frame.tobytes())
            self.frames_written += 1
        except (BrokenPipeError, OSError):
            print("WARNING: ffmpeg pipe closed unexpectedly")
            self.recording = False

    def _find_ffmpeg(self):
        """Find ffmpeg executable."""
        import shutil

        # Check if in PATH
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg:
            return ffmpeg

        # Check common Windows locations
        common_paths = [
            os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Packages'),
            r'C:\ffmpeg\bin',
            r'C:\Program Files\ffmpeg\bin',
        ]

        for base_path in common_paths:
            if os.path.exists(base_path):
                for root, dirs, files in os.walk(base_path):
                    if 'ffmpeg.exe' in files:
                        return os.path.join(root, 'ffmpeg.exe')

        return None

    def _finalize_recording(self):
        """Finalize real-time recording and mux audio if available."""
        if not self.ffmpeg_proc:
            return

        print(f"\nFinalizing recording...")
        print(f"Frames: {self.frames_written}, Resolution: {self.width}x{self.height}")

        # Close video encoding pipe
        try:
            self.ffmpeg_proc.stdin.close()
            self.ffmpeg_proc.wait(timeout=30)
        except Exception as e:
            print(f"WARNING: Error closing ffmpeg: {e}")

        # Check if we have audio to mux
        audio_file = getattr(self, 'audio_file', None)
        temp_video = getattr(self, 'temp_video', None)
        actual_fps = getattr(self, 'actual_fps', 60.0)

        if audio_file and os.path.exists(audio_file) and temp_video and os.path.exists(temp_video):
            ffmpeg_path = self._find_ffmpeg()
            if ffmpeg_path:
                print(f"  Muxing audio (adjusting video to {actual_fps:.1f} fps)...")
                # Use filter to adjust video speed to match audio duration
                # pts = presentation timestamp, dividing slows down if actual_fps < 60
                speed_factor = 60.0 / actual_fps
                cmd = [
                    ffmpeg_path, '-y',
                    '-i', temp_video,
                    '-i', audio_file,
                    '-filter:v', f'setpts={speed_factor}*PTS',
                    '-r', str(actual_fps),
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-crf', '18',
                    '-c:a', 'aac',
                    '-b:a', '256k',
                    '-shortest',
                    self.record_file
                ]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        os.remove(temp_video)
                        os.remove(audio_file)
                        print(f"Saved: {self.record_file}")
                    else:
                        print(f"WARNING: Mux failed: {result.stderr[:200]}")
                        os.rename(temp_video, self.record_file)
                        print(f"Saved (no audio): {self.record_file}")
                except Exception as e:
                    print(f"WARNING: Mux error: {e}")
                    if os.path.exists(temp_video):
                        os.rename(temp_video, self.record_file)
        elif temp_video and os.path.exists(temp_video):
            os.rename(temp_video, self.record_file)
            print(f"Saved: {self.record_file}")

    def init_offscreen(self, width: int, height: int, crt_enabled: bool = True):
        """Initialize for offscreen rendering (minimal window, renders to framebuffer)."""
        pygame.init()

        # Position window off-screen or minimized
        os.environ['SDL_VIDEO_WINDOW_POS'] = '-10000,-10000'

        pygame.display.set_caption("Rendering...")
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)

        # Create a small window (we render to framebuffers)
        pygame.display.set_mode((320, 240), DOUBLEBUF | OPENGL)

        self.width = width
        self.height = height
        self.crt_enabled = crt_enabled
        self.offscreen_mode = True

        # Initialize OpenGL with our target resolution
        self._init_gl()

    def render_frame_offscreen(self):
        """Render a single frame and return as numpy array."""
        # Pass 1: Render scene to framebuffer -> fb_texture
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glViewport(0, 0, self.width, self.height)
        self._render_scene()

        # Pass 2: Apply zoom shader: fb_texture -> framebuffer2 -> fb_texture2
        self._apply_zoom_shader()

        # Pass 3: Apply CRT shader: fb_texture2 -> framebuffer -> fb_texture
        # (Re-use framebuffer to avoid reading/writing same texture)
        shader = self.crt_shader if self.crt_enabled else self.zoom_shader
        glUseProgram(shader)

        # Render CRT back to framebuffer (fb_texture)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glViewport(0, 0, self.width, self.height)
        glClear(GL_COLOR_BUFFER_BIT)

        # Set uniforms
        if self.crt_enabled:
            glUniform1f(glGetUniformLocation(shader, "time"), 0)
            glUniform2f(glGetUniformLocation(shader, "resolution"), self.width, self.height)

        # Bind zoom output texture (fb_texture2)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self.fb_texture2)
        glUniform1i(glGetUniformLocation(shader, "screenTexture"), 0)

        # Draw fullscreen quad
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(-1, -1)
        glTexCoord2f(1, 0); glVertex2f(1, -1)
        glTexCoord2f(1, 1); glVertex2f(1, 1)
        glTexCoord2f(0, 1); glVertex2f(-1, 1)
        glEnd()

        glUseProgram(0)

        # Read pixels from framebuffer (fb_texture)
        pixels = glReadPixels(0, 0, self.width, self.height, GL_RGB, GL_UNSIGNED_BYTE)
        frame = np.frombuffer(pixels, dtype=np.uint8).reshape(self.height, self.width, 3)
        frame = np.flipud(frame)  # OpenGL origin is bottom-left

        return frame.copy()

    def cleanup_offscreen(self):
        """Clean up offscreen rendering resources."""
        pygame.quit()
        if 'SDL_VIDEO_WINDOW_POS' in os.environ:
            del os.environ['SDL_VIDEO_WINDOW_POS']

    def run(self, title: str = "Genesis Engine Visualizer", width: int = 1280, height: int = 720,
            fullscreen: bool = False, record_file: str = None):
        """Run the visualizer.

        Args:
            title: Window title
            width: Render width (and window width if it fits)
            height: Render height (and window height if it fits)
            fullscreen: Start in fullscreen mode
            record_file: If set, record video to this file (requires opencv-python)
        """
        pygame.init()

        # Recording setup
        self.recording = record_file is not None
        self.record_file = record_file
        self.recording_started = False  # Set to True when playback starts (for sync)
        self.ffmpeg_proc = None  # Real-time encoding process
        self.frames_written = 0

        if self.recording and not _HAS_CV2:
            print("WARNING: opencv-python not installed. Recording disabled.")
            print("Install with: pip install opencv-python")
            self.recording = False

        # Set DPI awareness on Windows so we get physical pixels, not scaled logical pixels
        # This makes CRT effects consistent regardless of Windows display scaling
        self.dpi_scale = 1.0
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            # Get the DPI scale factor
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
            self.dpi_scale = dpi / 96.0  # 96 is the default DPI (100% scaling)
        except:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except:
                pass

        # When recording, use exact requested dimensions (no DPI scaling, no window scaling)
        # Window may extend off-screen but OpenGL capture still works at full resolution
        if self.recording:
            self.width = width
            self.height = height
            self.window_width = width
            self.window_height = height
        else:
            # Scale by DPI for proper display
            self.width = int(width * self.dpi_scale)
            self.height = int(height * self.dpi_scale)
            self.window_width = self.width
            self.window_height = self.height

        pygame.display.set_caption(title)

        # Use compatibility profile for immediate mode + shaders
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)

        self.screen = pygame.display.set_mode((self.window_width, self.window_height), DOUBLEBUF | OPENGL | RESIZABLE)

        self._init_gl()

        # Start in fullscreen if requested
        if fullscreen:
            self._toggle_fullscreen()

        clock = pygame.time.Clock()
        self.running = True

        while self.running:
            for event in pygame.event.get():
                if event.type == QUIT:
                    self.running = False
                elif event.type == VIDEORESIZE:
                    # Handle window resize
                    if not self.fullscreen:
                        self.screen = pygame.display.set_mode(
                            (event.w, event.h),
                            DOUBLEBUF | OPENGL | RESIZABLE
                        )
                        self._resize_framebuffers(event.w, event.h)
                        self.screen_width = event.w
                        self.screen_height = event.h
                        self.viewport = (0, 0, event.w, event.h)
                elif event.type == KEYDOWN:
                    if event.key == K_ESCAPE:
                        if self.fullscreen:
                            self._toggle_fullscreen()  # Exit fullscreen first
                        else:
                            self.running = False
                    elif event.key == K_F11:
                        self._toggle_fullscreen()

            # Pass 1: Render scene to framebuffer
            glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
            glViewport(0, 0, self.width, self.height)
            self._render_scene()

            # Pass 2: Apply zoom/pulse effect (content zooms, scanlines stay fixed)
            self._apply_zoom_shader()

            # Pass 3: Apply CRT shader to screen
            self._apply_crt_shader()

            # Capture frame for recording (before flip, from the CRT output framebuffer)
            if self.recording:
                self._capture_frame()

            pygame.display.flip()
            clock.tick(60)
            self.current_fps = clock.get_fps()

        pygame.quit()


def test_visualizer():
    """Test the visualizer with dummy waveforms."""
    import time
    import math

    app = VisualizerApp()
    app.set_playback_info("test_song.vgm", 180.0)
    app.set_status("Testing...")

    # Start the app in background, generate data in main thread before
    def generate_test_data():
        sample_rate = 44100
        samples_per_update = 256
        phases = [0.0] * app.TOTAL_CHANNELS
        freqs = [220 * (1 + ch * 0.5) for ch in range(app.TOTAL_CHANNELS)]

        frame = 0
        while app.running:
            for ch in range(app.TOTAL_CHANNELS):
                freq = freqs[ch]
                phase_inc = freq / sample_rate
                wave = np.zeros(samples_per_update, dtype=np.float32)

                phase = phases[ch]
                for i in range(samples_per_update):
                    if ch >= app.FM_CHANNELS:
                        wave[i] = 0.7 if phase < 0.5 else -0.7
                    else:
                        wave[i] = np.sin(2 * np.pi * phase + 0.3 * np.sin(4 * np.pi * phase)) * 0.7

                    phase += phase_inc
                    if phase >= 1.0:
                        phase -= 1.0

                phases[ch] = phase
                app.update_waveform(ch, wave)
                app.set_key_on(ch, True)

                # Set pitch for keyboard
                midi_note = 60 + ch * 2
                app.set_channel_pitch(ch, midi_note)

            frame += 1
            time.sleep(0.008)  # ~120fps data generation

    # Set running before thread starts
    app.running = True

    test_thread = threading.Thread(target=generate_test_data, daemon=True)
    test_thread.start()

    # Small delay to let data accumulate
    time.sleep(0.1)

    app.run()


if __name__ == "__main__":
    test_visualizer()
