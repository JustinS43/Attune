/*
 * Attune rig firmware - Arduino UNO R4 WiFi (TODO H-01 .. H-04).
 *
 * Measures, reports and plays patterns; makes no decisions about people or sounds.
 *  - READY <version> <driver> after probing the I2C motor driver (0x14 TB6612, 0x0F L298)
 *  - LV <ms> <left> <right> <motor> every CFG rate ms: peak-to-peak of each sound sensor
 *  - TOUCH TAP / HOLD / DOUBLE / TRIPLE from the touch sensor (CFG tap_ms, hold_ms)
 *  - HB <ms> every second; the laptop's HB every 0.5 s keeps the link alive
 *  - PAT / STOP -> ACK n; bad commands -> ERR
 *  - laptop silent for 2 s -> stop all lights and the motor, LOST icon, LOST blink
 *  - motor: 40 ms soft start, no buzz > 0.6 s, never > 60 % of any 2 s
 *  - built-in 12x8 LED matrix: HEART (pulsing), ALERT_L/R/B, PAUSE, LOST
 *
 * READY is also sent every time the link comes (back) up, because the laptop opens the
 * port without toggling DTR and so never resets the board.
 *
 * Nothing here blocks: loop() runs thousands of times a second.
 */
#include <Wire.h>
#include "Arduino_LED_Matrix.h"

#include "rig_config.h"
#include "protocol.h"
#include "patterns.h"

#if USE_TB6612_LIB
#include "Grove_Motor_Driver_TB6612FNG.h"
MotorDriver tb6612;
#endif
#if USE_L298_LIB
#include "Grove_I2C_Motor_Driver.h"
#endif
#if USE_SERVO_BACKUP
#include <Servo.h>
Servo tapper;
#endif

// ------------------------------------------------------------------------ types / state
enum Driver { DRV_NONE = 0, DRV_TB6612, DRV_L298 };

struct Slot {                // one pattern being played
  const PatternDef *def;     // NULL = idle
  char side;                 // 'L', 'R', 'B'
  unsigned long start;
};

struct Output {              // what the patterns want right now
  uint16_t light;            // 0..255 (before CFG led scaling)
  bool motor;
  uint16_t motorStepMs;      // length of the current buzz step, for the soft-start ramp
  char side;
};

ArduinoLEDMatrix matrix;

Driver driver = DRV_NONE;
bool driverUsable = false;

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
Slot background = {NULL, 'B', 0};   // repeating alert pattern (T3, T4)
Slot foreground = {NULL, 'B', 0};   // one-shot (BELL, NAME, OK, NO) plays over it
Slot lostSlot = {NULL, 'B', 0};

// motor
uint8_t motorLevel = 0;             // 0..255 currently applied
bool motorWanted = false;
unsigned long motorOnSince = 0;
bool buzzCapped = false;            // current buzz hit MAX_BUZZ_MS
uint16_t dutyBuckets[DUTY_WINDOW_MS / DUTY_BUCKET_MS];
uint8_t dutyIndex = 0;
unsigned long dutyBucketStart = 0;
unsigned long lastMotorTick = 0;

// sound levels
int minL = 1023, maxL = 0, minR = 1023, maxR = 0;
bool motorInWindow = false;
unsigned long nextReport = 0;

// touch
bool touchStable = false, touchRaw = false;
unsigned long touchRawChange = 0, pressStart = 0, tapReleasedAt = 0;
bool holdSent = false;
uint8_t tapCount = 0;  // short taps in the current burst; each must start within tap_ms of the last

// matrix
int currentIcon = -1;
bool heartBig = true;
unsigned long nextHeartToggle = 0;
uint8_t frame[8 * 12];

// serial input
char line[LINE_MAX + 1];
uint8_t lineLen = 0;
bool lineOverflow = false;

// ------------------------------------------------------------------------ matrix icons
// 8 rows of 12 columns; 'X' = LED on.
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

void setIcon(int icon) {
  if (icon < 0 || icon >= ICON_COUNT) return;
  currentIcon = icon;
  heartBig = true;
  nextHeartToggle = millis() + 500;
  drawRows(ICON_ROWS[icon]);
}

void updateMatrix(unsigned long now) {
  if (currentIcon != ICON_HEART || (long)(now - nextHeartToggle) < 0) return;
  nextHeartToggle = now + 500;
  heartBig = !heartBig;
  drawRows(heartBig ? ICON_ROWS[ICON_HEART] : HEART_SMALL);
}

// ------------------------------------------------------------------------ serial output
// The UNO R4 WiFi's Serial is a UART to the on-board USB bridge, which drains it at
// 115200 baud whether or not the laptop is reading, so these writes never stall loop()
// (LV + HB are about 500 bytes/s, under 5 % of the line rate).
void sendLine(const char *text) {
  Serial.print(text);
  Serial.print('\n');
}

