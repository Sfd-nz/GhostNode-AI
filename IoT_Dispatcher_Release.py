import paho.mqtt.client as mqtt
import json
import requests
import os
import threading
import time
from dotenv import load_dotenv

# ==========================================
# 1. CONFIGURATION & ENV VARIABLES
# ==========================================
load_dotenv()

BROKER_IP = os.getenv("BROKER_IP", "127.0.0.1")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

OLLAMA_GENERATE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat").replace("/chat", "/generate")
CODER_MODEL = os.getenv("CODER_MODEL", "qwen2.5-coder:7b")

HELTEC_NODE_ID_DEC = int(os.getenv("HELTEC_NODE_ID_DEC", "0"))
HELTEC_HEX_ID = "!" + hex(HELTEC_NODE_ID_DEC)[2:]
ROOT_TOPIC = os.getenv("LISTEN_TOPIC", "msh/2/#").split("/#")[0]
RADIO_PUBLISH_TOPIC = f"{ROOT_TOPIC}/json/mqtt/{HELTEC_HEX_ID}"

PRIMARY_CHANNEL = int(os.getenv("ALLOWED_AI_CHANNELS", "2").split(",")[0])
LISTEN_TOPIC = "ghostnode/iot/requests"
TELEMETRY_TOPIC = "ghostnode/iot/telemetry"
PUBLISH_TOPIC_BASIC = "ghostnode/iot/basic"

# ==========================================
# 2. THE SYSTEM PROMPT
# ==========================================
PROMPT_BASIC = """You are a headless IoT routing engine controlling tactical hardware.
Convert natural language into strict JSON. DO NOT output conversational text.

RULES:
1. Extract the specific device name as 'node_id'. If no specific device is named, use 'all'.
2. Identify the 'target' (e.g., led, relay, pan_servo, temperature_sensor).
3. For the 'action' field, use ONLY these verbs: "ON", "OFF", "OPEN", "CLOSE", "SET", "MOVE", or "READ".
4. If the user specifies a number, angle, or percentage, include it in a 'value' field (as an integer). 
5. If the user asks for data or status (e.g., "what is the temperature", "check the sensor"), the action MUST be "READ".

Example: "what is the temperature at node charlie?"
{"node_id": "charlie", "target": "temperature_sensor", "action": "READ"}
"""

# ==========================================
# 3. TELEMETRY AGGREGATOR (THE BUFFER)
# ==========================================
telemetry_buffer = []
buffer_timer = None

def flush_telemetry_buffer(mqtt_client):
    global telemetry_buffer
    if not telemetry_buffer:
        return
        
    # Combine all caught sensor readings into one string
    combined_text = "[SENSORS] " + " | ".join(telemetry_buffer)
    telemetry_buffer.clear()
    
    # --- CHUNKING LOGIC FOR LORA LIMITS ---
    max_chunk_length = 190
    words = combined_text.split()
    chunks, current_chunk = [], ""
    
    for word in words:
        if len(current_chunk) + len(word) + 1 > max_chunk_length:
            if current_chunk: chunks.append(current_chunk)
            current_chunk = word
        else:
            current_chunk = current_chunk + " " + word if current_chunk else word
    if current_chunk: chunks.append(current_chunk)

    total_chunks = len(chunks)
    for i, chunk in enumerate(chunks):
        final_text = f"{chunk} ({i+1}/{total_chunks})" if total_chunks > 1 else chunk
        
        reply_payload = {
            "channel": PRIMARY_CHANNEL,  
            "from": HELTEC_NODE_ID_DEC, 
            "type": "sendtext",
            "payload": final_text
        }
        mqtt_client.publish(RADIO_PUBLISH_TOPIC, json.dumps(reply_payload), retain=True)
        print(f"[📡] Broadcasted Aggregated Telemetry: {final_text}")
        
        # Duty cycle wait if we have multiple chunks to send
        if total_chunks > 1 and i < total_chunks - 1:
            time.sleep(12) 

