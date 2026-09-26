/*
 * Serial message names shared with engine/attune/hardware/protocol.py
 * (docs/contracts.md section 6). Keep the two files in step; the Python test
 * tests/hardware_services/test_protocol.py checks that every name appears here.
 *
 * Plain text lines, 115200 baud, '\n' endings.
 *   Arduino -> laptop: READY <version> <driver> | LV <ms> <left> <right> <motor> |
 *                      TOUCH TAP|HOLD|DOUBLE|TRIPLE | HB <ms> | ACK <n> | ERR <text>
 *   laptop -> Arduino: PAT <n> <L/R/B> <name> | STOP <n> | HB | MX <icon> | CFG <key> <value>
 */
#pragma once
#include "board.h"

#define FW_VERSION "1.1.0"   // 1.1.0: UNO R3 + servo tapper support; protocol unchanged
#define SERIAL_BAUD 115200
#define LINE_MAX 64   // longest laptop line is ~20 chars ("PAT 999999 B BELL")

// Arduino -> laptop
#define MSG_READY "READY"
#define MSG_LV "LV"
#define MSG_TOUCH "TOUCH"
#define MSG_HB "HB"
#define MSG_ACK "ACK"
#define MSG_ERR "ERR"

// laptop -> Arduino
#define CMD_PAT "PAT"
#define CMD_STOP "STOP"
#define CMD_HB "HB"
#define CMD_MX "MX"
#define CMD_CFG "CFG"

// Gestures
#define GESTURE_TAP "TAP"
#define GESTURE_HOLD "HOLD"
#define GESTURE_DOUBLE "DOUBLE"
#define GESTURE_TRIPLE "TRIPLE"

// Drivers reported in READY
#define DRIVER_TB6612 "TB6612"
#define DRIVER_L298 "L298"
#define DRIVER_NONE "NONE"

// Pattern names accepted by PAT ("LOST" is played by the board itself). Kept in flash on
// AVR; patterns.h builds its table from these.
static const char PN_T3[] RIG_PROGMEM = "T3";
static const char PN_T4[] RIG_PROGMEM = "T4";
static const char PN_BELL[] RIG_PROGMEM = "BELL";
static const char PN_NAME[] RIG_PROGMEM = "NAME";
static const char PN_OK[] RIG_PROGMEM = "OK";
static const char PN_NO[] RIG_PROGMEM = "NO";
#define PATTERN_NAME_COUNT 6

// Status icons accepted by MX: the LED matrix on the UNO R4 WiFi, LED 13 blink codes on the
// UNO R3. "ALERT_B" (alert, side unknown) is an addition to the contract's list, used by the
// laptop when an alert has side "none".
static const char IN_HEART[] RIG_PROGMEM = "HEART";
static const char IN_ALERT_L[] RIG_PROGMEM = "ALERT_L";
static const char IN_ALERT_R[] RIG_PROGMEM = "ALERT_R";
static const char IN_ALERT_B[] RIG_PROGMEM = "ALERT_B";
static const char IN_PAUSE[] RIG_PROGMEM = "PAUSE";
static const char IN_LOST[] RIG_PROGMEM = "LOST";
static const char *const ICON_NAMES[] RIG_PROGMEM = {IN_HEART, IN_ALERT_L, IN_ALERT_R,
                                                     IN_ALERT_B, IN_PAUSE, IN_LOST};
#define ICON_COUNT 6
enum Icon { ICON_HEART = 0, ICON_ALERT_L, ICON_ALERT_R, ICON_ALERT_B, ICON_PAUSE, ICON_LOST };

// CFG keys and their defaults / limits
#define CFG_RATE "rate"        // LV report interval, ms
#define CFG_TAP_MS "tap_ms"    // a touch shorter than this is a tap; also the gap allowed between taps
#define CFG_HOLD_MS "hold_ms"  // a touch this long is a hold
#define CFG_LED "led"          // LED brightness 0..255
#define DEFAULT_RATE_MS 50
#define DEFAULT_TAP_MS 400
#define DEFAULT_HOLD_MS 800
#define DEFAULT_LED 180

// Timing
#define HB_OUT_MS 1000       // board heartbeat to the laptop
#define HB_TIMEOUT_MS 2000   // laptop silent this long -> stop everything, show LOST
