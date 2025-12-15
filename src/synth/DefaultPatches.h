#ifndef GENESIS_DEFAULT_PATCHES_H
#define GENESIS_DEFAULT_PATCHES_H

#include "FMPatch.h"
#include "PSGEnvelope.h"
#include "../config/platform_detect.h"

/**
 * Default FM Patches
 *
 * Classic Genesis-style sounds for immediate use.
 * These are stored in PROGMEM (flash memory) to save RAM.
 *
 * To use a patch, copy it to RAM first:
 *   FMPatch myPatch;
 *   memcpy_P(&myPatch, &defaultFMPatches[0], sizeof(FMPatch));
 *   FMPatch::loadToChannel(board, 0, myPatch);
 *
 * Patches:
 *   0: Bright EP (Electric Piano) - Algorithm 5
 *   1: Synth Bass - Algorithm 0
 *   2: Brass - Algorithm 4
 *   3: Lead Synth - Algorithm 7
 *   4: Organ - Algorithm 7
 *   5: Strings - Algorithm 2
 *   6: Pluck/Guitar - Algorithm 0
 *   7: Bell/Chime - Algorithm 4
 */
#define DEFAULT_FM_PATCH_COUNT 8
extern const FMPatch defaultFMPatches[DEFAULT_FM_PATCH_COUNT] GENESIS_PROGMEM;

/**
 * Default PSG Envelopes
 *
 * Software envelopes for SN76489 channels.
 * Update at 60Hz for proper timing.
 *
 * To use an envelope:
 *   PSGEnvelope myEnv;
 *   memcpy_P(&myEnv, &defaultPSGEnvelopes[0], sizeof(PSGEnvelope));
 *   state.trigger(&myEnv);
 *
 * Envelopes:
 *   0: Short pluck (quick decay, no loop) - ~170ms
 *   1: Sustain (organ-like, loops at full volume)
 *   2: Slow attack pad (fades in, loops sustain)
 *   3: Tremolo (volume wobble, loops)
 */
#define DEFAULT_PSG_ENV_COUNT 4
extern const PSGEnvelope defaultPSGEnvelopes[DEFAULT_PSG_ENV_COUNT] GENESIS_PROGMEM;

#endif // GENESIS_DEFAULT_PATCHES_H