# ==========================================
# 4. ASK OLLAMA
# ==========================================
def translate_to_json(user_command):
    clean_command = user_command.lower().replace("!action", "").strip()
    send_via_lora = False
    if clean_command.startswith("lora"):
        send_via_lora = True
        clean_command = clean_command[4:].strip()
    
    print(f"[🧠] Spinning up {CODER_MODEL} for command: '{clean_command}'")
    full_prompt = f"{PROMPT_BASIC}\n\nUser Command: {clean_command}\nOutput strictly valid JSON:"

    payload = {
        "model": CODER_MODEL, "prompt": full_prompt, 
        "stream": False, "format": "json", "keep_alive": 0        
    }
    try:
        response = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=60)
        response.raise_for_status()
        return response.json().get("response", "").strip(), PUBLISH_TOPIC_BASIC, send_via_lora
    except Exception as e:
        print(f"[!] Ollama generation error: {e}")
        return None, None, False

# ==========================================
# 5. MQTT EVENT PROCESSOR
# ==========================================
def process_request(text, client):
    json_payload, base_topic, send_via_lora = translate_to_json(text)
    
    if json_payload and base_topic:
        try:
            parsed_check = json.loads(json_payload)
            raw_node_id = parsed_check.get("node_id", "all")
            safe_node_id = str(raw_node_id).lower().strip().replace(" ", "_")
            dynamic_target_topic = f"{base_topic}/{safe_node_id}"
            
            client.publish(dynamic_target_topic, json_payload)

            if send_via_lora:
                minified_json = json.dumps(parsed_check, separators=(',', ':'))
                lora_command_string = f"!C2:{dynamic_target_topic}:{minified_json}"
                lora_cmd_payload = {"channel": PRIMARY_CHANNEL, "from": HELTEC_NODE_ID_DEC, "type": "sendtext", "payload": lora_command_string}
                client.publish(RADIO_PUBLISH_TOPIC, json.dumps(lora_cmd_payload), retain=True)

            action_type = parsed_check.get("action", "").upper()
            if action_type not in ["READ", "GET", "CHECK"]:
                time.sleep(8) 
                target_device = parsed_check.get("target", "device").upper()
                confirmation_text = f"[DISPATCH] {safe_node_id.upper()} {target_device} -> {action_type}"
                reply_payload = {"channel": PRIMARY_CHANNEL, "from": HELTEC_NODE_ID_DEC, "type": "sendtext", "payload": confirmation_text}
                client.publish(RADIO_PUBLISH_TOPIC, json.dumps(reply_payload), retain=True)

        except json.JSONDecodeError:
            print(f"[❌] AI failed to generate valid JSON. Dropping payload.")

# ==========================================
# 6. THE MAIN LISTENER LOOP
# ==========================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[+] Dispatcher connected to Broker at {BROKER_IP}")
        client.subscribe(LISTEN_TOPIC)
        client.subscribe(TELEMETRY_TOPIC)
        print(f"[+] Listening strictly on {LISTEN_TOPIC} for Basic IoT handoffs...")
        print(f"[+] Listening strictly on {TELEMETRY_TOPIC} for returning sensor data...")

def on_message(client, userdata, msg):
    topic = msg.topic
    incoming_text = msg.payload.decode("utf-8")

    # --- CATCH TELEMETRY AND ADD TO BUFFER ---
    if topic == TELEMETRY_TOPIC:
        try:
            telemetry_data = json.loads(incoming_text)
            node = telemetry_data.get("node_id", "UNKNOWN").upper()
            value = telemetry_data.get("value", "N/A")
            
            # Create a tiny string like "ALPHA: 22.5"
            mini_text = f"{node}: {value}"
            
            global telemetry_buffer, buffer_timer
            telemetry_buffer.append(mini_text)
            
            # Reset the 3-second flush timer every time a new reading comes in
            if buffer_timer:
                buffer_timer.cancel()
            buffer_timer = threading.Timer(3.0, flush_telemetry_buffer, args=[client])
            buffer_timer.start()
            
            print(f"[⏱️] Added {node} to telemetry buffer. Waiting for others...")
        except Exception as e:
            print(f"[!] Failed to route telemetry to buffer: {e}")
        return 

    # --- OUTBOUND PATH: ROUTE COMMANDS TO QWEN ---
    if topic == LISTEN_TOPIC:
        threading.Thread(target=process_request, args=(incoming_text, client)).start()

# ==========================================
# START ENGINE
# ==========================================
if __name__ == "__main__":
    print(f"=== Starting KINETIC IoT Dispatcher ({CODER_MODEL}) ===")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(BROKER_IP, BROKER_PORT, 60)
        client.loop_forever()
    except Exception as e:
        print(f"[!] Could not connect to MQTT Broker. Check .env. Error: {e}")
