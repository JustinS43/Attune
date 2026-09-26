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
 *
 * "Buzz" is the DC motor, or on the servo tapper one tap per SERVO_TAP_PERIOD while the step
 * buzzes (see rig_config.h). All tables live in flash (RIG_PROGMEM); read them with
 * RIG_READ_* / RIG_MEMCPY_P, never by plain dereference, or AVR reads RAM garbage.
 */
#pragma once
#include <stdint.h>
#include "board.h"
#include "protocol.h"

#define LIGHT_FADE 0xFFFF  // step light value meaning "triangle fade 0 -> full -> 0"

struct Step {
  uint16_t ms;      // duration
  uint16_t light;   // 0..255 (scaled by CFG led), or LIGHT_FADE
  uint8_t motor;    // 1 = buzz during this step
};

struct PatternDef {
  const char *name;   // flash string
  const Step *steps;  // flash table
  uint8_t count;
  uint8_t repeats;    // 1 = loops until STOP
};

static const Step STEPS_T3[] RIG_PROGMEM = {
    {450, 255, 1}, {50, 255, 0}, {500, 0, 0},
    {450, 255, 1}, {50, 255, 0}, {500, 0, 0},
    {450, 255, 1}, {50, 255, 0}, {1500, 0, 0},
};
static const Step STEPS_T4[] RIG_PROGMEM = {
    {80, 255, 1}, {20, 255, 0}, {100, 0, 0},
    {80, 255, 1}, {20, 255, 0}, {100, 0, 0},
    {80, 255, 1}, {20, 255, 0}, {100, 0, 0},
    {80, 255, 1}, {20, 255, 0}, {5000, 0, 0},
};
static const Step STEPS_BELL[] RIG_PROGMEM = {
    {120, 255, 1}, {30, 255, 0}, {150, 0, 0}, {120, 255, 1}, {30, 255, 0},
};
static const Step STEPS_NAME[] RIG_PROGMEM = {{1000, LIGHT_FADE, 0}};
static const Step STEPS_OK[] RIG_PROGMEM = {{80, 255, 1}, {20, 255, 0}};
static const Step STEPS_NO[] RIG_PROGMEM = {{40, 0, 1}, {80, 0, 0}, {40, 0, 1}};
static const Step STEPS_LOST[] RIG_PROGMEM = {{100, 255, 0}, {1900, 0, 0}};  // light scaled to "dim"

#define COUNT_OF(a) (sizeof(a) / sizeof((a)[0]))

static const char PN_LOST[] RIG_PROGMEM = "LOST";

// The first PATTERN_COUNT entries are the ones PAT accepts; LOST comes last.
static const PatternDef PATTERNS[] RIG_PROGMEM = {
    {PN_T3, STEPS_T3, COUNT_OF(STEPS_T3), 1},
    {PN_T4, STEPS_T4, COUNT_OF(STEPS_T4), 1},
    {PN_BELL, STEPS_BELL, COUNT_OF(STEPS_BELL), 0},
    {PN_NAME, STEPS_NAME, COUNT_OF(STEPS_NAME), 0},
    {PN_OK, STEPS_OK, COUNT_OF(STEPS_OK), 0},
    {PN_NO, STEPS_NO, COUNT_OF(STEPS_NO), 0},
    {PN_LOST, STEPS_LOST, COUNT_OF(STEPS_LOST), 1},
};
#define PATTERN_COUNT (COUNT_OF(PATTERNS) - 1)
#define PATTERN_LOST PATTERN_COUNT   // index of LOST in PATTERNS
