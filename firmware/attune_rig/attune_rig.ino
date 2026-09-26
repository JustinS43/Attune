/*
 * Attune rig firmware - Arduino UNO R3 (servo tapper, LED 13) or UNO R4 WiFi (motor, matrix)
 * (TODO H-01 .. H-04, H-15).
 *
 * Measures, reports and plays patterns; makes no decisions about people or sounds.
 *  - READY <version> <driver>: the I2C motor driver found (0x14 TB6612, 0x0F L298), or NONE
 *    when the servo taps (UNO R3 default, or the R4's backup when no driver answers)
 *  - LV <ms> <left> <right> <motor> every CFG rate ms: peak-to-peak of each sound sensor
 *  - TOUCH TAP / HOLD / DOUBLE / TRIPLE from the touch sensor (CFG tap_ms, hold_ms)
 *  - HB <ms> every second; the laptop's HB every 0.5 s keeps the link alive
 *  - PAT / STOP -> ACK n; bad commands -> ERR
 *  - laptop silent for 2 s -> stop all lights and the buzzer, LOST icon, LOST blink
 *  - motor: 40 ms soft start, no buzz > 0.6 s, never > 60 % of any 2 s
 *  - servo: each buzzing step taps (rest -> tap angle -> rest), same 0.6 s / 60 % limits,
 *    detached when idle
 *  - status: 12x8 LED matrix on the R4 WiFi; blink codes on LED 13 elsewhere
 *
 * READY is also sent every time the link comes (back) up. The laptop opens the port with
 * DTR low; the R4 WiFi then does not reset, so its boot READY may be long gone. The UNO R3
 * may still reset when the port opens (its USB chip pulses reset on DTR changes); it then
 * prints READY at boot, and again on the laptop's first HB. Either way the laptop links.
 *
 * Nothing here blocks: loop() runs thousands of times a second.
 * Memory (UNO R3 has 2 KB RAM): no String, no printf, text and tables in flash (board.h).
 */
#include "board.h"
#include "rig_config.h"
#include "protocol.h"
#include "patterns.h"

#if BUZZER != BUZZER_SERVO && BUZZER != BUZZER_MOTOR
#error "rig_config.h: BUZZER must be BUZZER_SERVO or BUZZER_MOTOR"
#endif

#if BUZZER == BUZZER_MOTOR
#include <Wire.h>
#if USE_TB6612_LIB
#include "Grove_Motor_Driver_TB6612FNG.h"
MotorDriver tb6612;
#endif
#if USE_L298_LIB
#include "Grove_I2C_Motor_Driver.h"
#endif
#endif

#define HAS_TAPPER (BUZZER == BUZZER_SERVO || USE_SERVO_BACKUP)
#if HAS_TAPPER
#include <Servo.h>
Servo tapper;
#endif

#if RIG_HAS_MATRIX
#include "Arduino_LED_Matrix.h"
ArduinoLEDMatrix matrix;
#endif

// ------------------------------------------------------------------------ types / state
enum Driver : uint8_t { DRV_NONE = 0, DRV_TB6612, DRV_L298 };

struct Slot {                // one pattern being played
  int8_t pat;                // index into PATTERNS, -1 = idle
  char side;                 // 'L', 'R', 'B'
  unsigned long start;
};

struct Output {              // what the patterns want right now
  uint16_t light;            // 0..255 (before CFG led scaling)
  bool motor;
  uint16_t motorStepMs;      // length of the current buzz step, for the soft-start ramp
  char side;
};

Driver driver = DRV_NONE;
bool driverUsable = false;
bool servoBuzzer = false;           // the D9 servo taps instead of a motor

// config (CFG)
uint16_t cfgRate = DEFAULT_RATE_MS;
uint16_t cfgTapMs = DEFAULT_TAP_MS;
uint16_t cfgHoldMs = DEFAULT_HOLD_MS;
uint8_t cfgLed = DEFAULT_LED;

// link
bool linked = false;
unsigned long lastHbIn = 0;
unsigned long nextHbOut = HB_OUT_MS;

// patterns
Slot background = {-1, 'B', 0};     // repeating alert pattern (T3, T4)
Slot foreground = {-1, 'B', 0};     // one-shot (BELL, NAME, OK, NO) plays over it
Slot lostSlot = {-1, 'B', 0};

