#include <Arduino.h>

struct RadioState {
  const char* name;
  uint8_t pinPtt;
  uint8_t pinAudio;
  bool pttActiveHigh;

  String currentText;
  int currentWpm;
  int currentTone;

  bool beaconEnabled;
  unsigned long beaconIntervalMs;
  unsigned long nextBeaconAt;

  bool sendingNow;
  bool stopRequested;
};

RadioState vhf = {
  "VHF",
  6,    // PTT VHF
  7,    // AUDIO VHF
  false, // cambiar a false si queda invertido
  "CQ CQ DE IVAN",
  15,
  700,
  false,
  60000,
  0,
  false,
  false
};

RadioState uhf = {
  "UHF",
  8,    // PTT UHF
  9,    // AUDIO UHF
  false, // cambiar a false si queda invertido
  "CQ CQ DE IVAN",
  15,
  700,
  false,
  60000,
  0,
  false,
  false
};

String rxLine = "";

unsigned int dotMsFromWpm(int wpm) {
  if (wpm < 5) wpm = 5;
  if (wpm > 60) wpm = 60;
  return 1200 / wpm;
}

int clampTone(int hz) {
  if (hz < 200) hz = 200;
  if (hz > 2000) hz = 2000;
  return hz;
}

void setPtt(RadioState &r, bool on) {
  bool level = r.pttActiveHigh ? on : !on;
  digitalWrite(r.pinPtt, level ? HIGH : LOW);
}

void audioOn(RadioState &r, int freq) {
  tone(r.pinAudio, clampTone(freq));
}

void audioOff(RadioState &r) {
  noTone(r.pinAudio);
}

void toneKeyDown(RadioState &r, int freq) {
  audioOn(r, freq);
}

void toneKeyUp(RadioState &r) {
  audioOff(r);
}

void txStart(RadioState &r) {
  setPtt(r, true);
  delay(120);
}

void txStop(RadioState &r) {
  audioOff(r);
  delay(80);
  setPtt(r, false);
}

const char* morseFor(char c) {
  switch (toupper(c)) {
    case 'A': return ".-";
    case 'B': return "-...";
    case 'C': return "-.-.";
    case 'D': return "-..";
    case 'E': return ".";
    case 'F': return "..-.";
    case 'G': return "--.";
    case 'H': return "....";
    case 'I': return "..";
    case 'J': return ".---";
    case 'K': return "-.-";
    case 'L': return ".-..";
    case 'M': return "--";
    case 'N': return "-.";
    case 'O': return "---";
    case 'P': return ".--.";
    case 'Q': return "--.-";
    case 'R': return ".-.";
    case 'S': return "...";
    case 'T': return "-";
    case 'U': return "..-";
    case 'V': return "...-";
    case 'W': return ".--";
    case 'X': return "-..-";
    case 'Y': return "-.--";
    case 'Z': return "--..";
    case '1': return ".----";
    case '2': return "..---";
    case '3': return "...--";
    case '4': return "....-";
    case '5': return ".....";
    case '6': return "-....";
    case '7': return "--...";
    case '8': return "---..";
    case '9': return "----.";
    case '0': return "-----";
    case '/': return "-..-.";
    case '?': return "..--..";
    case '.': return ".-.-.-";
    case ',': return "--..--";
    case '=': return "-...-";
    case '+': return ".-.-.";
    case '-': return "-....-";
    default:  return nullptr;
  }
}

RadioState* getRadioByName(String s) {
  s.trim();
  s.toUpperCase();
  if (s == "VHF") return &vhf;
  if (s == "UHF") return &uhf;
  return nullptr;
}

void printStatusOne(RadioState &r) {
  Serial.print(r.name);
  Serial.print("|STATUS|PTT=");
  Serial.print(digitalRead(r.pinPtt) ? "1" : "0");
  Serial.print("|BEACON=");
  Serial.print(r.beaconEnabled ? "1" : "0");
  Serial.print("|TEXT=");
  Serial.print(r.currentText);
  Serial.print("|WPM=");
  Serial.print(r.currentWpm);
  Serial.print("|TONE=");
  Serial.print(r.currentTone);
  Serial.print("|INTERVAL_MS=");
  Serial.println(r.beaconIntervalMs);
}

void printStatusAll() {
  printStatusOne(vhf);
  printStatusOne(uhf);
}

