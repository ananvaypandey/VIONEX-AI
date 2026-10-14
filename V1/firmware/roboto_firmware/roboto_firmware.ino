/*
  roboto_firmware.ino  -  VIONEX AI robot firmware
  Implements PROTOCOL.md so main.py can drive the robot over USB serial.

  Serial: 115200 baud, newline-terminated commands.
    IDLE        -> go to rest pose
    PLAY talk   -> loop the talking gesture until STOP
    PLAY bye    -> one-shot wave (bye v2)
    PLAY hello  -> same one-shot wave as bye
    PLAY walk   -> one-shot walk (walk-v2), then back to standing
    STOP        -> abort current animation, return to rest

  Board: Arduino Uno + Adafruit PCA9685 PWM servo shield (addr 0x40).
  Arm map = TALK.ino; leg map = walk-*.ino (CH9-12).
*/

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

// ---------------- channels (TALK.ino map) ----------------
#define HEAD 0
#define R_SHOULDER_FB 1
#define L_SHOULDER_FB 2
#define R_SHOULDER_UD 3
#define L_SHOULDER_UD 4
#define R_BICEP 5
#define L_BICEP 6
#define R_ELBOW 7
#define L_ELBOW 8

// ---------------- channels (walk-*.ino map) ----------------
// NOTE: CH13/CH14 = feet are DISCONNECTED, never commanded.
#define L_THIGH 9
#define R_THIGH 10
#define L_KNEE 11
#define R_KNEE 12

#define SERVO_MIN 150
#define SERVO_MAX 600

// ---------------- modes ----------------
#define MODE_NONE -1
#define MODE_IDLE 0
#define MODE_TALK 1
#define MODE_BYE 2
#define MODE_HELLO 3
#define MODE_WALK 4

// ---------------- walk calibration (walk-v2.ino) ----------------
#define L_THIGH_CENTER 70
#define R_THIGH_CENTER 75
#define L_KNEE_STRAIGHT 45
#define R_KNEE_STRAIGHT 90
#define R_ARM_CENTER 90
#define L_ARM_CENTER 90

int curLT, curRT, curLK, curRK, curRA, curLA;

int mode = MODE_IDLE;
int pendingMode = MODE_NONE;
bool abortFlag = false;

String lineBuf = "";

// ---------------- forward declarations ----------------
void readSerial();
void handleCommand(String cmd);
void enterIdle();
void applyPending();

// ---------------- low level ----------------
void setServo(int channel, int angle) {
  angle = constrain(angle, 0, 180);
  int pulse = map(angle, 0, 180, SERVO_MIN, SERVO_MAX);
  pwm.setPWM(channel, 0, pulse);
}

// Poll serial while waiting; returns false if a STOP/abort arrived.
bool waitMs(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) {
    readSerial();
    if (abortFlag) return false;
    delay(1);
  }
  return true;
}

// Interruptible move: returns false if aborted mid-way.
bool moveServo(int channel, int from, int to, int spd) {
  if (from < to) {
    for (int a = from; a <= to; a++) {
      setServo(channel, a);
      if (!waitMs(spd)) return false;
    }
  } else {
    for (int a = from; a >= to; a--) {
      setServo(channel, a);
      if (!waitMs(spd)) return false;
    }
  }
  return true;
}