// buzzer (motor or servo) and its safety budget
uint8_t motorLevel = 0;             // DC motor: 0..255 currently applied
bool motorWanted = false;           // a buzz is being asked for (either buzzer)
unsigned long motorOnSince = 0;
bool buzzCapped = false;            // current buzz hit MAX_BUZZ_MS
uint16_t dutyBuckets[DUTY_WINDOW_MS / DUTY_BUCKET_MS];
uint8_t dutyIndex = 0;
unsigned long dutyBucketStart = 0;
unsigned long lastMotorTick = 0;

// servo tapper
enum TapPhase : uint8_t { TAP_IDLE = 0, TAP_OUT, TAP_BACK };
TapPhase tapPhase = TAP_IDLE;
unsigned long tapPhaseStart = 0;    // start of TAP_OUT / TAP_BACK, or of idling at rest
bool tapAttached = false;

// sound levels
int minL = 1023, maxL = 0, minR = 1023, maxR = 0;
bool motorInWindow = false;
unsigned long nextReport = 0;

// touch
bool touchStable = false, touchRaw = false;
unsigned long touchRawChange = 0, pressStart = 0, tapReleasedAt = 0;
bool holdSent = false;
uint8_t tapCount = 0;  // short taps in the current burst; each must start within tap_ms of the last

// status display
int8_t currentIcon = -1;

// serial input
char line[LINE_MAX + 1];
uint8_t lineLen = 0;
bool lineOverflow = false;

// ------------------------------------------------------------------------ status display
#if RIG_HAS_MATRIX
// UNO R4 WiFi: 8 rows of 12 columns; 'X' = LED on.
bool heartBig = true;
unsigned long nextHeartToggle = 0;
uint8_t frame[8 * 12];

static const char *const ICON_ROWS[][8] = {
    {  // HEART (big)
        "..XX..XX....", ".XXXXXXXX...", ".XXXXXXXX...", ".XXXXXXXX...",
        "..XXXXXX....", "...XXXX.....", "....XX......", "............"},
    {  // ALERT_L: arrow left, "!"
        "...X........", "..XX....XX..", ".XXX....XX..", "XXXX....XX..",
        ".XXX....XX..", "..XX........", "...X....XX..", "............"},
    {  // ALERT_R: "!", arrow right
        "........X...", "..XX....XX..", "..XX....XXX.", "..XX....XXXX",
        "..XX....XXX.", "........XX..", "..XX....X...", "............"},
    {  // ALERT_B: "!"
        ".....XX.....", ".....XX.....", ".....XX.....", ".....XX.....",
        ".....XX.....", "............", ".....XX.....", "............"},
    {  // PAUSE: "P"
        "....XXXX....", "....X...X...", "....X...X...", "....XXXX....",
        "....X.......", "....X.......", "....X.......", "............"},
    {  // LOST: "?"
        "....XXX.....", "...X...X....", ".......X....", "......X.....",
        ".....X......", "............", ".....X......", "............"},
};
static const char *const HEART_SMALL[8] = {
    "............", "...X..X.....", "..XXXXXX....", "..XXXXXX....",
    "...XXXX.....", "....XX......", "............", "............"};

void drawRows(const char *const rows[8]) {
  for (uint8_t r = 0; r < 8; r++)
    for (uint8_t c = 0; c < 12; c++) frame[r * 12 + c] = rows[r][c] == 'X' ? 1 : 0;
  matrix.loadPixels(frame, sizeof(frame));
}

void statusBegin() { matrix.begin(); }

void setIcon(int8_t icon) {
  if (icon < 0 || icon >= ICON_COUNT) return;
  currentIcon = icon;
  heartBig = true;
  nextHeartToggle = millis() + 500;
  drawRows(ICON_ROWS[icon]);
}

void updateStatus(unsigned long now) {
  if (currentIcon != ICON_HEART || (long)(now - nextHeartToggle) < 0) return;
  nextHeartToggle = now + 500;
  heartBig = !heartBig;
  drawRows(heartBig ? ICON_ROWS[ICON_HEART] : HEART_SMALL);
}