void sendReady() {
  char buf[40];
  const char *name = driver == DRV_TB6612 ? DRIVER_TB6612 : driver == DRV_L298 ? DRIVER_L298 : DRIVER_NONE;
  snprintf(buf, sizeof(buf), "%s %s %s", MSG_READY, FW_VERSION, name);
  sendLine(buf);
}

void sendAck(long n) {
  char buf[24];
  snprintf(buf, sizeof(buf), "%s %ld", MSG_ACK, n);
  sendLine(buf);
}

void sendErr(const char *text) {
  char buf[LINE_MAX + 8];
  snprintf(buf, sizeof(buf), "%s %s", MSG_ERR, text);
  sendLine(buf);
}

void sendTouch(const char *gesture) {
  char buf[16];
  snprintf(buf, sizeof(buf), "%s %s", MSG_TOUCH, gesture);
  sendLine(buf);
}

// ------------------------------------------------------------------------ motor driver
bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

void probeDriver() {
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
    tapper.attach(PIN_SERVO);
    tapper.write(SERVO_REST_DEG);
    driverUsable = true;
  }
#endif
}

void applyMotor(uint8_t level) {  // level 0..255
  if (level == motorLevel) return;
  motorLevel = level;
  if (!driverUsable) return;
#if USE_TB6612_LIB
  if (driver == DRV_TB6612) {
    if (level == 0) tb6612.dcMotorStop(MOTOR_CHA);
    else tb6612.dcMotorRun(MOTOR_CHA, (int16_t)((uint32_t)level * MOTOR_SPEED_TB6612 / 255));
    return;
  }
#endif
#if USE_L298_LIB
  if (driver == DRV_L298) {
    if (level == 0) Motor.stop(MOTOR1);
    else Motor.speed(MOTOR1, (int)((uint32_t)level * MOTOR_SPEED_L298 / 255));
    return;
  }
#endif
#if USE_SERVO_BACKUP
  if (driver == DRV_NONE) tapper.write(level > 0 ? SERVO_TAP_DEG : SERVO_REST_DEG);
#endif
}

uint16_t dutyUsed() {
  uint16_t sum = 0;
  for (uint8_t i = 0; i < COUNT_OF(dutyBuckets); i++) sum += dutyBuckets[i];
  return sum;
}

