/*
  AquaGuard - real ESP32 sensor node
  -----------------------------------
  Replaces one virtual node (e.g. Zone A) with real hardware. It publishes the
  SAME JSON message as sensor_nodes.py, so the detector and dashboard work unchanged.

  Hardware
    - ESP32 dev board
    - YF-S201 water flow sensor   -> signal on GPIO 27 (5V, GND)
    - 0-1.2 MPa pressure transducer (0.5-4.5 V out) -> GPIO 34 through a 2:1 voltage divider
    - HC-SR04 ultrasonic sensor on the tank (optional, water level) -> TRIG 25, ECHO 26

  Arduino libraries:  PubSubClient (Nick O'Leary), ArduinoJson (Benoit Blanchon)
  Board: "ESP32 Dev Module"
*/

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ---------- change these ----------
const char* WIFI_SSID   = "YOUR_WIFI";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST   = "192.168.1.10";   // PC running the broker (use Mosquitto listening on the network)
const int   MQTT_PORT   = 1883;
const char* MQTT_USER   = "aquaguard";      // same as config.yaml
const char* MQTT_PASS   = "aquaguard123";
const char* ZONE        = "A";
const float TANK_HEIGHT_CM = 100.0;         // empty tank: distance from sensor to bottom
// -----------------------------------

const int FLOW_PIN = 27, PRESSURE_PIN = 34, TRIG_PIN = 25, ECHO_PIN = 26;

WiFiClient wifi;
PubSubClient mqtt(wifi);
volatile unsigned long pulses = 0;
unsigned long seq = 0;
String topic, statusTopic;

void IRAM_ATTR onPulse() { pulses++; }

float readFlowLpm() {
  // YF-S201: pulse frequency (Hz) = 7.5 x flow (L/min); we count pulses for 1 second
  noInterrupts(); unsigned long count = pulses; pulses = 0; interrupts();
  return count / 7.5;
}

float readPressureBar() {
  // 0.5 V = 0 bar, 4.5 V = 12 bar; the divider halves the voltage for the 3.3 V ADC
  float volts = analogRead(PRESSURE_PIN) / 4095.0 * 3.3 * 2.0;
  return max(0.0f, (volts - 0.5f) / 4.0f * 12.0f);
}

float readLevelPercent() {
  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10); digitalWrite(TRIG_PIN, LOW);
  long echo = pulseIn(ECHO_PIN, HIGH, 30000);
  if (echo == 0) return -1;                       // no echo: reported as an impossible reading
  float distance = echo * 0.0343 / 2.0;
  return constrain(100.0 * (TANK_HEIGHT_CM - distance) / TANK_HEIGHT_CM, 0, 100);
}

void connectMqtt() {
  String willMsg = String("{\"zone\":\"") + ZONE + "\",\"online\":false}";
  while (!mqtt.connected()) {
    String id = String("NODE-") + ZONE + "-esp32";
    // Last will: the broker marks this node offline if it drops off
    if (mqtt.connect(id.c_str(), MQTT_USER, MQTT_PASS, statusTopic.c_str(), 1, true, willMsg.c_str())) {
      mqtt.publish(statusTopic.c_str(), (String("{\"zone\":\"") + ZONE + "\",\"online\":true}").c_str(), true);
    } else {
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(FLOW_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), onPulse, FALLING);
  pinMode(TRIG_PIN, OUTPUT); pinMode(ECHO_PIN, INPUT);
  topic = String("aquaguard/sensors/") + ZONE;
  statusTopic = topic + "/status";

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
}

void loop() {
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();
  delay(1000);                                    // one reading per second

  JsonDocument doc;
  doc["zone"] = ZONE;
  doc["node"] = String("NODE-") + ZONE;
  doc["seq"] = ++seq;
  doc["flow"] = readFlowLpm();
  doc["pressure"] = readPressureBar();
  doc["level"] = readLevelPercent();
  doc["battery"] = 100;                           // mains powered (add a battery monitor if needed)
  doc["rssi"] = WiFi.RSSI();
  doc["ts"] = 0;                                  // the dashboard uses its own clock if 0
  char payload[256];
  serializeJson(doc, payload);
  mqtt.publish(topic.c_str(), payload);
  Serial.println(payload);
}
