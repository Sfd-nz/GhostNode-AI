import paho.mqtt.client as mqtt
import json
import requests
import os
import threading
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

# --- RADIO FEEDBACK CONFIG ---
HELTEC_NODE_ID_DEC = int(os.getenv("HELTEC_NODE_ID_DEC", "0"))
HELTEC_HEX_ID = "!" + hex(HELTEC_NODE_ID_DEC)[2:]
ROOT_TOPIC = os.getenv("LISTEN_TOPIC", "msh/2/#").split("/#")[0]
RADIO_PUBLISH_TOPIC = f"{ROOT_TOPIC}/json/mqtt/{HELTEC_HEX_ID}"

LISTEN_TOPIC = "ghostnode/iot/requests"

# --- THE FORKED TOPICS ---
PUBLISH_TOPIC_BASIC = "ghostnode/iot/basic"  # For dumb Arduino/MQTT nodes
PUBLISH_TOPIC_CLAW = "ghostnode/iot/claw"    # For smart ESP-Claw nodes

# ==========================================
# 2. THE DUAL SYSTEM PROMPTS
# ==========================================

# Prompt 1: For your lightweight custom scripts (Arduino)
PROMPT_BASIC = """You are a headless IoT routing engine controlling basic MQTT hardware.
Convert natural language into strict JSON. DO NOT output conversational text.
Your hardware expects simple toggles.
Example:
{
  "target": "gate_relay",
  "action": "open"
}
"""

# Prompt 2: For your heavy ESP-Claw framework nodes
PROMPT_CLAW = """You are an advanced AI agent controlling an ESP-Claw edge node.
Convert natural language into strict JSON. DO NOT output conversational text.
You MUST adhere strictly to the following hardware capabilities. Do not invent parameters.

HARDWARE PROFILE: "robotic_head"
- description: A 2-axis servo mount.
- allowed actions: "move_to", "sweep", "center"
- parameters for "move_to": 
    "pan_angle" (integer 0-180, where 90 is center, 180 is right)
    "tilt_angle" (integer 0-180, where 90 is center, 180 is up)
- parameters for "sweep":
    "axis" (string: "pan", "tilt", or "both")
    "speed" (integer 1-10)

Example Request: "look to your top right"
Example JSON:
{
  "target": "robotic_head",
  "action": "move_to",
  "parameters": {
    "pan_angle": 180,
    "tilt_angle": 180
  }
}

Example Request: "sweep left and right"
Example JSON:
{
  "target": "robotic_head",
  "action": "sweep",
  "parameters": {
    "axis": "pan",
    "speed": 5
  }
}
"""

# ==========================================
# 3. ASK OLLAMA (THE CODER BRAIN)
# ==========================================
def translate_to_json(user_command):
    # Strip the base "!action" trigger
    clean_command = user_command.lower().replace("!action", "").strip()
    
    # --- THE ROUTING LOGIC ---
    if clean_command.startswith("edge") or clean_command.startswith("claw"):
        # It's a Smart Node command!
        target_topic = PUBLISH_TOPIC_CLAW
        active_prompt = PROMPT_CLAW
        # Remove the trigger word so the AI just sees the command
        clean_command = clean_command.replace("edge", "").replace("claw", "").strip()
        print(f"\n[🤖] Smart Node (ESP-Claw) request detected!")
    else:
        # It's a Basic Node command!
        target_topic = PUBLISH_TOPIC_BASIC
        active_prompt = PROMPT_BASIC
        print(f"\n[💡] Basic Node (MQTT) request detected!")

    print(f"[🧠] Spinning up {CODER_MODEL} for command: '{clean_command}'")
    
    full_prompt = f"{active_prompt}\n\nUser Command: {clean_command}\nOutput strictly valid JSON:"

    payload = {
        "model": CODER_MODEL,
        "prompt": full_prompt,
        "stream": False,
        "format": "json",      
        "keep_alive": 0        
    }

    try:
        response = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=60)
        response.raise_for_status()
        
        reply_json_str = response.json().get("response", "").strip()
        return reply_json_str, target_topic 

    except Exception as e:
        print(f"[!] Ollama generation error: {e}")
        return None, None

# ==========================================
# 4. MQTT EVENT LOOP
# ==========================================
def process_request(text, client):
    # Pass to Ollama and get both the payload and the correct topic back
    json_payload, target_topic = translate_to_json(text)
    
    if json_payload and target_topic:
        print(f"[⚡] Generated JSON payload:\n{json_payload}")
        
        try:
            parsed_check = json.loads(json_payload)
            client.publish(target_topic, json_payload)
            print(f"[✅] Payload published to {target_topic}")

            # ==========================================
            # --- THE NEW FEEDBACK LOOP ---
            # ==========================================
            node_type = "Basic Node" if "basic" in target_topic else "ESP-Claw"
            
            # Clean up the text for the reply summary
            clean_command = text.lower().replace("!action", "").replace("edge", "").replace("claw", "").strip()
            confirmation_text = f"[DISPATCH] Sent '{clean_command}' to {node_type}."
            
            # Build the exact payload your Heltec/LilyGo radio expects
            reply_payload = {
                "channel": 2,  
                "from": HELTEC_NODE_ID_DEC, 
                "type": "sendtext",
                "payload": confirmation_text
            }
            
            # Broadcast it directly to the radio!
            client.publish(RADIO_PUBLISH_TOPIC, json.dumps(reply_payload), retain=True)
            print(f"[📡] Radio confirmation transmitted: '{confirmation_text}'")
            # ==========================================

        except json.JSONDecodeError:
            print(f"[❌] AI failed to generate valid JSON. Dropping payload.")
    else:
        print("[!] No response from AI. Dropping.")

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[+] Dispatcher connected to Broker at {BROKER_IP}")
        client.subscribe(LISTEN_TOPIC)
        print(f"[+] Listening strictly on {LISTEN_TOPIC} for IoT handoffs...")
        print(f"[+] Routing to -> {PUBLISH_TOPIC_BASIC} (Basic) and {PUBLISH_TOPIC_CLAW} (Claw)")
    else:
        print(f"[!] Dispatcher failed to connect to Broker, return code {rc}")

def on_message(client, userdata, msg):
    incoming_text = msg.payload.decode("utf-8")
    threading.Thread(target=process_request, args=(incoming_text, client)).start()

# ==========================================
# START ENGINE
# ==========================================
if __name__ == "__main__":
    print(f"=== Starting DUAL-PATH IoT Dispatcher Brain ({CODER_MODEL}) ===")
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