#else
// UNO R3 (and any board without the matrix): blink codes on LED 13. D13 has no PWM, so the
// heart is a slow blink rather than a breathe. Each code is on/off times in ms, starting on.
static const uint16_t BLINK_HEART[] RIG_PROGMEM = {500, 500};             // slow blink: linked
static const uint16_t BLINK_ALERT[] RIG_PROGMEM = {100, 100};             // fast blink: alert (any side)
static const uint16_t BLINK_PAUSE[] RIG_PROGMEM = {100, 150, 100, 1150};  // double blink: paused
static const uint16_t BLINK_LOST[] RIG_PROGMEM = {50, 1950};              // blip every 2 s: link lost

const uint16_t *blinkCode = NULL;
uint8_t blinkLen = 0, blinkStep = 0;
unsigned long blinkStepStart = 0;

void statusBegin() {
  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, LOW);
}

void setIcon(int8_t icon) {
  if (icon < 0 || icon >= ICON_COUNT) return;
  if (icon == currentIcon) return;  // keep the rhythm when the laptop repeats an icon
  currentIcon = icon;
  switch (icon) {
    case ICON_HEART: blinkCode = BLINK_HEART; blinkLen = COUNT_OF(BLINK_HEART); break;
    case ICON_PAUSE: blinkCode = BLINK_PAUSE; blinkLen = COUNT_OF(BLINK_PAUSE); break;
    case ICON_LOST: blinkCode = BLINK_LOST; blinkLen = COUNT_OF(BLINK_LOST); break;
    default: blinkCode = BLINK_ALERT; blinkLen = COUNT_OF(BLINK_ALERT); break;  // ALERT_L/R/B
  }
  blinkStep = 0;
  blinkStepStart = millis();
  digitalWrite(PIN_STATUS_LED, HIGH);
}

void updateStatus(unsigned long now) {
  if (blinkCode == NULL) return;
  uint16_t stepMs = RIG_READ_U16(&blinkCode[blinkStep]);
  if (now - blinkStepStart < stepMs) return;
  blinkStepStart = now;
  blinkStep = (blinkStep + 1) % blinkLen;
  digitalWrite(PIN_STATUS_LED, (blinkStep & 1) ? LOW : HIGH);  // even steps are "on"
}
#endif

// ------------------------------------------------------------------------ serial output
// Lines are printed piece by piece with text kept in flash (F()); the bytes on the wire are
// the same as one formatted line.
// UNO R3: Serial is the ATmega328P UART to the on-board 16U2 USB chip. The UART drains at
// 115200 baud whether or not the laptop reads (no flow control; the 16U2 drops what the laptop
// doesn't take), so a print only waits while its 64-byte TX buffer is full. RX is also 64 bytes:
// loop() empties it far faster than the ~11 bytes/ms that can arrive.
// UNO R4 WiFi: Serial is a UART to the on-board USB bridge; same reasoning, bigger buffers.
// LV + HB are about 500 bytes/s, under 5 % of the line rate.
void endLine() { Serial.print('\n'); }

void sendReady() {
  Serial.print(F(MSG_READY " " FW_VERSION " "));
  if (driver == DRV_TB6612) Serial.print(F(DRIVER_TB6612));
  else if (driver == DRV_L298) Serial.print(F(DRIVER_L298));
  else Serial.print(F(DRIVER_NONE));
  endLine();
}

void sendAck(long n) {
  Serial.print(F(MSG_ACK " "));
  Serial.print(n);
  endLine();
}

void errStart() { Serial.print(F(MSG_ERR " ")); }

void sendErr(const __FlashStringHelper *text) {
  errStart();
  Serial.print(text);
  endLine();
}

void sendTouch(const __FlashStringHelper *gesture) {
  Serial.print(F(MSG_TOUCH " "));
  Serial.print(gesture);
  endLine();
}

// ------------------------------------------------------------------------ servo tapper
#if HAS_TAPPER
void tapWrite(uint8_t deg) {
  tapper.write(deg);  // AVR: sets the first pulse before attach, so the arm doesn't jump
  if (!tapAttached) {
    tapper.attach(PIN_SERVO, SERVO_MIN_US, SERVO_MAX_US);
    tapAttached = true;
    tapper.write(deg);  // some cores only take a position once attached
  }
}

// Park the arm at rest; it detaches SERVO_DETACH_MS later.
void tapPark(unsigned long now) {
  tapWrite(SERVO_REST_DEG);
  tapPhase = TAP_IDLE;
  tapPhaseStart = now;
}

