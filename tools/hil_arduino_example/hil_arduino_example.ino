/*
  Motor Test Bench - hardware-in-the-loop telemetry example.

  Prints one telemetry line per sample over USB serial in the format the
  bench's hardware bridge understands (python/motorsim_server/
  hardware_bridge.py). Connect in the app: Record tab -> Hardware ->
  enter the COM port -> Connect; the stream appears as the
  "hardware-live" run you can overlay in compare mode.

  Replace the fake readings with your real sensors: an encoder/hall
  sensor for RPM, a shunt or hall current sensor, a voltage divider for
  bus voltage, a thermistor for temperature.

  Either line format works:
    JSON:       {"rpm": 1234.0, "current": 0.82, "voltage": 11.7}
    key=value:  rpm=1234.0,current=0.82,voltage=11.7
*/

const unsigned long SAMPLE_MS = 20;   // 50 samples/s
unsigned long lastSample = 0;

void setup() {
  Serial.begin(115200);
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_MS) return;
  lastSample = now;

  // --- replace these with real sensor reads -------------------------
  float rpm     = 1500.0 + 200.0 * sin(now / 1000.0);
  float current = 0.6 + 0.1 * sin(now / 300.0);
  float voltage = 11.9;
  float tempC   = 31.5;
  // -------------------------------------------------------------------

  Serial.print("{\"t\":");
  Serial.print(now / 1000.0, 3);
  Serial.print(",\"rpm\":");
  Serial.print(rpm, 1);
  Serial.print(",\"current\":");
  Serial.print(current, 3);
  Serial.print(",\"voltage\":");
  Serial.print(voltage, 2);
  Serial.print(",\"temperature\":");
  Serial.print(tempC, 1);
  Serial.println("}");
}
