# Rig wiring and bench-test log

**Owner:** Section 3 - Hardware & Services
**TODO:** H-13 (assembly + bench tests), H-01 - H-05 (firmware and link they exercise), H-15 (UNO R3 + servo)
**Plan:** section 04 "Wiring", "Power", "What the Arduino does"; test IDs T-H1 - T-H8

**3D CAD view of the rig:** [cad-rig.html](cad-rig.html) (open it in a browser: orbit the model, explode view, click parts; bill of materials, pin map, signal flow and assembly steps). Its pin map matches `firmware/attune_rig/rig_config.h`. Needs internet for three.js and fonts.

## Parts

The team's rig is the **UNO R3 + SG92R servo** build. The UNO R4 WiFi + motor-driver build
still works with the same sketch (rows marked "R4 build").

| Part | Qty | Job |
|---|---|---|
| Arduino **UNO R3** (ATmega328P, USB `2341:0043`) — or UNO R4 WiFi | 1 | Reads sensors, plays light/buzz patterns, talks USB serial to the laptop. Status: built-in **LED 13** blink codes on the R3; the 12×8 LED matrix on the R4 WiFi. |
| **SG92R micro servo** | 1 | The buzzer: taps the right temple on D9 (one tap per short tick, 4 per long buzz) |
| 100–470 µF electrolytic capacitor (≥ 6.3 V) | 0–1 | Across the servo's 5 V and GND, only if the board browns out when it taps |
| Grove Base Shield | 1 | Grove ports for the sensors and LEDs |
| Grove Sound Sensor | 2 | Left/right loudness (direction of sounds) |
| Grove Touch Sensor (TTP223) | 1 | Tap = yes, hold = no, double tap = save this person, triple tap = pause |
| Grove LED Socket Kit | 2 | Alert light in the corner of each eye |
| Grove cables, three male–female jumpers for the servo, tape, labels | | |
| *R4 build:* Grove I²C Motor Driver: TB6612FNG (0x14) or V1.3 L298 (0x0F) | 0–1 | Powers a buzz motor instead of the servo |
| *R4 build:* Mini DC motor (two leads) + 0.1 µF "104" capacitor across its leads | 0–1 | Buzz, tape lump off-centre on the shaft; the cap cuts noise into the sound sensors |

## Pins (also in `firmware/attune_rig/rig_config.h`)

| Part | Wire | Arduino pin | Base Shield port | Without a shield |
|---|---|---|---|---|
| Sound Sensor, left | Yellow (SIG) | **A0** | A0 | Yellow to A0 |
| Sound Sensor, right | Yellow (SIG) | **A1** | A1 | Yellow to A1 |
| Touch Sensor | Yellow (SIG) | **D2** | D2 | Yellow to D2 |
| LED Socket, left | Yellow (SIG) | **D5** (PWM) | D5 | Yellow to D5 |
| LED Socket, right | Yellow (SIG) | **D6** (PWM) | D6 | Yellow to D6 |
| SG92R servo (buzzer) | **Orange** = signal, **red** = 5 V, **brown** = GND | **D9** | none (the shield has no D9 Grove port): jumpers to the shield's D9, 5V and GND header pins | Orange to D9, red to 5V, brown to GND |
| Status LED | built in | **D13** ("L" LED) | — | Nothing to wire; the R4 WiFi uses its matrix instead |
| *R4 build:* motor driver, control | Yellow = SCL, white = SDA | **SCL / SDA** (beside AREF, 5 V) | any I²C port | To SCL/SDA beside AREF — **not** the Qwiic socket (3.3 V) |
| *R4 build:* motor driver, power | Screw terminal | 5V, GND | shield 5V/GND | Jumpers from the rails |
| *R4 build:* motor + capacitor | Two leads + cap legs | driver output **M1** (channel A) | — | Clamp the cap in the M1 terminal with the motor leads |

On the R3 the Servo library takes Timer1, so D9 and D10 lose `analogWrite` (PWM); keep
anything that needs PWM off them. The LEDs on D5/D6 use Timer0 and are unaffected.

All Grove red = 5 V, black = GND. Label both ends of every cable ("L sound", "R LED"):
crossed left/right is the most likely wiring bug, and T-H3 catches it.

Power: everything runs from the Arduino's USB 5 V; stay under ~0.5 A.

**Servo power.** The SG92R pulls 250–700 mA peaks each time it starts to move (more if the arm
is blocked), which can sag a laptop's USB 5 V. Take its red wire from the board's **5V** pin
(not 3.3 V, not VIN). If the board resets, the LEDs flicker or the USB link drops when it
taps, fit a **100–470 µF** electrolytic capacitor across the servo's 5 V and GND, close to
the servo plug (stripe / short leg to GND), or power the servo from a separate 5 V supply
with its GND joined to the Arduino's GND. The firmware detaches the servo between taps, so
it draws almost nothing at rest; it never holds the arm against the head. Tune
`SERVO_TAP_DEG` so the arm just touches the temple: pressing in makes it stall and draw the
most current.

*R4 build (motor):* the motor draws ~1 A for a few ms at start (the firmware's 40 ms soft
start keeps it down) and 0.8–2 A if stalled — never let it stall. If the board resets during
a buzz: lower `MOTOR_SPEED_*` in `rig_config.h`, lengthen `SOFT_START_MS`, check the capacitor.