// Send the arm back to rest now (STOP, link lost). A tap in progress ends early.
void tapToRest(unsigned long now) {
  if (tapPhase != TAP_OUT) return;
  tapper.write(SERVO_REST_DEG);
  tapPhase = TAP_BACK;
  tapPhaseStart = now;
}

// One tap = TAP_OUT (at the tap angle) then TAP_BACK (at rest); a started tap always
// finishes at rest. `allowed` = a buzz is wanted and the safety budget has room.
void updateTapper(unsigned long now, bool allowed) {
  if (tapPhase == TAP_OUT && now - tapPhaseStart >= SERVO_TAP_OUT_MS) {
    tapper.write(SERVO_REST_DEG);
    tapPhase = TAP_BACK;
    tapPhaseStart = now;
  }
  if (tapPhase == TAP_BACK && now - tapPhaseStart >= SERVO_TAP_BACK_MS) {
    tapPhase = TAP_IDLE;
    tapPhaseStart = now;
  }
  if (tapPhase != TAP_IDLE) return;
  if (allowed) {
    tapWrite(SERVO_TAP_DEG);
    tapPhase = TAP_OUT;
    tapPhaseStart = now;
  } else if (tapAttached && now - tapPhaseStart >= SERVO_DETACH_MS) {
    tapper.detach();  // no holding torque needed at rest; stops pulse jitter and noise
    tapAttached = false;
  }
}
#else
void tapPark(unsigned long) {}
void tapToRest(unsigned long) {}
void updateTapper(unsigned long, bool) {}
#endif

// ------------------------------------------------------------------------ motor driver
#if BUZZER == BUZZER_MOTOR
bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}
#endif

void setupBuzzer(unsigned long now) {
#if BUZZER == BUZZER_SERVO
  servoBuzzer = true;
  driverUsable = true;
#else
  Wire.begin();
  delay(100);  // the driver's own microcontroller boots after ours
  if (i2cPresent(TB6612_ADDR)) {
    driver = DRV_TB6612;
#if USE_TB6612_LIB
    tb6612.init(TB6612_ADDR);
    tb6612.notStandby();  // make sure the H-bridge is awake
    driverUsable = true;
#endif
  } else if (i2cPresent(L298_ADDR)) {
    driver = DRV_L298;
#if USE_L298_LIB
    Motor.begin(L298_ADDR);
    driverUsable = true;
#endif
  }
#if USE_SERVO_BACKUP
  if (driver == DRV_NONE) {
    servoBuzzer = true;
    driverUsable = true;
  }
#endif
#endif
  if (servoBuzzer) tapPark(now);
}

void applyMotor(uint8_t level) {  // DC motor level 0..255
  if (level == motorLevel) return;
  motorLevel = level;
  if (!driverUsable || servoBuzzer) return;
#if BUZZER == BUZZER_MOTOR && USE_TB6612_LIB
  if (driver == DRV_TB6612) {
    if (level == 0) tb6612.dcMotorStop(MOTOR_CHA);
    else tb6612.dcMotorRun(MOTOR_CHA, (int16_t)((uint32_t)level * MOTOR_SPEED_TB6612 / 255));
    return;
  }
#endif
#if BUZZER == BUZZER_MOTOR && USE_L298_LIB
  if (driver == DRV_L298) {
    if (level == 0) Motor.stop(MOTOR1);
    else Motor.speed(MOTOR1, (int)((uint32_t)level * MOTOR_SPEED_L298 / 255));
    return;
  }
#endif
}

// Stop the buzzer at once (STOP, link lost).
void buzzOff(unsigned long now) {
  applyMotor(0);
  if (servoBuzzer) tapToRest(now);
}

// The buzzer is working: motor running, or the servo arm mid-tap.
bool buzzing() { return motorLevel > 0 || tapPhase != TAP_IDLE; }

// The buzzer may disturb the sound sensors: also while the servo is still attached at rest.
bool buzzerNoisy() { return buzzing() || tapAttached; }

uint16_t dutyUsed() {
  uint16_t sum = 0;
  for (uint8_t i = 0; i < COUNT_OF(dutyBuckets); i++) sum += dutyBuckets[i];
  return sum;
}