// ---------------- serial command handling ----------------
void readSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      lineBuf.trim();
      if (lineBuf.length() > 0) handleCommand(lineBuf);
      lineBuf = "";
    } else {
      lineBuf += c;
      if (lineBuf.length() > 32) lineBuf = "";  // safety on garbage
    }
  }
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  // Diagnostic: "T <channel> <angle>" moves one servo directly (for mapping).
  if (cmd.startsWith("T ")) {
    int s1 = cmd.indexOf(' ');
    int s2 = cmd.indexOf(' ', s1 + 1);
    if (s2 > 0) {
      int ch = cmd.substring(s1 + 1, s2).toInt();
      int ang = cmd.substring(s2 + 1).toInt();
      if (ch >= 0 && ch <= 15) setServo(ch, ang);
    }
    return;
  }

  if (cmd == "STOP") {
    pendingMode = MODE_IDLE;
    abortFlag = true;
  } else if (cmd == "IDLE") {
    pendingMode = MODE_IDLE;
    abortFlag = true;
  } else if (cmd == "PLAY TALK") {
    pendingMode = MODE_TALK;
    abortFlag = true;  // break any running animation so talk starts fresh
  } else if (cmd == "PLAY BYE") {
    pendingMode = MODE_BYE;
    abortFlag = true;
  } else if (cmd == "PLAY HELLO") {
    pendingMode = MODE_HELLO;
    abortFlag = true;
  } else if (cmd == "PLAY WALK") {
    pendingMode = MODE_WALK;
    abortFlag = true;
  }
}

// ---------------- poses / animations ----------------
void restPosition() {
  // HEAD
  setServo(HEAD, 180);

  // RIGHT
  setServo(R_SHOULDER_FB, 90);
  setServo(R_SHOULDER_UD, 90);
  setServo(R_BICEP, 40);
  setServo(R_ELBOW, 90);

  // LEFT
  setServo(L_SHOULDER_FB, 90);
  setServo(L_SHOULDER_UD, 60);
  setServo(L_BICEP, 90);
  setServo(L_ELBOW, 90);

  // LEGS (walk standing pose; CH13/14 feet left untouched)
  curLT = L_THIGH_CENTER;
  curRT = R_THIGH_CENTER;
  curLK = L_KNEE_STRAIGHT;
  curRK = R_KNEE_STRAIGHT;
  curRA = R_ARM_CENTER;
  curLA = L_ARM_CENTER;
  setServo(L_THIGH, curLT);
  setServo(R_THIGH, curRT);
  setServo(L_KNEE, curLK);
  setServo(R_KNEE, curRK);
}

