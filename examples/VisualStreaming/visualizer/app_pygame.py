"""
Pygame + OpenGL visualizer with CRT shader effects.
Drop-in replacement for app.py with enhanced visual capabilities.
"""

import numpy as np
from typing import Optional, Callable
import threading
import queue

import pygame
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GL import shaders


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

// Attempt to simulate bloom by sampling neighbors
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

    // Add bloom for glow effect on bright areas
    vec3 bloom = sampleBloom(screenTexture, uv, pixelSize);
    float brightness = dot(color, vec3(0.299, 0.587, 0.114));
    color += bloom * brightness * 0.3;

    // Scanlines - these are physical CRT properties, use SCREEN coordinates (TexCoord not uv)
    float scanlinePos = TexCoord.y * resolution.y;
    float scanline = sin(scanlinePos * 3.14159);
    float scanlineMask = 0.85 + 0.15 * smoothstep(-0.5, 0.5, scanline);
    color *= scanlineMask;

    // Phosphor/aperture grille - also physical, use SCREEN coordinates
    float pixelX = TexCoord.x * resolution.x;
    int subpixel = int(mod(pixelX, 3.0));
    vec3 phosphorMask;
    if (subpixel == 0) {
        phosphorMask = vec3(1.0, 0.7, 0.7);
    } else if (subpixel == 1) {
        phosphorMask = vec3(0.7, 1.0, 0.7);
    } else {
        phosphorMask = vec3(0.7, 0.7, 1.0);
    }
    color *= mix(vec3(1.0), phosphorMask, 0.15);

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

    def __init__(self):
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

        self._lock = threading.Lock()
        self.valid_samples = [0] * self.TOTAL_CHANNELS
        self.display_samples = 256
        self.scroll_display_samples = 512

        # Trigger state
        self.trigger_offset = [self.display_samples + 50] * self.TOTAL_CHANNELS
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

    def set_playback_info(self, filename: str, duration: float):
        self.current_file = filename
        self.total_duration = duration

    def set_progress(self, progress: float, elapsed: float):
        self.elapsed_time = elapsed

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
        # Initialize fonts
        pygame.font.init()
        self.font = pygame.font.SysFont('consolas', 14)
        self.font_small = pygame.font.SysFont('consolas', 12)
        # Specific fonts for branding
        # FM-90s uses Neuropol, Genesis Engine uses NiseGenesis
        try:
            self.font_fm90s = pygame.font.SysFont('Neuropol', 26)
            print("Using Neuropol for FM-90s")
        except:
            self.font_fm90s = pygame.font.SysFont('Impact', 26, bold=True)
            print("Neuropol not found, using Impact")

        try:
            self.font_genesis = pygame.font.SysFont('NiseGenesis', 24)
            print("Using NiseGenesis for Genesis Engine")
        except:
            self.font_genesis = pygame.font.SysFont('Impact', 24, bold=True)
            print("NiseGenesis not found, using Impact")

        # Fallback brand font
        self.font_brand = pygame.font.SysFont('Impact', 24, bold=True)

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

        with self._lock:
            full_data = self.waveforms[channel_idx].copy()
            valid_count = self.valid_samples[channel_idx]
            samples_advanced = self.samples_since_last_frame[channel_idx]
            self.samples_since_last_frame[channel_idx] = 0

        is_noise = (channel_idx == 9)
        is_dac_active = (channel_idx == 5 and self.dac_enabled)
        use_scrolling = is_noise or is_dac_active
        display_samples = self.scroll_display_samples if use_scrolling else self.display_samples

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
            # DAC gets thicker line for visibility
            glLineWidth(3.0 if is_dac_channel else (2.0 if is_active else 1.0))
            glBegin(GL_LINE_STRIP)
            for i, val in enumerate(y_data):
                px = x + (i / len(y_data)) * w
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
        padding = 10
        status_height = 60
        keyboard_width = 50

        available_height = self.height - status_height - padding * 2
        available_width = self.width - padding * 2 - keyboard_width - padding

        fm_rows = 3
        fm_cols = 2
        psg_cols = 4

        fm_height_per_channel = (available_height * 0.75) / fm_rows
        psg_height = available_height * 0.25

        fm_width = (available_width - padding) / fm_cols
        psg_width = (available_width - padding * 3) / psg_cols

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

        # Draw PSG channels (1x4)
        psg_y = status_height + padding + available_height * 0.75
        for i in range(psg_cols):
            ch = self.FM_CHANNELS + i
            cx = keyboard_width + padding * 2 + i * psg_width + i * padding
            self._draw_waveform(cx, psg_y, psg_width - padding, psg_height - padding, ch)

        # Draw status bar
        self._draw_status_bar(padding, padding, self.width - padding * 2, status_height - padding)

    def _draw_status_bar(self, x, y, w, h):
        """Draw status bar with branding and info."""
        # Background
        self._draw_rect(x, y, w, h, self.COLORS['panel'])

        # Branding - FM-90s in cyan/blue (Neuropol font) - top left
        fm90s_color = (0.2, 0.8, 1.0, 1.0)  # Cyan blue
        self._draw_text_glowing("FM-90s", x + 10, y + 10, fm90s_color, font=self.font_fm90s, glow_strength=0.8)

        # Genesis Engine in Sega red (NiseGenesis font) - centered
        engine_color = (1.0, 0.2, 0.2, 1.0)  # Sega red
        engine_text = "GENESIS ENGINE"
        engine_width = self.font_genesis.size(engine_text)[0]
        engine_x = x + (w - engine_width) // 2
        self._draw_text_glowing(engine_text, engine_x, y + 12, engine_color, font=self.font_genesis, glow_strength=0.8)

        # Status message - below FM-90s on left side
        self._draw_text(self.status_message, x + 10, y + 38, self.COLORS['white'])

        # Filename
        if self.current_file:
            self._draw_text(self.current_file, x + w - 300, y + 10, self.COLORS['dim'])

        # Progress bar
        if self.total_duration > 0:
            progress = min(1.0, self.elapsed_time / self.total_duration)
            bar_width = 200
            bar_height = 8
            bar_x = x + w - 220
            bar_y = y + 30

            # Background
            self._draw_rect(bar_x, bar_y, bar_width, bar_height, (0.2, 0.2, 0.25, 1.0))
            # Progress
            if progress > 0:
                self._draw_rect(bar_x, bar_y, bar_width * progress, bar_height, (0.2, 0.8, 1.0, 1.0))

            # Time
            mins = int(self.elapsed_time) // 60
            secs = int(self.elapsed_time) % 60
            total_mins = int(self.total_duration) // 60
            total_secs = int(self.total_duration) % 60
            time_str = f"{mins}:{secs:02d} / {total_mins}:{total_secs:02d}"
            self._draw_text(time_str, bar_x - 100, bar_y - 2, self.COLORS['dim'], self.font_small)

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
        glViewport(0, 0, self.width, self.height)
        glClear(GL_COLOR_BUFFER_BIT)

        if self.crt_shader is None:
            # Fallback: just copy framebuffer to screen without shader
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

        glUseProgram(self.crt_shader)

        # Set uniforms
        glUniform1f(glGetUniformLocation(self.crt_shader, "time"), pygame.time.get_ticks() / 1000.0)
        glUniform2f(glGetUniformLocation(self.crt_shader, "resolution"), float(self.width), float(self.height))

        # Bind zoomed framebuffer texture
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self.fb_texture2)
        glUniform1i(glGetUniformLocation(self.crt_shader, "screenTexture"), 0)

        # Draw fullscreen quad
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(-1, -1)
        glTexCoord2f(1, 0); glVertex2f(1, -1)
        glTexCoord2f(1, 1); glVertex2f(1, 1)
        glTexCoord2f(0, 1); glVertex2f(-1, 1)
        glEnd()

        glUseProgram(0)

    def run(self, title: str = "Genesis Engine Visualizer", width: int = 1280, height: int = 720):
        """Run the visualizer."""
        self.width = width
        self.height = height

        pygame.init()
        pygame.display.set_caption(title)

        # Use compatibility profile for immediate mode + shaders
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)

        self.screen = pygame.display.set_mode((width, height), DOUBLEBUF | OPENGL)

        self._init_gl()

        clock = pygame.time.Clock()
        self.running = True

        while self.running:
            for event in pygame.event.get():
                if event.type == QUIT:
                    self.running = False
                elif event.type == KEYDOWN:
                    if event.key == K_ESCAPE:
                        self.running = False

            # Pass 1: Render scene to framebuffer
            glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
            glViewport(0, 0, self.width, self.height)
            self._render_scene()

            # Pass 2: Apply zoom/pulse effect (content zooms, scanlines stay fixed)
            self._apply_zoom_shader()

            # Pass 3: Apply CRT shader to screen
            self._apply_crt_shader()

            pygame.display.flip()
            clock.tick(60)

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
