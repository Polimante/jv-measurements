/*
 * Shutter controller for the J-V Measurements software.
 *
 * Works with the "Serial / Arduino (text commands)" backend
 * (Instrument -> Shutter Control...). Defaults that match this sketch:
 *
 *     Serial port .......... the COM port the board enumerated as
 *     Baud rate ............ 9600
 *     Open command ......... OPEN
 *     Close command ........ CLOSE
 *     Line ending .......... LF
 *     State query .......... STATE      (optional but recommended)
 *     Reply meaning 'open' . OPEN
 *     Settling time ........ your shutter's travel time, in ms
 *
 * The STATE query is what turns an open-loop command into a checked one: with
 * it configured, the software refuses to carry on when the controller reports a
 * state it did not ask for. Leave it blank if your hardware cannot answer.
 *
 * WIRING
 * ------
 * SHUTTER_PIN drives whatever actuates your shutter. An Arduino pin sources
 * 20 mA at 5 V, which is enough for an opto-isolator or a small relay-module
 * input, and NOT enough for a solenoid or a relay coil directly — use a driver
 * board. If your shutter has a TTL input (like the ABET Sun 2000's SHUTTER
 * INPUT BNC in EXTERNAL mode), the pin can drive it directly through a series
 * resistor, with grounds tied together.
 *
 * FAIL-SAFE
 * ---------
 * setup() closes the shutter before anything else, so a power cycle or a reset
 * (which happens whenever the serial port is opened) leaves the cell dark. If
 * your interface inverts the signal, set ACTIVE_HIGH to false rather than
 * swapping the OPEN and CLOSE commands — that keeps the fail-safe correct.
 */

const int  SHUTTER_PIN = 8;
const bool ACTIVE_HIGH = true;   // false for an inverting interface

bool shutterOpen = false;

void applyShutter(bool wantOpen) {
  shutterOpen = wantOpen;
  digitalWrite(SHUTTER_PIN, (wantOpen == ACTIVE_HIGH) ? HIGH : LOW);
}

void setup() {
  pinMode(SHUTTER_PIN, OUTPUT);
  applyShutter(false);           // closed before we are ready to be told otherwise
  Serial.begin(9600);
}

void loop() {
  if (!Serial.available()) {
    return;
  }

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "OPEN") {
    applyShutter(true);
    Serial.println("OPEN");
  } else if (cmd == "CLOSE") {
    applyShutter(false);
    Serial.println("CLOSED");
  } else if (cmd == "STATE") {
    Serial.println(shutterOpen ? "OPEN" : "CLOSED");
  } else if (cmd.length() > 0) {
    Serial.print("ERR ");
    Serial.println(cmd);
  }
}