// Soft start (motor), max buzz length and the 60 %-of-2-s duty limit (both buzzers) live here.
void updateBuzzer(unsigned long now, bool want, uint16_t stepMs) {
  // account for the time the buzzer was working since the last tick
  unsigned long dt = now - lastMotorTick;
  lastMotorTick = now;
  if (buzzing()) {
    uint16_t add = dt > 1000 ? 1000 : (uint16_t)dt;
    dutyBuckets[dutyIndex] += add;
  }
  while (now - dutyBucketStart >= DUTY_BUCKET_MS) {
    dutyBucketStart += DUTY_BUCKET_MS;
    dutyIndex = (dutyIndex + 1) % COUNT_OF(dutyBuckets);
    dutyBuckets[dutyIndex] = 0;
  }

  if (!want) {
    motorWanted = false;
    buzzCapped = false;
    if (servoBuzzer) updateTapper(now, false);
    else applyMotor(0);
    return;
  }
  if (!motorWanted) {  // a new buzz starts
    motorWanted = true;
    motorOnSince = now;
    buzzCapped = false;
  }
  unsigned long onFor = now - motorOnSince;
  if (onFor >= MAX_BUZZ_MS) buzzCapped = true;
  bool allowed = !buzzCapped && dutyUsed() < DUTY_MAX_MS;
  if (servoBuzzer) {
    updateTapper(now, allowed);
    return;
  }
  if (!allowed) {
    applyMotor(0);
    return;
  }
  uint16_t ramp = SOFT_START_MS;
  if (stepMs / 2 < ramp) ramp = stepMs / 2;
  uint8_t level = 255;
  if (ramp > 0 && onFor < ramp) level = (uint8_t)(64 + (uint32_t)191 * onFor / ramp);
  applyMotor(level);
}

// ------------------------------------------------------------------------ patterns
void loadPattern(int8_t index, PatternDef &def) {
  RIG_MEMCPY_P(&def, &PATTERNS[index], sizeof(PatternDef));
}

int8_t findPattern(const char *name) {  // only the ones PAT accepts, not LOST
  for (uint8_t i = 0; i < PATTERN_COUNT; i++) {
    PatternDef def;
    loadPattern(i, def);
    if (RIG_STRCMP_P(name, def.name) == 0) return (int8_t)i;
  }
  return -1;
}

// Evaluate one slot; returns false when idle or when a one-shot pattern has finished.
bool evalSlot(Slot &slot, unsigned long now, Output &out) {
  if (slot.pat < 0) return false;
  PatternDef def;
  loadPattern(slot.pat, def);
  unsigned long total = 0;
  for (uint8_t i = 0; i < def.count; i++) total += RIG_READ_U16(&def.steps[i].ms);
  unsigned long elapsed = now - slot.start;
  if (elapsed >= total) {
    if (!def.repeats) {
      slot.pat = -1;
      return false;
    }
    elapsed %= total;
  }
  unsigned long acc = 0;
  for (uint8_t i = 0; i < def.count; i++) {
    uint16_t ms = RIG_READ_U16(&def.steps[i].ms);
    if (elapsed < acc + ms) {
      uint16_t light = RIG_READ_U16(&def.steps[i].light);
      if (light == LIGHT_FADE) {
        unsigned long x = (elapsed - acc) * 510UL / ms;  // 0..510
        out.light = x <= 255 ? x : 510 - x;
      } else {
        out.light = light;
      }
      out.motor = RIG_READ_U8(&def.steps[i].motor) != 0;
      out.motorStepMs = ms;
      out.side = slot.side;
      return true;
    }
    acc += ms;
  }
  return true;
}

void stopAll() {
  background.pat = -1;
  foreground.pat = -1;
}

void writeLeds(uint16_t light, char side, bool dim) {
  uint32_t level = (uint32_t)light * cfgLed / 255;
  if (dim) level = level * LOST_LED_LEVEL / 255;
  uint8_t l = (side == 'L' || side == 'B') ? level : 0;
  uint8_t r = (side == 'R' || side == 'B') ? level : 0;
  analogWrite(PIN_LED_L, l);
  analogWrite(PIN_LED_R, r);
}

