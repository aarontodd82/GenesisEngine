#ifndef DEFAULT_PATCHES_H
#define DEFAULT_PATCHES_H

#include <avr/pgmspace.h>
#include "FMPatch.h"
#include "PSGEnvelope.h"

// =============================================================================
// Default FM Patches
// =============================================================================
// Classic Genesis-style sounds to get started
// These can be replaced via SysEx at runtime

#define DEFAULT_FM_PATCH_COUNT 8

const FMPatch defaultFMPatches[DEFAULT_FM_PATCH_COUNT] PROGMEM = {
    // Patch 0: Bright EP (Electric Piano)
    // Algorithm 5, good for keys
    {
        .algorithm = 5,
        .feedback = 6,
        .op = {
            // S1 (modulator)
            {.mul = 1, .dt = 3, .tl = 35, .rs = 1, .ar = 31, .dr = 12, .sr = 0, .rr = 6, .sl = 2, .ssg = 0},
            // S3 (carrier)
            {.mul = 1, .dt = 3, .tl = 25, .rs = 1, .ar = 31, .dr = 8, .sr = 2, .rr = 7, .sl = 2, .ssg = 0},
            // S2 (carrier)
            {.mul = 2, .dt = 3, .tl = 28, .rs = 1, .ar = 31, .dr = 10, .sr = 2, .rr = 7, .sl = 3, .ssg = 0},
            // S4 (carrier)
            {.mul = 1, .dt = 3, .tl = 20, .rs = 1, .ar = 31, .dr = 10, .sr = 2, .rr = 8, .sl = 2, .ssg = 0},
        }
    },

    // Patch 1: Synth Bass
    // Algorithm 0, punchy bass
    {
        .algorithm = 0,
        .feedback = 5,
        .op = {
            {.mul = 0, .dt = 3, .tl = 25, .rs = 0, .ar = 31, .dr = 8, .sr = 0, .rr = 5, .sl = 1, .ssg = 0},
            {.mul = 1, .dt = 3, .tl = 30, .rs = 0, .ar = 31, .dr = 10, .sr = 0, .rr = 5, .sl = 2, .ssg = 0},
            {.mul = 0, .dt = 3, .tl = 20, .rs = 0, .ar = 31, .dr = 6, .sr = 0, .rr = 5, .sl = 1, .ssg = 0},
            {.mul = 1, .dt = 3, .tl = 15, .rs = 0, .ar = 31, .dr = 12, .sr = 2, .rr = 7, .sl = 3, .ssg = 0},
        }
    },

    // Patch 2: Brass
    // Algorithm 4, warm brass
    {
        .algorithm = 4,
        .feedback = 4,
        .op = {
            {.mul = 1, .dt = 3, .tl = 40, .rs = 1, .ar = 25, .dr = 5, .sr = 0, .rr = 4, .sl = 1, .ssg = 0},
            {.mul = 1, .dt = 3, .tl = 20, .rs = 1, .ar = 28, .dr = 6, .sr = 1, .rr = 5, .sl = 2, .ssg = 0},
            {.mul = 2, .dt = 4, .tl = 35, .rs = 1, .ar = 25, .dr = 5, .sr = 0, .rr = 4, .sl = 1, .ssg = 0},
            {.mul = 1, .dt = 2, .tl = 18, .rs = 1, .ar = 28, .dr = 6, .sr = 1, .rr = 5, .sl = 2, .ssg = 0},
        }
    },

    // Patch 3: Lead Synth
    // Algorithm 7, all carriers for thick sound
    {
        .algorithm = 7,
        .feedback = 0,
        .op = {
            {.mul = 1, .dt = 3, .tl = 28, .rs = 2, .ar = 31, .dr = 8, .sr = 0, .rr = 6, .sl = 2, .ssg = 0},
            {.mul = 2, .dt = 4, .tl = 30, .rs = 2, .ar = 31, .dr = 10, .sr = 0, .rr = 6, .sl = 3, .ssg = 0},
            {.mul = 4, .dt = 2, .tl = 35, .rs = 2, .ar = 31, .dr = 12, .sr = 0, .rr = 6, .sl = 4, .ssg = 0},
            {.mul = 1, .dt = 3, .tl = 25, .rs = 2, .ar = 31, .dr = 8, .sr = 0, .rr = 6, .sl = 2, .ssg = 0},
        }
    },

    // Patch 4: Organ
    // Algorithm 7, sine-like with different harmonics
    {
        .algorithm = 7,
        .feedback = 0,
        .op = {
            {.mul = 1, .dt = 3, .tl = 25, .rs = 0, .ar = 31, .dr = 0, .sr = 0, .rr = 8, .sl = 0, .ssg = 0},
            {.mul = 2, .dt = 3, .tl = 30, .rs = 0, .ar = 31, .dr = 0, .sr = 0, .rr = 8, .sl = 0, .ssg = 0},
            {.mul = 4, .dt = 3, .tl = 35, .rs = 0, .ar = 31, .dr = 0, .sr = 0, .rr = 8, .sl = 0, .ssg = 0},
            {.mul = 8, .dt = 3, .tl = 40, .rs = 0, .ar = 31, .dr = 0, .sr = 0, .rr = 8, .sl = 0, .ssg = 0},
        }
    },

    // Patch 5: Strings
    // Algorithm 2, slow attack pad
    {
        .algorithm = 2,
        .feedback = 3,
        .op = {
            {.mul = 1, .dt = 3, .tl = 35, .rs = 0, .ar = 18, .dr = 4, .sr = 0, .rr = 4, .sl = 1, .ssg = 0},
            {.mul = 2, .dt = 4, .tl = 40, .rs = 0, .ar = 20, .dr = 5, .sr = 0, .rr = 4, .sl = 2, .ssg = 0},
            {.mul = 3, .dt = 2, .tl = 45, .rs = 0, .ar = 22, .dr = 6, .sr = 0, .rr = 4, .sl = 2, .ssg = 0},
            {.mul = 1, .dt = 3, .tl = 22, .rs = 0, .ar = 16, .dr = 6, .sr = 1, .rr = 5, .sl = 2, .ssg = 0},
        }
    },

    // Patch 6: Pluck/Guitar
    // Algorithm 0, quick decay
    {
        .algorithm = 0,
        .feedback = 6,
        .op = {
            {.mul = 1, .dt = 3, .tl = 28, .rs = 2, .ar = 31, .dr = 15, .sr = 5, .rr = 8, .sl = 5, .ssg = 0},
            {.mul = 3, .dt = 3, .tl = 35, .rs = 2, .ar = 31, .dr = 18, .sr = 6, .rr = 8, .sl = 6, .ssg = 0},
            {.mul = 1, .dt = 4, .tl = 30, .rs = 2, .ar = 31, .dr = 16, .sr = 5, .rr = 8, .sl = 5, .ssg = 0},
            {.mul = 1, .dt = 3, .tl = 18, .rs = 2, .ar = 31, .dr = 14, .sr = 4, .rr = 9, .sl = 4, .ssg = 0},
        }
    },

    // Patch 7: Bell/Chime
    // Algorithm 4, metallic harmonics
    {
        .algorithm = 4,
        .feedback = 3,
        .op = {
            {.mul = 1, .dt = 3, .tl = 30, .rs = 2, .ar = 31, .dr = 6, .sr = 2, .rr = 5, .sl = 3, .ssg = 0},
            {.mul = 1, .dt = 3, .tl = 22, .rs = 2, .ar = 31, .dr = 8, .sr = 2, .rr = 6, .sl = 3, .ssg = 0},
            {.mul = 7, .dt = 6, .tl = 45, .rs = 2, .ar = 31, .dr = 10, .sr = 3, .rr = 6, .sl = 5, .ssg = 0},
            {.mul = 3, .dt = 0, .tl = 25, .rs = 2, .ar = 31, .dr = 9, .sr = 2, .rr = 7, .sl = 4, .ssg = 0},
        }
    },
};