## How to test tomorrow (plug in → link → patterns)

1. **Flash** the firmware (see `firmware/README.md`, "UNO R3 + servo"). Serial Monitor at
   115200 should show `READY 1.1.0 NONE` on the R3 (`TB6612` / `L298` / `NONE` on the R4) and
   `LV …` lines; LED 13 blips every 2 s (the R4's matrix shows `?`); the servo twitches to rest once.
   **Close the Serial Monitor** before starting the engine (only one program can hold the port).
2. **Engine**, from the repo root, with `hardware.simulate = false` (default) and no
   `ATTUNE_SIMULATE_HARDWARE` set. The service finds the board by USB ID (Arduino: UNO R3
   `2341:0043` or `2341:0001`, UNO R4 WiFi `2341:1002`; or CH340/FTDI/CP210x clones); set
   `hardware.port = "COM5"` only if auto-find picks the wrong one. The R3 may reset when the
   port opens; that only adds about a second. Within ~3 s: `hw.link` →
   `{connected: true, firmware: "1.1.0", driver: "NONE"}` (R3 + servo), the console's Arduino
   chip goes green, LED 13 blinks slowly (the R4's matrix shows a pulsing heart).
3. **Levels:** clap near each side; the console/`status.part` hardware metrics `left`/`right` jump.
4. **Patterns from the console** ("test pattern" buttons send command `pattern.test`):
   T3 L, T3 R, T4 B, BELL R, NAME R, OK R, NO R. Then STOP (`hw.stop`). With the servo:
   T3 = 3 bursts of 4 taps, T4 = 4 single taps, BELL = 2 taps, OK = 1, NO = 2 quick, NAME = none.
5. **Touch:** tap / hold / double / triple on the pad → `sensors.touch` events; with a fake alert
   active a tap sends `touch.action {target: alert}`. A double tap sends `touch.action {target: save}`:
   with a name proposal or a named person in view the phone asks them to consent to being saved,
   otherwise the glasses say "Say their name first". A triple tap sends `pause.toggle` and the
   status shows pause (LED 13 double blink; `P` on the R4 matrix). Timing: every tap must start within `tap_ms` of the last one's release; a
   single or double tap is reported `tap_ms` after the last release, a triple at once.
6. **Pull the USB cable:** `hw.link` goes false; the rig stops within 2 s and shows LOST
   (LED 13 blip / `?`) + dim LOST blink (it's unpowered, so only when on a hub/battery). Plug back: link returns by itself.
7. **No rig at all?** `ATTUNE_SIMULATE_HARDWARE=1` (or `hardware.simulate = true`) runs the
   in-process FakeArduino; bus `hw.sim_touch {gesture: "tap"}` fakes a touch (`hold`, `double`,
   `triple` too).

Quick standalone check without the engine (from the repo root, board plugged in):

```bash
PYTHONPATH=engine python -c "from attune.hardware.serial_link import find_port; print(find_port())"
```

## Bench tests (T-H1 – T-H8) — log results here

| ID | Test | Pass when | Result | Date / who |
|---|---|---|---|---|
| T-H1 | Board up: `READY` at power-up (R4 build: the I²C driver check) | R3 + servo: `READY 1.1.0 NONE` and the servo parks at rest. R4: TB6612 (0x14), or L298 (0x0F → V1.3 column: try 5 V, else servo on D9) | [ ] | |
| T-H2 | Connections: multimeter continuity on every pins-in-plug joint, then wiggle each cable while watching the readings | No dropouts or jumps while wiggling | [ ] | |
| T-H3 | Sound sensors: quiet room, speech at 1 m, a clap, then the JBL at 90° left and right, 20 plays | Louder side correct in ≥ 18/20 plays by ≥ 3 dB; left sensor really on A0 | [ ] | |
| T-H4 | Touch: 20 taps, 10 holds, 10 double taps, 10 triple taps through the tape; then 10 min untouched with the motor buzzing | ≥ 95 % classified right; no phantom touches | [ ] | |
| T-H5 | LEDs: every pattern, worn, in a bright room | Noticeable at the edge of vision without dazzling; `led` brightness set (CFG / `hardware.led_brightness`) | [ ] | |
| T-H6 | Buzzer and power: 50 T3 cycles; current with the multimeter in series if possible | Taps clearly felt, arm never presses in or buzzes against a stop; no Arduino resets (else add the 100–470 µF cap); current within budget; stops within 2 s when the laptop link is cut | [ ] | |
| T-H7 | Buzzer noise: sensor levels with the servo (or motor) idle, then tapping | The jump is small, and flagged (`motor_on`) readings are ignored; idle levels are quiet once the servo detaches | [ ] | |
| T-H8 | Wear test: 15 min worn, walking, turning your head, sitting | Nothing pulls loose; camera stays level; comfortable for three demos | [ ] | |

Notes / tuning values found on the bench:

- tap_ms = , hold_ms = , led = , SERVO_REST_DEG = , SERVO_TAP_DEG = , SERVO_TAP_OUT_MS = , SERVO_TAP_BACK_MS =
- R4 build only: MOTOR_SPEED = , SOFT_START_MS =
