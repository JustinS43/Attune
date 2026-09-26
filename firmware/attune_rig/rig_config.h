/*
 * Pins, buzzer choice and safety limits for the Attune rig.
 * Boards: Arduino UNO R3 (the team's rig: SG92R servo tapper on D9, status on LED 13) or
 * Arduino UNO R4 WiFi (DC motor on an I2C driver, status on the LED matrix).
 * Pins follow docs/hardware/wiring.md.
 */
#pragma once
#include "board.h"

// ---- Pins ------------------------------------------------------------------------
#define PIN_SOUND_L A0   // Grove Sound Sensor, left hinge
#define PIN_SOUND_R A1   // Grove Sound Sensor, right hinge
#define PIN_TOUCH 2      // Grove Touch Sensor (TTP223), HIGH while touched
#define PIN_LED_L 5      // Grove LED Socket, left hinge (PWM, Timer0 on the R3)
#define PIN_LED_R 6      // Grove LED Socket, right hinge (PWM, Timer0 on the R3)
#define PIN_SERVO 9      // SG92R servo tapper signal (orange). The Servo library takes Timer1,
                         // so D9/D10 lose analogWrite; nothing else here uses them.
#define PIN_STATUS_LED 13  // built-in LED: status blink codes on boards without the LED matrix

// ---- Buzzer ------------------------------------------------------------------------
// BUZZER_SERVO: the SG92R on D9 taps the temple. No I2C, no motor-driver libraries.
// BUZZER_MOTOR: DC motor on a Grove I2C motor driver, probed at start-up; with
//               USE_SERVO_BACKUP the D9 servo taps instead if no driver answers.
// Default: servo on AVR (UNO R3), motor on the UNO R4 WiFi. Override by defining BUZZER here.
#define BUZZER_SERVO 1
#define BUZZER_MOTOR 2
#ifndef BUZZER
#if RIG_AVR
#define BUZZER BUZZER_SERVO
#else
#define BUZZER BUZZER_MOTOR
#endif
#endif

// ---- Motor driver (BUZZER_MOTOR only) ------------------------------------------------
// Which driver libraries to compile in. The board probes I2C at start-up and uses the one
// that answers. Install the matching library in the Arduino IDE Library Manager:
//   TB6612 -> "Grove - Motor Driver TB6612FNG"  (header Grove_Motor_Driver_TB6612FNG.h)
//   L298   -> "Grove I2C Motor Driver v1.3"     (header Grove_I2C_Motor_Driver.h)
// Set a switch to 0 if its library is not installed.
// Driver on the SDA/SCL header pins beside AREF (5 V), NOT the R4's 3.3 V Qwiic socket.
// Motor on driver output M1 (channel A), with the 0.1 uF "104" capacitor across its leads.
#define USE_TB6612_LIB 1
#define USE_L298_LIB 0
#define USE_SERVO_BACKUP 1   // drive the D9 servo as a tapper when no driver answers

#define TB6612_ADDR 0x14
#define L298_ADDR 0x0F

// Buzz strength at 100 % of a pattern step (lower it if the board resets during a buzz).
#define MOTOR_SPEED_TB6612 200   // of 255
#define MOTOR_SPEED_L298 80      // of 100

// ---- Servo tapper (BUZZER_SERVO, or the motor build's backup) -----------------------
// Each buzzing pattern step becomes taps: the arm swings REST -> TAP, holds TAP for
// SERVO_TAP_OUT_MS, swings back and rests SERVO_TAP_BACK_MS before the next tap may start.
// A step that buzzes for 450 ms (T3) gives 4 taps, an 80 ms tick (T4, OK) gives one.
// A tap that has started always finishes back at REST. The SG92R turns ~60 deg per 100 ms
// at 5 V, so a 20 deg swing needs ~35 ms each way; raise the times for a bigger swing.
#define SERVO_REST_DEG 90
#define SERVO_TAP_DEG 110          // swing towards the temple; bench-tune, never press hard
#define SERVO_TAP_OUT_MS 50        // time at the tap angle (includes the swing out)
#define SERVO_TAP_BACK_MS 70       // time back at rest before the next tap (swing back)
#define SERVO_DETACH_MS 250        // idle this long at rest -> detach: no holding current,
                                   // no pulse jitter, no noise in the sound sensors
#define SERVO_MIN_US 544           // Servo library defaults; narrow them if the arm
#define SERVO_MAX_US 2400          // buzzes against an end stop

// ---- Safety limits (plan: soft start 40 ms, no buzz > 0.6 s, <= 60 % of any 2 s) ------
// For the servo, "buzzing" is time spent in a tap (out + back). A continuous buzz is cut
// at MAX_BUZZ_MS, and no new tap starts once DUTY_MAX_MS of the last 2 s were tapping, so
// the servo can never tap non-stop or sit pushed against something.
#define SOFT_START_MS 40        // DC motor only
#define MAX_BUZZ_MS 600
#define DUTY_WINDOW_MS 2000
#define DUTY_MAX_MS 1200
#define DUTY_BUCKET_MS 100      // duty is tracked in 20 buckets of 100 ms

// ---- Touch ---------------------------------------------------------------------------
#define TOUCH_DEBOUNCE_MS 20

// ---- LEDs ----------------------------------------------------------------------------
#define LOST_LED_LEVEL 50       // "dim" for the LOST blink, of 255 (then scaled by CFG led)