// =============================================================================
// Default PSG Envelopes
// =============================================================================
// Software envelopes for the SN76489 (no hardware envelope support)

#define DEFAULT_PSG_ENV_COUNT 4

const PSGEnvelope defaultPSGEnvelopes[DEFAULT_PSG_ENV_COUNT] PROGMEM = {
    // Envelope 0: Short pluck (quick decay)
    {
        .data = {0x00, 0x01, 0x02, 0x04, 0x06, 0x08, 0x0A, 0x0C, 0x0E, 0x0F},
        .length = 10,
        .loopStart = 0xFF  // No loop
    },

    // Envelope 1: Sustain (organ-like)
    {
        .data = {0x00, 0x00, 0x00, 0x00},
        .length = 4,
        .loopStart = 0  // Loop from start (sustain)
    },

    // Envelope 2: Slow attack pad
    {
        .data = {0x0F, 0x0C, 0x0A, 0x08, 0x06, 0x04, 0x02, 0x01, 0x00, 0x00, 0x00, 0x00},
        .length = 12,
        .loopStart = 8  // Loop the sustain portion
    },

    // Envelope 3: Tremolo
    {
        .data = {0x00, 0x02, 0x04, 0x02, 0x00, 0x02, 0x04, 0x02},
        .length = 8,
        .loopStart = 0  // Loop for continuous tremolo
    },
};

#endif // DEFAULT_PATCHES_H