void stopRadioNow(RadioState &r) {
  r.stopRequested = true;
  r.beaconEnabled = false;
  audioOff(r);
  setPtt(r, false);
  r.sendingNow = false;
  Serial.println(String(r.name) + "|STOPPED");
}

bool serviceIncomingDuringDelay(RadioState &r, unsigned long totalMs) {
  unsigned long start = millis();

  while (millis() - start < totalMs) {
    while (Serial.available()) {
      char ch = Serial.read();

      if (ch == '\n' || ch == '\r') {
        if (rxLine.length() > 0) {
          String line = rxLine;
          rxLine = "";
          line.trim();

          if (line == "STATUS") {
            printStatusAll();
          } else if (line == "PING") {
            Serial.println("DUAL|PONG");
          } else if (line == "STOP|BOTH") {
            stopRadioNow(vhf);
            stopRadioNow(uhf);
          } else if (line == String("STOP|") + r.name) {
            stopRadioNow(r);
            return true;
          }
        }
      } else {
        rxLine += ch;
      }
    }

    if (r.stopRequested) return true;
    delay(1);
  }

  return false;
}

bool sendMorseText(RadioState &r, const String &text, int wpm, int freq) {
  r.sendingNow = true;
  r.stopRequested = false;

  wpm = constrain(wpm, 5, 60);
  freq = clampTone(freq);

  unsigned int dot = dotMsFromWpm(wpm);
  unsigned int dash = dot * 3;
  unsigned int intra = dot;
  unsigned int letterGap = dot * 3;
  unsigned int wordGap = dot * 7;

  Serial.println(String(r.name) + "|TX_BEGIN|" + text + "|WPM=" + String(wpm) + "|TONE=" + String(freq));

  txStart(r);

  for (unsigned int i = 0; i < text.length(); i++) {
    if (r.stopRequested) break;

    char c = text[i];

    if (c == ' ') {
      if (serviceIncomingDuringDelay(r, wordGap)) break;
      continue;
    }

    const char* code = morseFor(c);
    if (!code) {
      if (serviceIncomingDuringDelay(r, letterGap)) break;
      continue;
    }

    int len = strlen(code);
    for (int j = 0; j < len; j++) {
      if (r.stopRequested) break;

      toneKeyDown(r, freq);

      if (code[j] == '.') {
        if (serviceIncomingDuringDelay(r, dot)) break;
      } else {
        if (serviceIncomingDuringDelay(r, dash)) break;
      }

      toneKeyUp(r);

      if (j < len - 1) {
        if (serviceIncomingDuringDelay(r, intra)) break;
      }
    }

    if (r.stopRequested) break;

    if (i < text.length() - 1 && text[i + 1] != ' ') {
      if (serviceIncomingDuringDelay(r, letterGap - intra)) break;
    }
  }

  txStop(r);
  r.sendingNow = false;

  if (r.stopRequested) {
    Serial.println(String(r.name) + "|TX_ABORT");
    return false;
  } else {
    Serial.println(String(r.name) + "|TX_END");
    return true;
  }
}

// Devuelve true si s parece entero positivo
bool looksNumeric(const String &s) {
  if (s.length() == 0) return false;
  for (unsigned int i = 0; i < s.length(); i++) {
    if (!isDigit(s[i])) return false;
  }
  return true;
}

void handlePtt(String line) {
  int p1 = line.indexOf('|');
  int p2 = line.indexOf('|', p1 + 1);
  if (p1 < 0 || p2 < 0) {
    Serial.println("ERR|BAD_PTT");
    return;
  }

  String radioName = line.substring(p1 + 1, p2);
  String state = line.substring(p2 + 1);

  RadioState* r = getRadioByName(radioName);
  if (!r) {
    Serial.println("ERR|BAD_RADIO");
    return;
  }

  state.trim();
  state.toUpperCase();

  if (state == "ON") {
    setPtt(*r, true);
    Serial.println(String(r->name) + "|PTT|ON");
  } else if (state == "OFF") {
    audioOff(*r);
    setPtt(*r, false);
    Serial.println(String(r->name) + "|PTT|OFF");
  } else {
    Serial.println(String(r->name) + "|ERR|BAD_PTT_STATE");
  }
}