// One full talking sequence. Returns early if aborted.
void talk() {
  // 1. RIGHT DEEP FOLD
  if (!moveServo(R_ELBOW, 90, 30, 5)) return;
  if (!moveServo(R_ELBOW, 30, 90, 5)) return;
  if (!waitMs(120)) return;

  // 2. LEFT DEEP FOLD
  if (!moveServo(L_ELBOW, 90, 150, 5)) return;
  if (!moveServo(L_ELBOW, 150, 90, 5)) return;
  if (!waitMs(120)) return;

  // 3. RIGHT DEEP x2
  if (!moveServo(R_ELBOW, 90, 25, 4)) return;
  if (!moveServo(R_ELBOW, 25, 90, 4)) return;
  if (!moveServo(R_ELBOW, 90, 35, 4)) return;
  if (!moveServo(R_ELBOW, 35, 90, 4)) return;
  if (!waitMs(150)) return;

  // 4. LEFT DEEP x2
  if (!moveServo(L_ELBOW, 90, 155, 4)) return;
  if (!moveServo(L_ELBOW, 155, 90, 4)) return;
  if (!moveServo(L_ELBOW, 90, 145, 4)) return;
  if (!moveServo(L_ELBOW, 145, 90, 4)) return;
  if (!waitMs(150)) return;

  // 5. BOTH ARMS DEEP
  if (!moveServo(R_ELBOW, 90, 30, 5)) return;
  if (!moveServo(L_ELBOW, 90, 150, 5)) return;
  if (!waitMs(150)) return;
  if (!moveServo(R_ELBOW, 30, 90, 5)) return;
  if (!moveServo(L_ELBOW, 150, 90, 5)) return;

  // 6. RIGHT VERY DEEP GESTURE
  if (!moveServo(R_ELBOW, 90, 20, 4)) return;
  if (!moveServo(R_ELBOW, 20, 60, 4)) return;
  if (!moveServo(R_ELBOW, 60, 25, 4)) return;
  if (!moveServo(R_ELBOW, 25, 90, 5)) return;
  if (!waitMs(150)) return;

  // 7. LEFT VERY DEEP GESTURE
  if (!moveServo(L_ELBOW, 90, 160, 4)) return;
  if (!moveServo(L_ELBOW, 160, 120, 4)) return;
  if (!moveServo(L_ELBOW, 120, 155, 4)) return;
  if (!moveServo(L_ELBOW, 155, 90, 5)) return;
  if (!waitMs(150)) return;

  // 8. HEAD LOOK + RIGHT ELBOW
  if (!moveServo(HEAD, 180, 155, 10)) return;
  if (!moveServo(R_ELBOW, 90, 25, 5)) return;
  if (!moveServo(R_ELBOW, 25, 90, 5)) return;
  if (!moveServo(HEAD, 155, 180, 10)) return;

  // 9. LEFT ELBOW BIG TALK
  if (!moveServo(L_ELBOW, 90, 155, 4)) return;
  if (!moveServo(L_ELBOW, 155, 110, 4)) return;
  if (!moveServo(L_ELBOW, 110, 160, 4)) return;
  if (!moveServo(L_ELBOW, 160, 90, 5)) return;
  if (!waitMs(150)) return;

  // 10. BOTH DEEP ALTERNATING
  if (!moveServo(R_ELBOW, 90, 25, 4)) return;
  if (!moveServo(L_ELBOW, 90, 155, 4)) return;
  if (!moveServo(R_ELBOW, 25, 55, 4)) return;
  if (!moveServo(L_ELBOW, 155, 120, 4)) return;
  if (!moveServo(R_ELBOW, 55, 20, 4)) return;
  if (!moveServo(L_ELBOW, 120, 160, 4)) return;
  if (!moveServo(R_ELBOW, 20, 90, 5)) return;
  if (!moveServo(L_ELBOW, 160, 90, 5)) return;

  // 11. BICEP ACCENTS
  if (!moveServo(R_BICEP, 40, 55, 8)) return;
  if (!moveServo(R_ELBOW, 90, 35, 5)) return;
  if (!moveServo(R_ELBOW, 35, 90, 5)) return;
  if (!moveServo(R_BICEP, 55, 40, 8)) return;

  if (!moveServo(L_BICEP, 90, 105, 8)) return;
  if (!moveServo(L_ELBOW, 90, 145, 5)) return;
  if (!moveServo(L_ELBOW, 145, 90, 5)) return;
  if (!moveServo(L_BICEP, 105, 90, 8)) return;

  // 12. FINAL BIG GESTURE
  if (!moveServo(R_ELBOW, 90, 20, 4)) return;
  if (!moveServo(L_ELBOW, 90, 160, 4)) return;
  if (!waitMs(150)) return;
  if (!moveServo(R_ELBOW, 20, 90, 5)) return;
  if (!moveServo(L_ELBOW, 160, 90, 5)) return;
  if (!waitMs(300)) return;
}

// One-shot wave, shared by both bye and hello.
//   CH3 shoulder up/down : 90 rest -> 155 up
//   CH1 shoulder f/b     : 90 neutral (untouched)
//   CH5 bicep            : 40 rest -> 110 lifts the hand
//   CH7 elbow            : 90 rest; 0 = up, 35 = down (the wave)
void wave() {
  const int SH_REST = 90;
  const int SH_UP = 155;
  const int BIC_REST = 40;
  const int BIC_UP = 110;
  const int EL_REST = 90;
  const int EL_UP = 0;
  const int EL_DOWN = 35;

  // 1. Raise the arm: shoulder up + bicep up.
  if (!moveServo(R_SHOULDER_UD, SH_REST, SH_UP, 6)) return;
  if (!moveServo(R_BICEP, BIC_REST, BIC_UP, 8)) return;
  if (!waitMs(200)) return;

  // 2. Wave the hand: elbow swings down <-> up.
  if (!moveServo(R_ELBOW, EL_REST, EL_DOWN, 6)) return;
  for (int i = 0; i < 5; i++) {
    if (!moveServo(R_ELBOW, EL_DOWN, EL_UP, 6)) return;
    if (!moveServo(R_ELBOW, EL_UP, EL_DOWN, 6)) return;
  }

  // 3. Return everything to rest.
  if (!moveServo(R_ELBOW, EL_DOWN, EL_REST, 6)) return;
  if (!moveServo(R_BICEP, BIC_UP, BIC_REST, 8)) return;
  if (!moveServo(R_SHOULDER_UD, SH_UP, SH_REST, 6)) return;
  waitMs(150);
}

