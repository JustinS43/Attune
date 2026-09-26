# Firmware for the Attune rig (Arduino UNO R3 or UNO R4 WiFi)

**Owner:** Section 3 - Hardware & Services
**TODO:** H-01 - H-04, H-15 (UNO R3 + servo)
**Plan:** section 04 "What the Arduino does", "Light and buzz patterns", "Messages over USB"

The sketch measures, reports and plays patterns with exact timing. It makes no decisions
about people or sounds; the laptop does. One sketch builds for both boards; the serial
protocol is the same on both.

| | **UNO R3** (the team's rig) | UNO R4 WiFi |
|---|---|---|
| FQBN | `arduino:avr:uno` | `arduino:renesas_uno:unor4wifi` |
| Buzzer | SG92R micro servo tapper on D9 (`BUZZER_SERVO`, the default on AVR) | DC motor on an I²C driver (`BUZZER_MOTOR`), D9 servo as backup |
| Status | Built-in LED 13 blink codes | 12×8 LED matrix icons |
| `READY` driver | `NONE` (the servo is not an I²C driver) | `TB6612`, `L298`, or `NONE` when the servo backup taps |
| USB ID | `2341:0043` (or `2341:0001`), 16U2 USB chip | `2341:1002` |

| File | What it holds |
|---|---|
| `attune_rig/attune_rig.ino` | The sketch: sensors, touch gestures, patterns, buzzer (motor or servo taps) and its safety budget, link watchdog, status (matrix or LED 13) |
| `attune_rig/protocol.h` | Serial message names, CFG keys and defaults (mirror of `engine/attune/hardware/protocol.py`) |
| `attune_rig/patterns.h` | T3, T4, BELL, NAME, OK, NO, LOST step tables in flash (mirror of `engine/attune/hardware/simulator.py`) |
| `attune_rig/rig_config.h` | Pins, buzzer choice (`BUZZER`), driver libraries, servo tap angles and times, safety limits |
| `attune_rig/board.h` | R3 / R4 differences: which board, matrix or LED 13, flash (`PROGMEM`) read helpers |

> **Compile status.** The UNO R3 build compiles cleanly (`arduino-cli` 1.5.1, `arduino:avr`
> 1.8.8, Servo 1.3.0, `--warnings all`): **8,586 bytes flash (26 %), 434 bytes RAM (21 %)**,
> leaving 1,614 bytes for the stack. It has not run on a board yet. The UNO R4 WiFi build was
> not compiled after the R3 changes (its core was not installed), and the motor-driver
> library calls have never been compiled; on the bench, compile first and fix any typo there
> (library API names are the most likely spot, see below).

## UNO R3 + servo

The rig's board: ATmega328P with 2 KB RAM and 32 KB flash, so the sketch keeps all text and
tables in flash (`F()`, `PROGMEM`), uses no `String` and no `printf`, and leaves out `Wire`
and the motor-driver libraries.

1. Arduino IDE 2 → Boards Manager → install **Arduino AVR Boards** (core `arduino:avr`).
2. Library Manager → install **Servo** (by Arduino) if it is not already there.
3. Open `firmware/attune_rig/attune_rig.ino`, board **Arduino Uno**, pick the port, Upload.

With arduino-cli (the port is whatever `arduino-cli board list` shows, e.g. `COM5`):

```bash
arduino-cli core install arduino:avr
arduino-cli lib install Servo
arduino-cli compile --fqbn arduino:avr:uno firmware/attune_rig
arduino-cli upload  --fqbn arduino:avr:uno -p COM5 firmware/attune_rig
```

The compile prints flash and RAM use ("Global variables use … bytes"); keep global RAM
under ~1.5 KB so the stack has room.

**Servo "buzz".** Each buzzing pattern step becomes taps: the arm swings from
`SERVO_REST_DEG` (90) to `SERVO_TAP_DEG` (110), stays `SERVO_TAP_OUT_MS` (50 ms), swings back
and rests `SERVO_TAP_BACK_MS` (70 ms) before the next tap may start. So T3's 0.45 s buzzes are
4 taps each, T4 and OK one tap per tick, BELL two taps, NO two quick taps. A started tap
always finishes back at rest; `STOP` and link loss send the arm back at once. The servo is
attached only while tapping and detached `SERVO_DETACH_MS` (250 ms) after returning to rest,
so it holds no current, doesn't jitter and doesn't hum into the sound sensors. `LV`'s motor
flag is 1 while the servo is attached. Tune the angles and times in `rig_config.h` on the
bench: the arm should touch the temple, never press into it.

**Status on LED 13** (the R3 has no matrix; `MX` is accepted and handled as before):

| `MX` icon | LED 13 |
|---|---|
| `HEART` (linked) | slow blink, 0.5 s on / 0.5 s off |
| `ALERT_L` / `ALERT_R` / `ALERT_B` | fast blink, 0.1 s on / 0.1 s off |
| `PAUSE` | double blink every 1.5 s |
| `LOST` (link lost, also at power-up) | short 50 ms blip every 2 s |

**Reset on connect.** The R3's USB chip resets the board when DTR changes. The laptop opens
the port with DTR low, but if the board resets anyway it boots in about a second and prints
`READY`, and prints `READY` again on the laptop's first `HB`; the link comes up either way.

**Servo power.** The SG92R draws 250–700 mA peaks when it starts moving. If the board resets
or the USB port drops when it taps, add a 100–470 µF capacitor across the servo's 5 V and GND
(see `docs/hardware/wiring.md`).

## UNO R4 WiFi + motor driver

1. Arduino IDE 2 → Boards Manager → install **Arduino UNO R4 Boards** (core `arduino:renesas_uno`).
   `Arduino_LED_Matrix`, `Wire` and `Servo` come with it.
2. Library Manager → install the library for the driver on your board
   (T-H1 tells you which; look at the chip):
   - **Grove - Motor Driver TB6612FNG** (header `Grove_Motor_Driver_TB6612FNG.h`, I²C `0x14`) — default.
   - **Grove I2C Motor Driver v1.3** (header `Grove_I2C_Motor_Driver.h`, L298, I²C `0x0F`) —
     then set `USE_L298_LIB 1` (and `USE_TB6612_LIB 0` if that library isn't installed) in `rig_config.h`.
3. Open `firmware/attune_rig/attune_rig.ino`, board **Arduino UNO R4 WiFi**, pick the port, Upload.

With arduino-cli:

```bash
arduino-cli core install arduino:renesas_uno
arduino-cli lib install "Grove - Motor Driver TB6612FNG"
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi firmware/attune_rig
arduino-cli upload  --fqbn arduino:renesas_uno:unor4wifi -p COM5 firmware/attune_rig
```

To use the servo on the R4 instead of a motor, add `#define BUZZER BUZZER_SERVO` near the
top of `rig_config.h`.

If the compile complains about a driver-library call, the four calls used are
`MotorDriver::init(addr)`, `notStandby()`, `dcMotorRun(MOTOR_CHA, speed)`,
`dcMotorStop(MOTOR_CHA)` (TB6612) and `Motor.begin(addr)`, `Motor.speed(MOTOR1, v)`,
`Motor.stop(MOTOR1)` (L298). Match them to the installed library's example sketch.

## Behaviour

- **Start-up:** servo build (R3): parks the servo at rest, prints `READY 1.1.0 NONE`. Motor
  build (R4): probes I²C (`0x14` → TB6612, `0x0F` → L298, else NONE; with `USE_SERVO_BACKUP`
  the D9 servo becomes the tapper), prints `READY 1.1.0 <driver>`. Shows LOST (`?` on the
  matrix, a blip on LED 13) until the laptop's first `HB`.
- **Link:** every laptop `HB` refreshes a 2 s watchdog. On the first `HB` after being
  unlinked the board prints `READY` again: the laptop opens the port without toggling DTR,
  so the board usually does not reset and the boot `READY` may be long gone.
  If the watchdog fires: all lights and the buzzer stop, status shows LOST, both LEDs blink
  dimly every 2 s (LOST) until `HB` returns.
- **Levels:** both sound sensors are sampled every loop (thousands of times a second); every
  `rate` ms (default 50) the board prints `LV <ms> <left> <right> <motor>` with the
  peak-to-peak of each side (0–1023). `motor` is 1 if the motor ran (or the servo was
  attached) at any time in that window.
- **Touch:** tap (< `tap_ms`, default 400), hold (≥ `hold_ms`, default 800, sent while still
  held), double (second tap starts within `tap_ms` of the first), triple (a third tap within
  `tap_ms` of the second; sent at once on its release). A single or double tap is sent `tap_ms`
  after the last release, once no further tap came. 20 ms debounce. The laptop reads tap = yes,
  hold = no, double = save this person, triple = pause (see the touch router).
- **Patterns:** `PAT n side name` → `ACK n`. T3/T4 repeat until `STOP`; BELL/NAME/OK/NO play
  once over a running alarm pattern, which then continues. `STOP n` → `ACK n`.
- **Buzzer safety:** no single buzz longer than 0.6 s, never more than 1.2 s of buzzing in
  any 2 s window (tracked in 100 ms buckets). Motor: 40 ms soft start (shorter for very
  short ticks). Servo: tapping time counts as buzzing; no new tap starts once the budget is
  spent, a started tap always returns to rest, and the servo is detached when idle.
- **Status (`MX`, no `ACK`):** matrix: `HEART` (pulsing heart), `ALERT_L`/`ALERT_R` (`!` +
  arrow), `ALERT_B` (`!`, side unknown — an addition to the contract's list), `PAUSE` (`P`),
  `LOST` (`?`). LED 13: see the table above. Unknown icon → `ERR bad icon`.
- **CFG:** `rate` 10–1000, `tap_ms` 100–1000, `hold_ms` 300–3000, `led` 0–255; out of range → `ERR`.
- **Errors:** `ERR <n> bad side …`, `ERR <n> unknown pattern …`, `ERR unknown <cmd>`,
  `ERR line too long`, `ERR driver found but its library is disabled in rig_config.h`.

Test by hand from the Arduino IDE Serial Monitor (115200, newline): type `HB`, then
`PAT 1 L T3`, `STOP 2`, `MX PAUSE`, `CFG led 60` — and stop typing `HB` for 2 s to see LOST.