void handleSend(String line) {
  // Backend actual:
  // SEND|VHF|15|700|CQ CQ DE IVAN
  //
  // Formato viejo soportado también:
  // SEND|VHF|CQ CQ DE IVAN|15|700

  int p1 = line.indexOf('|');
  int p2 = line.indexOf('|', p1 + 1);
  int p3 = line.indexOf('|', p2 + 1);
  int p4 = line.indexOf('|', p3 + 1);

  if (p1 < 0 || p2 < 0 || p3 < 0 || p4 < 0) {
    Serial.println("ERR|BAD_SEND");
    return;
  }

  String radioName = line.substring(p1 + 1, p2);
  RadioState* r = getRadioByName(radioName);
  if (!r) {
    Serial.println("ERR|BAD_RADIO");
    return;
  }

  String f1 = line.substring(p2 + 1, p3);
  String f2 = line.substring(p3 + 1, p4);
  String f3 = line.substring(p4 + 1);

  f1.trim();
  f2.trim();
  f3.trim();

  String text;
  int wpm = r->currentWpm;
  int toneHz = r->currentTone;

  if (looksNumeric(f1) && looksNumeric(f2)) {
    // Nuevo formato: SEND|RADIO|WPM|TONE|TEXT
    wpm = f1.toInt();
    toneHz = f2.toInt();
    text = f3;
  } else {
    // Formato viejo: SEND|RADIO|TEXT|WPM|TONE
    text = f1;
    wpm = f2.toInt();
    toneHz = f3.toInt();
  }

  text.trim();
  if (text.length() == 0) {
    Serial.println(String(r->name) + "|ERR|EMPTY_TEXT");
    return;
  }

  if (wpm <= 0) wpm = 15;
  if (toneHz <= 0) toneHz = 700;

  r->currentText = text;
  r->currentWpm = constrain(wpm, 5, 60);
  r->currentTone = clampTone(toneHz);

  Serial.println(String(r->name) + "|SEND_PARSED|TEXT=" + r->currentText + "|WPM=" + String(r->currentWpm) + "|TONE=" + String(r->currentTone));
  sendMorseText(*r, r->currentText, r->currentWpm, r->currentTone);
}

void handleBeacon(String line) {
  // Backend actual:
  // BEACON|VHF|ON|60000|15|700|CQ CQ DE IVAN
  // BEACON|VHF|OFF
  //
  // Formato viejo soportado:
  // BEACON|VHF|ON|CQ CQ DE IVAN|15|700|60000

  int p1 = line.indexOf('|');
  int p2 = line.indexOf('|', p1 + 1);
  int p3 = line.indexOf('|', p2 + 1);

  if (p1 < 0 || p2 < 0 || p3 < 0) {
    Serial.println("ERR|BAD_BEACON");
    return;
  }

  String radioName = line.substring(p1 + 1, p2);
  String mode = line.substring(p2 + 1, p3);

  radioName.trim();
  radioName.toUpperCase();
  mode.trim();
  mode.toUpperCase();

  if (radioName == "BOTH" && mode == "OFF") {
    vhf.beaconEnabled = false;
    uhf.beaconEnabled = false;
    Serial.println("VHF|BEACON|OFF");
    Serial.println("UHF|BEACON|OFF");
    return;
  }

  RadioState* r = getRadioByName(radioName);
  if (!r) {
    Serial.println("ERR|BAD_RADIO");
    return;
  }

  if (mode == "OFF") {
    r->beaconEnabled = false;
    Serial.println(String(r->name) + "|BEACON|OFF");
    return;
  }

  if (mode != "ON") {
    Serial.println(String(r->name) + "|ERR|BAD_BEACON_MODE");
    return;
  }

  int p4 = line.indexOf('|', p3 + 1);
  int p5 = line.indexOf('|', p4 + 1);
  int p6 = line.indexOf('|', p5 + 1);
  int p7 = line.indexOf('|', p6 + 1);

  if (p4 < 0 || p5 < 0 || p6 < 0 || p7 < 0) {
    Serial.println(String(r->name) + "|ERR|BAD_BEACON");
    return;
  }

  String f1 = line.substring(p3 + 1, p4);
  String f2 = line.substring(p4 + 1, p5);
  String f3 = line.substring(p5 + 1, p6);
  String f4 = line.substring(p6 + 1, p7);
  String f5 = line.substring(p7 + 1);

  f1.trim();
  f2.trim();
  f3.trim();
  f4.trim();
  f5.trim();

  String text;
  int wpm = r->currentWpm;
  int toneHz = r->currentTone;
  unsigned long intervalMs = r->beaconIntervalMs;

  if (looksNumeric(f1) && looksNumeric(f2) && looksNumeric(f3)) {
    // Nuevo formato:
    // BEACON|RADIO|ON|INTERVAL|WPM|TONE|TEXT
    intervalMs = (unsigned long) f1.toInt();
    wpm = f2.toInt();
    toneHz = f3.toInt();
    text = f4 + "|" + f5; // fallback simple si texto tuviera pipes, aunque backend los reemplaza
    text = line.substring(p6 + 1);
    // En realidad desde p6+1 es "tone|text"; corregimos abajo
    text = line.substring(p7 + 1);
  } else {
    // Formato viejo:
    // BEACON|RADIO|ON|TEXT|WPM|TONE|INTERVAL
    text = f1;
    wpm = f2.toInt();
    toneHz = f3.toInt();
    intervalMs = (unsigned long) f4.toInt();
    if (f5.length() > 0 && looksNumeric(f5)) {
      intervalMs = (unsigned long) f5.toInt();
    }
  }

  text.trim();
  if (text.length() == 0) {
    Serial.println(String(r->name) + "|ERR|EMPTY_BEACON_TEXT");
    return;
  }

  if (wpm <= 0) wpm = 15;
  if (toneHz <= 0) toneHz = 700;
  if (intervalMs == 0) intervalMs = 60000UL;

  r->currentText = text;
  r->currentWpm = constrain(wpm, 5, 60);
  r->currentTone = clampTone(toneHz);
  r->beaconIntervalMs = intervalMs;
  r->beaconEnabled = true;
  r->nextBeaconAt = millis();

  Serial.println(String(r->name) + "|BEACON_PARSED|TEXT=" + r->currentText + "|WPM=" + String(r->currentWpm) + "|TONE=" + String(r->currentTone) + "|INTERVAL=" + String(r->beaconIntervalMs));
  Serial.println(String(r->name) + "|BEACON|ON");
}

