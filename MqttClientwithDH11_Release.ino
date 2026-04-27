/*
 * GHOSTNODE FIRMWARE V1.4 (Kinetic Routing + DHT11 Telemetry)
 * Target: ESP32 D1 Mini (WROOM-32)
 * REQUIRED LIBRARIES: PubSubClient, ArduinoJson, DHT sensor library
 */

#include <WiFi.h>
#include <PubSubClient.h>      
#include <ArduinoJson.h>       
#include <DHT.h>               
#include "esp_wifi.h"          
#include "soc/soc.h"           
#include "soc/rtc_cntl_reg.h"  

// --- WI-FI SETTINGS ---
const char* ssid = "HomeWifiName";
const char* password = "HomeWifiPassword"; 

// --- GHOSTNODE MQTT SETTINGS ---
const char* mqtt_server  = "Mqtt_Ip"; 
const int   mqtt_port    = 1883;
const char* mqtt_user    = "User_Name";
const char* mqtt_pass    = "PassWord";

// --- DYNAMIC ROUTING SETTINGS ---
const char* base_topic = "ghostnode/iot/basic";
const char* telemetry_topic = "ghostnode/iot/telemetry";
const char* node_name  = "dh11node1"; // <-- CHANGE THIS FOR EACH DEVICE

// --- HARDWARE PINOUT ---
const int LED_PIN = 2; // Standard onboard blue LED 
#define DHTPIN 4       // The GPIO pin you wired the DHT11 Data line to
#define DHTTYPE DHT11  // Tell it you are using the blue DHT11
DHT dht(DHTPIN, DHTTYPE);

WiFiClient espClient;
PubSubClient client(espClient);

// --- MQTT CALLBACK: Handles JSON from Qwen ---
void callback(char* topic, byte* payload, unsigned int length) {
  Serial.print("\n[⚡] AI Payload Received on [");
  Serial.print(topic);
  Serial.println("]");
  
  String jsonString = "";
  for (int i = 0; i < length; i++) {
    jsonString += (char)payload[i];
  }
  Serial.println("Raw Qwen JSON: " + jsonString);

  DynamicJsonDocument doc(512);
  DeserializationError error = deserializeJson(doc, jsonString);

  if (error) {
    Serial.print("JSON Parse Failed: ");
    Serial.println(error.c_str());
    return; 
  }

  // Extract variables (Forcing case to match exactly)
  String target = doc["target"].as<String>();
  target.toLowerCase(); 
  
  String action = doc["action"].as<String>();
  action.toUpperCase(); 

  int parameterValue = 0;
  if (doc.containsKey("value")) {
    parameterValue = doc["value"].as<int>();
  }

  // --- KINETIC LOGIC (ON/OFF/MOVE) ---
  if (action.indexOf("ON") >= 0 || action.indexOf("OPEN") >= 0 || action.indexOf("START") >= 0) {
    digitalWrite(LED_PIN, HIGH);
    Serial.println(" -> SUCCESS: LED TURNED ON");
  } 
  else if (action.indexOf("OFF") >= 0 || action.indexOf("CLOSE") >= 0 || action.indexOf("STOP") >= 0) {
    digitalWrite(LED_PIN, LOW);
    Serial.println(" -> SUCCESS: LED TURNED OFF");
  }
  else if (action.indexOf("MOVE") >= 0 || action.indexOf("SET") >= 0 || action.indexOf("PAN") >= 0) {
    Serial.print(" -> SUCCESS: HARDWARE MOVED TO ");
    Serial.println(parameterValue);
  }

  // --- TELEMETRY LOGIC (READ/GET/CHECK) ---
  if (action.indexOf("READ") >= 0 || action.indexOf("GET") >= 0 || action.indexOf("CHECK") >= 0) {
    
    float sensorValue = 0.0;
    String sensorName = "unknown";

    if (target.indexOf("temperature") >= 0 || target.indexOf("temp") >= 0) {
      sensorValue = dht.readTemperature();
      sensorName = "temperature";
    } 
    else if (target.indexOf("humidity") >= 0 || target.indexOf("humid") >= 0) {
      sensorValue = dht.readHumidity();
      sensorName = "humidity";
    }

    // Safety Check: Did the sensor actually reply?
    if (isnan(sensorValue) || sensorName == "unknown") {
      Serial.println(" -> ERROR: Failed to read from DHT sensor! Check wiring.");
      return; 
    }
    
    // Build the JSON response payload
    String responseJson = "{\"node_id\":\"" + String(node_name) + "\", \"sensor\":\"" + sensorName + "\", \"value\":" + String(sensorValue) + "}";
    
    // Publish it back to the GhostNode Telemetry channel
    client.publish(telemetry_topic, responseJson.c_str());
    
    Serial.print(" -> SUCCESS: TELEMETRY SENT: ");
    Serial.println(responseJson);
  }
}

// --- WIFI INITIALIZATION ---
void setup_stable_wifi() {
  WiFi.mode(WIFI_STA);
  esp_wifi_set_max_tx_power(8); // Whisper Mode

  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  int timeout_counter = 0;
  while (WiFi.status() != WL_CONNECTED && timeout_counter < 40) {
    delay(500);
    Serial.print(".");
    timeout_counter++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[SUCCESS] WiFi Connected!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[ERROR] WiFi Connection Failed. Check credentials.");
  }
}

// --- MQTT RECONNECT LOGIC ---
void reconnect() {
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    String clientId = "GhostNode-" + String(random(0xffff), HEX);
    
    if (client.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
      Serial.println("CONNECTED to Broker!");
      
      String specific_topic = String(base_topic) + "/" + node_name;
      String broadcast_topic = String(base_topic) + "/all";
      
      client.subscribe(specific_topic.c_str());
      client.subscribe(broadcast_topic.c_str());
      
      Serial.print("Listening on specific: ");
      Serial.println(specific_topic);
      Serial.print("Listening on broadcast: ");
      Serial.println(broadcast_topic);
      
    } else {
      Serial.print("Failed, rc=");
      Serial.print(client.state());
      Serial.println(" - Retrying in 5 seconds...");
      delay(5000);
    }
  }
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); 
  setCpuFrequencyMhz(80); 
  
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); 
  
  delay(3000); 
  Serial.println("\n====================================");
  Serial.println(" GHOSTNODE V1.4 - KINETIC & TELEMETRY ");
  Serial.println("====================================");

  dht.begin(); // Boot up the temperature sensor

  setup_stable_wifi();
  
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();
}