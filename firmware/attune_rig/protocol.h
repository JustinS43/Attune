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

#define FW_VERSION "1.0.0"
#define SERIAL_BAUD 115200
#define LINE_MAX 64

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

// Pattern names accepted by PAT ("LOST" is played by the board itself)
static const char *const PATTERN_NAMES[] = {"T3", "T4", "BELL", "NAME", "OK", "NO"};
#define PATTERN_NAME_COUNT 6

// Matrix icons accepted by MX. "ALERT_B" (alert, side unknown) is an addition to the
// contract's list, used by the laptop when an alert has side "none".
static const char *const ICON_NAMES[] = {"HEART", "ALERT_L", "ALERT_R", "ALERT_B", "PAUSE", "LOST"};
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