// Soft start, max buzz length and the 60 %-of-2-s duty limit all live here.
void updateMotor(unsigned long now, bool want, uint16_t stepMs) {
  // account for the time the motor was on since the last tick
  unsigned long dt = now - lastMotorTick;
  lastMotorTick = now;
  if (motorLevel > 0) {
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
    applyMotor(0);
    return;
  }
  if (!motorWanted) {  // a new buzz starts
    motorWanted = true;
    motorOnSince = now;
    buzzCapped = false;
  }
  unsigned long onFor = now - motorOnSince;
  if (onFor >= MAX_BUZZ_MS) buzzCapped = true;
  if (buzzCapped || dutyUsed() >= DUTY_MAX_MS) {
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
const PatternDef *findPattern(const char *name) {
  for (uint8_t i = 0; i < PATTERN_COUNT; i++)
    if (strcmp(PATTERNS[i].name, name) == 0) return &PATTERNS[i];
  return NULL;
}

// Evaluate one slot; returns false when a one-shot pattern has finished.
bool evalSlot(Slot &slot, unsigned long now, Output &out) {
  if (slot.def == NULL) return false;
  unsigned long total = 0;
  for (uint8_t i = 0; i < slot.def->count; i++) total += slot.def->steps[i].ms;
  unsigned long elapsed = now - slot.start;
  if (elapsed >= total) {
    if (!slot.def->repeats) {
      slot.def = NULL;
      return false;
    }
    elapsed %= total;
  }
  unsigned long acc = 0;
  for (uint8_t i = 0; i < slot.def->count; i++) {
    const Step &s = slot.def->steps[i];
    if (elapsed < acc + s.ms) {
      if (s.light == LIGHT_FADE) {
        unsigned long x = (elapsed - acc) * 510UL / s.ms;  // 0..510
        out.light = x <= 255 ? x : 510 - x;
      } else {
        out.light = s.light;
      }
      out.motor = s.motor != 0;
      out.motorStepMs = s.ms;
      out.side = slot.side;
      return true;
    }
    acc += s.ms;
  }
  return true;
}

void stopAll() {
  background.def = NULL;
  foreground.def = NULL;
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
  updateMotor(now, out.motor, out.motorStepMs);
  if (motorLevel > 0) motorInWindow = true;
}

// ------------------------------------------------------------------------ link safety
void linkUp(unsigned long now) {
  linked = true;
  lastHbIn = now;
  lostSlot.def = NULL;
  setIcon(ICON_HEART);
  sendReady();  // the laptop may have opened the port long after we booted
}

void linkLost(unsigned long now) {
  linked = false;
  stopAll();
  applyMotor(0);
  setIcon(ICON_LOST);
  lostSlot.def = &PATTERN_LOST;
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
  char err[LINE_MAX];

  if (strcmp(head, CMD_HB) == 0) {
    if (!linked) linkUp(now);
    lastHbIn = now;
  } else if (strcmp(head, CMD_PAT) == 0) {
    if (a1 == NULL || a2 == NULL || a3 == NULL) { sendErr("PAT needs n side name"); return; }
    long n = atol(a1);
    char side = a2[0];
    if (a2[1] != '\0' || (side != 'L' && side != 'R' && side != 'B')) {
      snprintf(err, sizeof(err), "%ld bad side %s", n, a2);
      sendErr(err);
      return;
    }
    const PatternDef *def = findPattern(a3);
    if (def == NULL) {
      snprintf(err, sizeof(err), "%ld unknown pattern %s", n, a3);
      sendErr(err);
      return;
    }
    Slot &slot = def->repeats ? background : foreground;
    if (def->repeats) foreground.def = NULL;  // a new alarm replaces everything
    slot.def = def;
    slot.side = side;
    slot.start = now;
    sendAck(n);
  } else if (strcmp(head, CMD_STOP) == 0) {
    if (a1 == NULL) { sendErr("STOP needs n"); return; }
    stopAll();
    applyMotor(0);
    sendAck(atol(a1));
  } else if (strcmp(head, CMD_MX) == 0) {
    int icon = -1;
    for (int i = 0; a1 != NULL && i < ICON_COUNT; i++)
      if (strcmp(ICON_NAMES[i], a1) == 0) icon = i;
    if (icon < 0) { sendErr("bad icon"); return; }
    if (linked || icon == ICON_LOST) setIcon(icon);
  } else if (strcmp(head, CMD_CFG) == 0) {
    if (a1 == NULL || a2 == NULL) { sendErr("CFG needs key value"); return; }
    long v = atol(a2);
    if (strcmp(a1, CFG_RATE) == 0 && v >= 10 && v <= 1000) cfgRate = v;
    else if (strcmp(a1, CFG_TAP_MS) == 0 && v >= 100 && v <= 1000) cfgTapMs = v;
    else if (strcmp(a1, CFG_HOLD_MS) == 0 && v >= 300 && v <= 3000) cfgHoldMs = v;
    else if (strcmp(a1, CFG_LED) == 0 && v >= 0 && v <= 255) cfgLed = v;
    else {
      snprintf(err, sizeof(err), "bad CFG %s %s", a1, a2);
      sendErr(err);
    }
  } else {
    snprintf(err, sizeof(err), "unknown %s", head);
    sendErr(err);
  }
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (lineOverflow) sendErr("line too long");
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
          sendTouch(GESTURE_TRIPLE);
          tapCount = 0;
        }
      } else {
        tapCount = 0;  // between a tap and a hold: ignored, with any taps before it
      }
    }
  }
  if (touchStable && !holdSent && now - pressStart >= cfgHoldMs) {
    sendTouch(GESTURE_HOLD);
    holdSent = true;
    tapCount = 0;
  }
  // one or two taps are reported tap_ms after the last release, once no further tap came
  if (tapCount > 0 && !touchStable && now - tapReleasedAt > cfgTapMs) {
    sendTouch(tapCount == 1 ? GESTURE_TAP : GESTURE_DOUBLE);
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
  char buf[48];
  snprintf(buf, sizeof(buf), "%s %lu %d %d %d", MSG_LV, now, left, right, motorInWindow ? 1 : 0);
  sendLine(buf);
  minL = minR = 1023;
  maxL = maxR = 0;
  motorInWindow = motorLevel > 0;
}

// ------------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(SERIAL_BAUD);  // never wait for the port: the rig must run on its own
  pinMode(PIN_TOUCH, INPUT);
  pinMode(PIN_LED_L, OUTPUT);
  pinMode(PIN_LED_R, OUTPUT);
  analogWrite(PIN_LED_L, 0);
  analogWrite(PIN_LED_R, 0);
  analogReadResolution(10);
  matrix.begin();
  for (uint8_t i = 0; i < COUNT_OF(dutyBuckets); i++) dutyBuckets[i] = 0;
  probeDriver();
  applyMotor(0);
  unsigned long now = millis();
  dutyBucketStart = lastMotorTick = now;
  nextReport = now + cfgRate;
  linkLost(now);  // not linked until the laptop's first HB
  sendReady();
  if (driver != DRV_NONE && !driverUsable) sendErr("driver found but its library is disabled in rig_config.h");
#if !USE_SERVO_BACKUP
  if (driver == DRV_NONE) sendErr("driver missing");
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
    char buf[24];
    snprintf(buf, sizeof(buf), "%s %lu", MSG_HB, now);
    sendLine(buf);
  }
  updateMatrix(now);
}