// Smooth multi-servo move (walk-v2 S-curve). Returns false if aborted.
bool moveTo(int nLT, int nRT, int nLK, int nRK, int nRA, int nLA) {
  const int steps = 50;
  for (int i = 0; i <= steps; i++) {
    float t = (float)i / steps;
    float s = t * t * t * (t * (t * 6.0 - 15.0) + 10.0);  // smootherstep
    setServo(L_THIGH, curLT + (nLT - curLT) * s);
    setServo(R_THIGH, curRT + (nRT - curRT) * s);
    setServo(L_KNEE, curLK + (nLK - curLK) * s);
    setServo(R_KNEE, curRK + (nRK - curRK) * s);
    setServo(R_SHOULDER_FB, curRA + (nRA - curRA) * s);
    setServo(L_SHOULDER_FB, curLA + (nLA - curLA) * s);
    if (!waitMs(5)) return false;
  }
  curLT = nLT; curRT = nRT; curLK = nLK; curRK = nRK; curRA = nRA; curLA = nLA;
  return true;
}

// One-shot walk: two gait cycles, then back to the standing pose.
bool walk() {
  for (int cycle = 0; cycle < 2; cycle++) {
    // Step 1: left leg forward, right leg back; opposite arms.
    if (!moveTo(30, 55, 150, 90, 120, 150)) return false;
    // Step 2: right leg forward, left leg back; opposite arms.
    if (!moveTo(110, 120, 45, 25, 60, 30)) return false;
  }
  // Return to standing.
  if (!moveTo(L_THIGH_CENTER, R_THIGH_CENTER, L_KNEE_STRAIGHT, R_KNEE_STRAIGHT,
              R_ARM_CENTER, L_ARM_CENTER)) return false;
  return true;
}

// ---------------- mode helpers ----------------
void enterIdle() {
  mode = MODE_IDLE;
  restPosition();
}

void applyPending() {
  if (pendingMode == MODE_NONE) return;
  mode = pendingMode;
  pendingMode = MODE_NONE;
  abortFlag = false;
  if (mode == MODE_IDLE) enterIdle();
}

// ---------------- setup / loop ----------------
void setup() {
  Serial.begin(115200);

  pwm.begin();
  pwm.setPWMFreq(50);
  delay(500);

  restPosition();
  Serial.println("READY");
}

void loop() {
  readSerial();
  applyPending();

  if (mode == MODE_TALK) {
    talk();
    if (pendingMode != MODE_NONE) {
      applyPending();
    } else if (abortFlag) {
      abortFlag = false;
      enterIdle();
    }
    // otherwise: finished one cycle -> loop again (continuous talk)
  } else if (mode == MODE_BYE) {
    wave();
    if (pendingMode != MODE_NONE) {
      applyPending();
    } else {
      if (!abortFlag) Serial.println("DONE bye");
      abortFlag = false;
      enterIdle();
    }
  } else if (mode == MODE_HELLO) {
    wave();
    if (pendingMode != MODE_NONE) {
      applyPending();
    } else {
      if (!abortFlag) Serial.println("DONE hello");
      abortFlag = false;
      enterIdle();
    }
  } else if (mode == MODE_WALK) {
    walk();
    if (pendingMode != MODE_NONE) {
      applyPending();
    } else {
      if (!abortFlag) Serial.println("DONE walk");
      abortFlag = false;
      enterIdle();
    }
  }
}