void updatePatterns(unsigned long now) {
  Output out = {0, false, 0, 'B'};
  bool dim = false;
  if (!evalSlot(foreground, now, out) && !evalSlot(background, now, out)) {
    if (evalSlot(lostSlot, now, out)) dim = true;
  }
  writeLeds(out.light, out.side, dim);
  updateBuzzer(now, out.motor, out.motorStepMs);
  if (buzzerNoisy()) motorInWindow = true;
}

// ------------------------------------------------------------------------ link safety
void linkUp(unsigned long now) {
  linked = true;
  lastHbIn = now;
  lostSlot.pat = -1;
  setIcon(ICON_HEART);
  sendReady();  // the laptop may have opened the port long after we booted
}

void linkLost(unsigned long now) {
  linked = false;
  stopAll();
  buzzOff(now);
  setIcon(ICON_LOST);
  lostSlot.pat = PATTERN_LOST;
  lostSlot.side = 'B';
  lostSlot.start = now;
}

// ------------------------------------------------------------------------ commands
void handleLine(char *text) {
  unsigned long now = millis();
  char *head = strtok(text, " \t");
  if (head == NULL) return;
  char *a1 = strtok(NULL, " \t");
  char *a2 = strtok(NULL, " \t");
  char *a3 = strtok(NULL, " \t");

  if (RIG_IS(head, CMD_HB)) {
    if (!linked) linkUp(now);
    lastHbIn = now;
  } else if (RIG_IS(head, CMD_PAT)) {
    if (a1 == NULL || a2 == NULL || a3 == NULL) { sendErr(F("PAT needs n side name")); return; }
    long n = atol(a1);
    char side = a2[0];
    if (a2[1] != '\0' || (side != 'L' && side != 'R' && side != 'B')) {
      errStart();
      Serial.print(n);
      Serial.print(F(" bad side "));
      Serial.print(a2);
      endLine();
      return;
    }
    int8_t index = findPattern(a3);
    if (index < 0) {
      errStart();
      Serial.print(n);
      Serial.print(F(" unknown pattern "));
      Serial.print(a3);
      endLine();
      return;
    }
    PatternDef def;
    loadPattern(index, def);
    Slot &slot = def.repeats ? background : foreground;
    if (def.repeats) foreground.pat = -1;  // a new alarm replaces everything
    slot.pat = index;
    slot.side = side;
    slot.start = now;
    sendAck(n);
  } else if (RIG_IS(head, CMD_STOP)) {
    if (a1 == NULL) { sendErr(F("STOP needs n")); return; }
    stopAll();
    buzzOff(now);
    sendAck(atol(a1));
  } else if (RIG_IS(head, CMD_MX)) {  // no ACK, as in the contract
    int8_t icon = -1;
    for (uint8_t i = 0; a1 != NULL && i < ICON_COUNT; i++)
      if (RIG_STRCMP_P(a1, (const char *)RIG_READ_PTR(&ICON_NAMES[i])) == 0) icon = i;
    if (icon < 0) { sendErr(F("bad icon")); return; }
    if (linked || icon == ICON_LOST) setIcon(icon);
  } else if (RIG_IS(head, CMD_CFG)) {
    if (a1 == NULL || a2 == NULL) { sendErr(F("CFG needs key value")); return; }
    long v = atol(a2);
    if (RIG_IS(a1, CFG_RATE) && v >= 10 && v <= 1000) cfgRate = v;
    else if (RIG_IS(a1, CFG_TAP_MS) && v >= 100 && v <= 1000) cfgTapMs = v;
    else if (RIG_IS(a1, CFG_HOLD_MS) && v >= 300 && v <= 3000) cfgHoldMs = v;
    else if (RIG_IS(a1, CFG_LED) && v >= 0 && v <= 255) cfgLed = v;
    else {
      errStart();
      Serial.print(F("bad CFG "));
      Serial.print(a1);
      Serial.print(' ');
      Serial.print(a2);
      endLine();
    }
  } else {
    errStart();
    Serial.print(F("unknown "));
    Serial.print(head);
    endLine();
  }
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (lineOverflow) sendErr(F("line too long"));
      else if (lineLen > 0) {
        line[lineLen] = '\0';
        handleLine(line);
      }
      lineLen = 0;
      lineOverflow = false;
    } else if (lineLen < LINE_MAX) {
      line[lineLen++] = c;
    } else {
      lineOverflow = true;
    }
  }
}

