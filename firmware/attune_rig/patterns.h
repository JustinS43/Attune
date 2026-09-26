/*
 * Light and buzz pattern timings (build plan, "Light and buzz patterns").
 * Mirrored in engine/attune/hardware/simulator.py (PATTERN_STEPS) - keep in step.
 *
 *   T3   LED on the alarm's side 0.5 s on / 0.5 s off x3, then 1.5 s dark; repeats.
 *        Buzz: same rhythm, 0.45 s buzzes.                         Smoke / fire alarm
 *   T4   Four quick 0.1 s flashes, then 5 s dark; four quick ticks. Carbon monoxide
 *   BELL Two 150 ms flashes on that side; two 120 ms buzzes.      Doorbell
 *   NAME Right LED fades in and out once over 1 s; no buzz.        "Sam? Tap to confirm"
 *   OK   One 100 ms blink; one 80 ms tick.                         Tap accepted
 *   NO   No light; two 40 ms ticks.                                Hold accepted
 *   LOST Both LEDs blink dimly every 2 s; no buzz.                 Laptop link lost
 */
#pragma once
#include <stdint.h>

#define LIGHT_FADE 0xFFFF  // step light value meaning "triangle fade 0 -> full -> 0"

struct Step {
  uint16_t ms;      // duration
  uint16_t light;   // 0..255 (scaled by CFG led), or LIGHT_FADE
  uint8_t motor;    // 1 = buzz during this step
};

struct PatternDef {
  const char *name;
  const Step *steps;
  uint8_t count;
  bool repeats;
};

static const Step STEPS_T3[] = {
    {450, 255, 1}, {50, 255, 0}, {500, 0, 0},
    {450, 255, 1}, {50, 255, 0}, {500, 0, 0},
    {450, 255, 1}, {50, 255, 0}, {1500, 0, 0},
};
static const Step STEPS_T4[] = {
    {80, 255, 1}, {20, 255, 0}, {100, 0, 0},
    {80, 255, 1}, {20, 255, 0}, {100, 0, 0},
    {80, 255, 1}, {20, 255, 0}, {100, 0, 0},
    {80, 255, 1}, {20, 255, 0}, {5000, 0, 0},
};
static const Step STEPS_BELL[] = {
    {120, 255, 1}, {30, 255, 0}, {150, 0, 0}, {120, 255, 1}, {30, 255, 0},
};
static const Step STEPS_NAME[] = {{1000, LIGHT_FADE, 0}};
static const Step STEPS_OK[] = {{80, 255, 1}, {20, 255, 0}};
static const Step STEPS_NO[] = {{40, 0, 1}, {80, 0, 0}, {40, 0, 1}};
static const Step STEPS_LOST[] = {{100, 255, 0}, {1900, 0, 0}};  // light scaled to "dim"

#define COUNT_OF(a) (sizeof(a) / sizeof((a)[0]))

static const PatternDef PATTERNS[] = {
    {"T3", STEPS_T3, COUNT_OF(STEPS_T3), true},
    {"T4", STEPS_T4, COUNT_OF(STEPS_T4), true},
    {"BELL", STEPS_BELL, COUNT_OF(STEPS_BELL), false},
    {"NAME", STEPS_NAME, COUNT_OF(STEPS_NAME), false},
    {"OK", STEPS_OK, COUNT_OF(STEPS_OK), false},
    {"NO", STEPS_NO, COUNT_OF(STEPS_NO), false},
};
#define PATTERN_COUNT COUNT_OF(PATTERNS)

static const PatternDef PATTERN_LOST = {"LOST", STEPS_LOST, COUNT_OF(STEPS_LOST), true};