void handleCommand(String line) {
  line.trim();
  if (line.length() == 0) return;

  if (line == "PING") {
    Serial.println("DUAL|PONG");
    return;
  }

  if (line == "STATUS") {
    printStatusAll();
    return;
  }

  if (line == "STOP|VHF") {
    stopRadioNow(vhf);
    return;
  }

  if (line == "STOP|UHF") {
    stopRadioNow(uhf);
    return;
  }

  if (line == "STOP|BOTH") {
    stopRadioNow(vhf);
    stopRadioNow(uhf);
    return;
  }

  if (line.startsWith("PTT|")) {
    handlePtt(line);
    return;
  }

  if (line.startsWith("SEND|")) {
    handleSend(line);
    return;
  }

  if (line.startsWith("BEACON|")) {
    handleBeacon(line);
    return;
  }

  Serial.println(String("ERR|UNKNOWN_CMD|") + line);
}

void setup() {
  pinMode(vhf.pinPtt, OUTPUT);
  pinMode(vhf.pinAudio, OUTPUT);
  pinMode(uhf.pinPtt, OUTPUT);
  pinMode(uhf.pinAudio, OUTPUT);

  setPtt(vhf, false);
  setPtt(uhf, false);
  audioOff(vhf);
  audioOff(uhf);

  Serial.begin(115200);
  delay(300);

  Serial.println("DUAL|BOOT");
  printStatusAll();
}

void loop() {
  while (Serial.available()) {
    char ch = Serial.read();

    if (ch == '\n' || ch == '\r') {
      if (rxLine.length() > 0) {
        String line = rxLine;
        rxLine = "";
        handleCommand(line);
      }
    } else {
      rxLine += ch;
    }
  }

  unsigned long now = millis();

  if (vhf.beaconEnabled && !vhf.sendingNow) {
    if ((long)(now - vhf.nextBeaconAt) >= 0) {
      sendMorseText(vhf, vhf.currentText, vhf.currentWpm, vhf.currentTone);
      vhf.nextBeaconAt = millis() + vhf.beaconIntervalMs;
    }
  }

  if (uhf.beaconEnabled && !uhf.sendingNow) {
    if ((long)(now - uhf.nextBeaconAt) >= 0) {
      sendMorseText(uhf, uhf.currentText, uhf.currentWpm, uhf.currentTone);
      uhf.nextBeaconAt = millis() + uhf.beaconIntervalMs;
    }
  }
}