// ------------------------------------------------------------------------ touch
void updateTouch(unsigned long now) {
  bool raw = digitalRead(PIN_TOUCH) == HIGH;
  if (raw != touchRaw) {
    touchRaw = raw;
    touchRawChange = now;
  }
  if (raw != touchStable && now - touchRawChange >= TOUCH_DEBOUNCE_MS) {
    touchStable = raw;
    if (touchStable) {  // pressed
      pressStart = now;
      holdSent = false;
      if (tapCount > 0 && (now - tapReleasedAt) > cfgTapMs) tapCount = 0;  // too late: a new burst
    } else {            // released
      unsigned long held = now - pressStart;
      if (holdSent) {
        // the hold was already reported
      } else if (held < cfgTapMs) {
        tapCount++;
        tapReleasedAt = now;
        if (tapCount >= 3) {  // a third tap needs no wait: nothing longer exists
          sendTouch(F(GESTURE_TRIPLE));
          tapCount = 0;
        }
      } else {
        tapCount = 0;  // between a tap and a hold: ignored, with any taps before it
      }
    }
  }
  if (touchStable && !holdSent && now - pressStart >= cfgHoldMs) {
    sendTouch(F(GESTURE_HOLD));
    holdSent = true;
    tapCount = 0;
  }
  // one or two taps are reported tap_ms after the last release, once no further tap came
  if (tapCount > 0 && !touchStable && now - tapReleasedAt > cfgTapMs) {
    if (tapCount == 1) sendTouch(F(GESTURE_TAP));
    else sendTouch(F(GESTURE_DOUBLE));
    tapCount = 0;
  }
}

// ------------------------------------------------------------------------ sound levels
void sampleSound() {
  int l = analogRead(PIN_SOUND_L);
  int r = analogRead(PIN_SOUND_R);
  if (l < minL) minL = l;
  if (l > maxL) maxL = l;
  if (r < minR) minR = r;
  if (r > maxR) maxR = r;
}

void reportLevels(unsigned long now) {
  if ((long)(now - nextReport) < 0) return;
  nextReport = now + cfgRate;
  int left = maxL >= minL ? maxL - minL : 0;
  int right = maxR >= minR ? maxR - minR : 0;
  Serial.print(F(MSG_LV " "));
  Serial.print(now);
  Serial.print(' ');
  Serial.print(left);
  Serial.print(' ');
  Serial.print(right);
  Serial.print(' ');
  Serial.print(motorInWindow ? '1' : '0');
  endLine();
  minL = minR = 1023;
  maxL = maxR = 0;
  motorInWindow = buzzerNoisy();
}

// ------------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(SERIAL_BAUD);  // never wait for the port: the rig must run on its own
  pinMode(PIN_TOUCH, INPUT);
  pinMode(PIN_LED_L, OUTPUT);
  pinMode(PIN_LED_R, OUTPUT);
  analogWrite(PIN_LED_L, 0);
  analogWrite(PIN_LED_R, 0);
#if !RIG_AVR
  analogReadResolution(10);  // the R4 can read 12/14 bit; the protocol's levels are 0..1023
#endif
  statusBegin();
  for (uint8_t i = 0; i < COUNT_OF(dutyBuckets); i++) dutyBuckets[i] = 0;
  setupBuzzer(millis());
  applyMotor(0);
  unsigned long now = millis();
  dutyBucketStart = lastMotorTick = now;
  nextReport = now + cfgRate;
  linkLost(now);  // not linked until the laptop's first HB
  sendReady();
  if (driver != DRV_NONE && !driverUsable) sendErr(F("driver found but its library is disabled in rig_config.h"));
#if BUZZER == BUZZER_MOTOR && !USE_SERVO_BACKUP
  if (driver == DRV_NONE) sendErr(F("driver missing"));
#endif
}

void loop() {
  unsigned long now = millis();
  readSerial();
  if (linked && now - lastHbIn > HB_TIMEOUT_MS) linkLost(now);
  sampleSound();
  updateTouch(now);
  updatePatterns(now);
  reportLevels(now);
  if ((long)(now - nextHbOut) >= 0) {
    nextHbOut = now + HB_OUT_MS;
    Serial.print(F(MSG_HB " "));
    Serial.print(now);
    endLine();
  }
  updateStatus(now);
}
