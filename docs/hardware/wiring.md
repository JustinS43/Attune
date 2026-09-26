# Rig wiring and bench-test log

**Owner:** Section 3 - Hardware & Services
**TODO:** H-13 (assembly + bench tests), H-01 - H-05 (firmware and link they exercise)
**Plan:** section 04 "Wiring", "Power", "What the Arduino does"; test IDs T-H1 - T-H8

**3D CAD view of the rig:** [cad-rig.html](cad-rig.html) (open it in a browser: orbit the model, explode view, click parts; bill of materials, pin map, signal flow and assembly steps). Its pin map matches `firmware/attune_rig/rig_config.h`. Needs internet for three.js and fonts.

## Parts

| Part | Qty | Job |
|---|---|---|
| Arduino UNO R4 WiFi | 1 | Reads sensors, plays light/buzz patterns, talks USB serial to the laptop. Built-in 12×8 LED matrix shows status. |
| Grove Sound Sensor | 2 | Left/right loudness (direction of sounds) |
| Grove Touch Sensor (TTP223) | 1 | Tap = yes, hold = no, double tap = pause |
| Grove LED Socket Kit | 2 | Alert light in the corner of each eye |
| Grove I²C Motor Driver: TB6612FNG (0x14) or V1.3 L298 (0x0F) | 1 | Powers the buzz motor |
| Mini DC motor (two leads) | 1 | Buzz, right temple tip, tape lump off-centre on the shaft |
| 0.1 µF ceramic capacitor ("104") | 1 | Across the motor leads, cuts noise into the sound sensors |
| Servo (backup) | 0–1 | Tapper on D9 only if the driver can't run the motor |
| Grove Base Shield (if the lab has one), Grove cables, tape, labels | | |

## Pins (also in `firmware/attune_rig/rig_config.h`)

| Part | Wire | Arduino pin | Base Shield port | Without a shield |
|---|---|---|---|---|
| Sound Sensor, left | Yellow (SIG) | **A0** | A0 | Yellow to A0 |
| Sound Sensor, right | Yellow (SIG) | **A1** | A1 | Yellow to A1 |
| Touch Sensor | Yellow (SIG) | **D2** | D2 | Yellow to D2 |
| LED Socket, left | Yellow (SIG) | **D5** (PWM) | D5 | Yellow to D5 |
| LED Socket, right | Yellow (SIG) | **D6** (PWM) | D6 | Yellow to D6 |
| Motor driver, control | Yellow = SCL, white = SDA | **SCL / SDA** (beside AREF, 5 V) | any I²C port | To SCL/SDA beside AREF — **not** the Qwiic socket (3.3 V) |
| Motor driver, power | Screw terminal | 5V, GND | shield 5V/GND | Jumpers from the rails |
| Motor + capacitor | Two leads + cap legs | driver output **M1** (channel A) | — | Clamp the cap in the M1 terminal with the motor leads |
| Backup servo | Signal / 5 V / GND | **D9** | (no D9 port) | Signal to D9, power to the rails |

All Grove red = 5 V, black = GND. Label both ends of every cable ("L sound", "R LED"):
crossed left/right is the most likely wiring bug, and T-H3 catches it.

Power: everything runs from the Arduino's USB 5 V; stay under ~0.5 A. The motor draws ~1 A for
a few ms at start (the firmware's 40 ms soft start keeps it down) and 0.8–2 A if stalled —
never let it stall. If the board resets during a buzz: lower `MOTOR_SPEED_*` in
`rig_config.h`, lengthen `SOFT_START_MS`, check the capacitor.

## How to test tomorrow (plug in → link → patterns)

1. **Flash** the firmware (see `firmware/README.md`). Serial Monitor at 115200 should show
   `READY 1.0.0 TB6612` (or `L298` / `NONE`) and `LV …` lines; matrix shows `?`.
   **Close the Serial Monitor** before starting the engine (only one program can hold the port).
2. **Engine**, from the repo root, with `hardware.simulate = false` (default) and no
   `ATTUNE_SIMULATE_HARDWARE` set. The service finds the board by USB ID (Arduino `2341:1002`,
   or CH340/FTDI/CP210x clones); set `hardware.port = "COM5"` only if auto-find picks the wrong one.
   Within ~3 s: `hw.link` → `{connected: true, firmware: "1.0.0", driver: "TB6612"}`, the console's
   Arduino chip goes green, the matrix shows a pulsing heart.
3. **Levels:** clap near each side; the console/`status.part` hardware metrics `left`/`right` jump.
4. **Patterns from the console** ("test pattern" buttons send command `pattern.test`):
   T3 L, T3 R, T4 B, BELL R, NAME R, OK R, NO R. Then STOP (`hw.stop`).
5. **Touch:** tap / hold / double on the pad → `sensors.touch` events; with a fake alert active a
   tap sends `touch.action {target: alert}`; double tap sends `pause.toggle` and the matrix shows `P`.
6. **Pull the USB cable:** `hw.link` goes false; the rig stops within 2 s and shows `?` + dim
   LOST blink (it's unpowered, so only when on a hub/battery). Plug back: link returns by itself.
7. **No rig at all?** `ATTUNE_SIMULATE_HARDWARE=1` (or `hardware.simulate = true`) runs the
   in-process FakeArduino; bus `hw.sim_touch {gesture: "tap"}` fakes a touch.

Quick standalone check without the engine (from the repo root, board plugged in):

```bash
PYTHONPATH=engine python -c "from attune.hardware.serial_link import find_port; print(find_port())"
```

## Bench tests (T-H1 – T-H8) — log results here

| ID | Test | Pass when | Result | Date / who |
|---|---|---|---|---|
| T-H1 | Driver found: the I²C check at power-up | `READY` reports TB6612 (0x14), or L298 (0x0F → V1.3 column: try 5 V, else servo on D9) | [ ] | |
| T-H2 | Connections: multimeter continuity on every pins-in-plug joint, then wiggle each cable while watching the readings | No dropouts or jumps while wiggling | [ ] | |
| T-H3 | Sound sensors: quiet room, speech at 1 m, a clap, then the JBL at 90° left and right, 20 plays | Louder side correct in ≥ 18/20 plays by ≥ 3 dB; left sensor really on A0 | [ ] | |
| T-H4 | Touch: 20 taps, 10 holds, 10 double taps through the tape; then 10 min untouched with the motor buzzing | ≥ 95 % classified right; no phantom touches | [ ] | |
| T-H5 | LEDs: every pattern, worn, in a bright room | Noticeable at the edge of vision without dazzling; `led` brightness set (CFG / `hardware.led_brightness`) | [ ] | |
| T-H6 | Motor and power: 50 T3 cycles; current with the multimeter in series if possible | Clearly felt; no Arduino resets; current within budget; stops within 2 s when the laptop link is cut | [ ] | |
| T-H7 | Motor noise: sensor levels with the motor off, then on | With the capacitor the jump is small, and flagged (`motor_on`) readings are ignored | [ ] | |
| T-H8 | Wear test: 15 min worn, walking, turning your head, sitting | Nothing pulls loose; camera stays level; comfortable for three demos | [ ] | |

Notes / tuning values found on the bench:

- tap_ms = , hold_ms = , led = , MOTOR_SPEED = , SOFT_START_MS =
