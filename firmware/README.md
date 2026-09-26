# Firmware for the Arduino UNO R4 WiFi

**Owner:** Section 3 - Hardware & Services
**TODO:** H-01 - H-04
**Plan:** section 04 "What the Arduino does", "Light and buzz patterns", "Messages over USB"

The sketch measures, reports and plays patterns with exact timing. It makes no decisions
about people or sounds; the laptop does.

| File | What it holds |
|---|---|
| `attune_rig/attune_rig.ino` | The sketch: sensors, touch gestures, patterns, motor safety, link watchdog, LED matrix |
| `attune_rig/protocol.h` | Serial message names, CFG keys and defaults (mirror of `engine/attune/hardware/protocol.py`) |
| `attune_rig/patterns.h` | T3, T4, BELL, NAME, OK, NO, LOST step tables (mirror of `engine/attune/hardware/simulator.py`) |
| `attune_rig/rig_config.h` | Pins, which driver library to compile, buzz strength, safety limits |

> **Not compiled yet.** This was written without `arduino-cli` or the board at hand. The
> first job on the bench is a compile; fix any typo there (library API names are the
> most likely spot, see below).

## Build and upload

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

If the compile complains about a driver-library call, the four calls used are
`MotorDriver::init(addr)`, `notStandby()`, `dcMotorRun(MOTOR_CHA, speed)`,
`dcMotorStop(MOTOR_CHA)` (TB6612) and `Motor.begin(addr)`, `Motor.speed(MOTOR1, v)`,
`Motor.stop(MOTOR1)` (L298). Match them to the installed library's example sketch.

## Behaviour

- **Start-up:** probes I²C (`0x14` → TB6612, `0x0F` → L298, else NONE; with `USE_SERVO_BACKUP`
  the D9 servo becomes the tapper), prints `READY 1.0.0 <driver>`, shows `?` (LOST) until the
  laptop's first `HB`.
- **Link:** every laptop `HB` refreshes a 2 s watchdog. On the first `HB` after being
  unlinked the board prints `READY` again: the laptop opens the port without toggling DTR,
  so the board does not reset and the boot `READY` may be long gone.
  If the watchdog fires: all lights and the motor stop, the matrix shows `?`, both LEDs blink
  dimly every 2 s (LOST) until `HB` returns.
- **Levels:** both sound sensors are sampled every loop (thousands of times a second); every
  `rate` ms (default 50) the board prints `LV <ms> <left> <right> <motor>` with the
  peak-to-peak of each side (0–1023). `motor` is 1 if the motor ran at any time in that window.
- **Touch:** tap (< `tap_ms`, default 400), hold (≥ `hold_ms`, default 800, sent while still
  held), double (second tap starts within `tap_ms` of the first), triple (a third tap within
  `tap_ms` of the second; sent at once on its release). A single or double tap is sent `tap_ms`
  after the last release, once no further tap came. 20 ms debounce. The laptop reads tap = yes,
  hold = no, double = save this person, triple = pause (see the touch router).
- **Patterns:** `PAT n side name` → `ACK n`. T3/T4 repeat until `STOP`; BELL/NAME/OK/NO play
  once over a running alarm pattern, which then continues. `STOP n` → `ACK n`.
- **Motor safety:** 40 ms soft start (shorter for very short ticks), no single buzz longer
  than 0.6 s, never more than 1.2 s of buzzing in any 2 s window (tracked in 100 ms buckets).
- **Matrix:** `MX HEART` (pulsing heart), `ALERT_L`/`ALERT_R` (`!` + arrow), `ALERT_B`
  (`!`, side unknown — an addition to the contract's list), `PAUSE` (`P`), `LOST` (`?`).
- **CFG:** `rate` 10–1000, `tap_ms` 100–1000, `hold_ms` 300–3000, `led` 0–255; out of range → `ERR`.
- **Errors:** `ERR <n> bad side …`, `ERR <n> unknown pattern …`, `ERR unknown <cmd>`,
  `ERR line too long`, `ERR driver found but its library is disabled in rig_config.h`.

Test by hand from the Arduino IDE Serial Monitor (115200, newline): type `HB`, then
`PAT 1 L T3`, `STOP 2`, `MX PAUSE`, `CFG led 60` — and stop typing `HB` for 2 s to see LOST.
