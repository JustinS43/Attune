/*
 * Pins, driver switches and safety limits for the Attune rig (UNO R4 WiFi).
 * Pins follow the build plan's wiring table / CAD page; see docs/hardware/wiring.md.
 */
#pragma once

// ---- Pins ------------------------------------------------------------------------
#define PIN_SOUND_L A0   // Grove Sound Sensor, left hinge
#define PIN_SOUND_R A1   // Grove Sound Sensor, right hinge
#define PIN_TOUCH 2      // Grove Touch Sensor (TTP223), HIGH while touched
#define PIN_LED_L 5      // Grove LED Socket, left hinge (PWM)
#define PIN_LED_R 6      // Grove LED Socket, right hinge (PWM)
#define PIN_SERVO 9      // backup: servo tapper, only if the motor driver can't run the motor
// Motor driver on I2C: SDA/SCL header pins beside AREF (5 V), NOT the 3.3 V Qwiic socket.
// Motor on driver output M1 (channel A), with the 0.1 uF "104" capacitor across its leads.

// ---- Motor driver ----------------------------------------------------------------
// Which driver libraries to compile in. The board probes I2C at start-up and uses the one
// that answers. Install the matching library in the Arduino IDE Library Manager:
//   TB6612 -> "Grove - Motor Driver TB6612FNG"  (header Grove_Motor_Driver_TB6612FNG.h)
//   L298   -> "Grove I2C Motor Driver v1.3"     (header Grove_I2C_Motor_Driver.h)
// Set a switch to 0 if its library is not installed.
#define USE_TB6612_LIB 1
#define USE_L298_LIB 0
#define USE_SERVO_BACKUP 1   // drive the D9 servo as a tapper when no driver answers

#define TB6612_ADDR 0x14
#define L298_ADDR 0x0F

// Buzz strength at 100 % of a pattern step (lower it if the board resets during a buzz).
#define MOTOR_SPEED_TB6612 200   // of 255
#define MOTOR_SPEED_L298 80      // of 100
#define SERVO_REST_DEG 90
#define SERVO_TAP_DEG 110

// ---- Safety limits (plan: soft start 40 ms, no buzz > 0.6 s, <= 60 % of any 2 s) ------
#define SOFT_START_MS 40
#define MAX_BUZZ_MS 600
#define DUTY_WINDOW_MS 2000
#define DUTY_MAX_MS 1200
#define DUTY_BUCKET_MS 100      // duty is tracked in 20 buckets of 100 ms

// ---- Touch ---------------------------------------------------------------------------
#define TOUCH_DEBOUNCE_MS 20

// ---- LEDs ----------------------------------------------------------------------------
#define LOST_LED_LEVEL 50       // "dim" for the LOST blink, of 255 (then scaled by CFG led)
