import os
import json
import threading
import warnings
import logging
import socket
import requests
import time  # <-- Added this so the 12-second delay works!
from flask import Flask, request, jsonify, render_template_string
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# ==========================================
# 0. MUTE WARNINGS & LOG SPAM
# ==========================================
warnings.filterwarnings("ignore", category=DeprecationWarning)
flask_log = logging.getLogger('werkzeug')
flask_log.setLevel(logging.ERROR)

# ==========================================
# 1. LOAD CONFIGURATION
# ==========================================
load_dotenv()
BROKER_IP = os.getenv("BROKER_IP", "127.0.0.1")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

HELTEC_NODE_ID_DEC = int(os.getenv("HELTEC_NODE_ID_DEC", "0"))
HELTEC_HEX_ID = "!" + hex(HELTEC_NODE_ID_DEC)[2:]

ROOT_TOPIC = os.getenv("LISTEN_TOPIC", "msh/2/#").split("/#")[0]
LISTEN_TOPIC = f"{ROOT_TOPIC}/#"  
C2_PUBLISH_TOPIC = f"{ROOT_TOPIC}/json/mqtt/WebUI"
RADIO_PUBLISH_TOPIC = f"{ROOT_TOPIC}/json/mqtt/{HELTEC_HEX_ID}"

app = Flask(__name__)
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

chat_history = {"c2": [], "radio": []}

# ==========================================
# 2. MQTT BACKGROUND LISTENER & SORTER
# ==========================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(LISTEN_TOPIC)
    else:
        print(f"[!] Dashboard MQTT Connection Failed: {rc}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        
        if "payload" in payload and isinstance(payload["payload"], dict) and "text" in payload["payload"]:
            sender = str(payload.get("from", "Unknown"))
            text = payload["payload"]["text"]
            
            incoming_channel = int(payload.get("channel", 0))
            
            # Ignore echoes from our own node
            if sender == "Web-Dashboard" or sender == str(HELTEC_NODE_ID_DEC):
                return

            # --- AUTO-RESPONDER FOR FRIENDS ---
            if text.lower().startswith("!weather"):
                chat_history["radio"].append({"sender": f"{sender} [Ch {incoming_channel}]", "text": text})
                if len(chat_history["radio"]) > 50: chat_history["radio"].pop(0)
                
                location = text[8:].strip()
                api_reply = get_weather(location)
                
                chat_history["radio"].append({"sender": f"HQ-Auto [Ch {incoming_channel}]", "text": api_reply})
                
                reply_payload = {
                    "channel": incoming_channel, 
                    "from": HELTEC_NODE_ID_DEC, 
                    "type": "sendtext",
                    "payload": api_reply
                }
                mqtt_client.publish(RADIO_PUBLISH_TOPIC, json.dumps(reply_payload, ensure_ascii=False))
                print(f"[🌤️] AUTO-REPLY: Sent weather to {sender} on Channel {incoming_channel}")
                return 

            # --- NORMAL SORTER FOR EVERYTHING ELSE ---
            elif sender == "AI-Bot" or text.startswith("!"):
                chat_history["c2"].append({"sender": sender, "text": text})
                if len(chat_history["c2"]) > 50: chat_history["c2"].pop(0)
            else:
                chat_history["radio"].append({"sender": f"{sender} [Ch {incoming_channel}]", "text": text})
                if len(chat_history["radio"]) > 50: chat_history["radio"].pop(0)

    except Exception:
        pass

if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

try:
    mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
except Exception as e:
    print(f"[!] Could not connect Dashboard to MQTT Broker: {e}")

threading.Thread(target=mqtt_client.loop_forever, daemon=True).start()

# ==========================================
# 2.5 API FETCH FUNCTIONS
# ==========================================
def get_weather(location="Auckland"):
    try:
        url = f"https://wttr.in/{location}?format=4"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        response.encoding = 'utf-8' 
        return f"[WEATHER] {response.text.strip()}"
    except Exception as e:
        print(f"[!] Weather API Error: {e}")
        return f"[!] Error fetching weather for {location}."

# ==========================================
# 3. FLASK WEB ROUTES
# ==========================================
@app.route("/")
def index():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Mesh-Net Operations Center</title>
        <style>
            body { background-color: #0d0d0d; color: #d4d4d4; font-family: 'Courier New', Courier, monospace; margin: 0; padding: 20px; }
            h2 { border-bottom: 1px solid #333; padding-bottom: 10px; margin-top: 0; text-align: center; color: #ffffff;}
            .layout { display: flex; gap: 20px; max-width: 1400px; margin: auto; }
            .panel { flex: 1; background: #1a1a1a; padding: 20px; border-radius: 8px; border: 1px solid #333; display: flex; flex-direction: column;}
            
            .panel-c2 { box-shadow: 0 0 15px rgba(0, 255, 204, 0.1); border-top: 3px solid #00ffcc; }
            .c2-title { color: #00ffcc; }
            .c2-sender { color: #00ffcc; font-weight: bold; }
            
            .panel-radio { box-shadow: 0 0 15px rgba(255, 153, 0, 0.1); border-top: 3px solid #ff9900; }
            .radio-title { color: #ff9900; }
            .radio-sender { color: #ff9900; font-weight: bold; }

            .chat-box { height: 500px; overflow-y: auto; background: #0a0a0a; padding: 15px; border: 1px solid #222; margin-bottom: 15px; border-radius: 4px; flex-grow: 1;}
            .message { margin-bottom: 10px; line-height: 1.4; word-wrap: break-word;}
            .sender-you { color: #888888; }
            
            .input-area { display: flex; gap: 10px; margin-bottom: 10px; }
            input[type="text"] { flex: 1; padding: 12px; background: #2a2a2a; border: 1px solid #444; color: white; font-family: monospace; border-radius: 4px; outline: none; }
            input[type="text"]:focus { border-color: #ffffff; }
            
            select { padding: 12px; background: #2a2a2a; border: 1px solid #444; color: #ff9900; font-family: monospace; font-weight: bold; border-radius: 4px; outline: none; cursor: pointer; }
            select:focus { border-color: #ff9900; }
            
            button { padding: 12px 15px; border: none; cursor: pointer; font-weight: bold; font-family: monospace; border-radius: 4px; transition: 0.2s; color: white;}
            .btn-silent { background: #333; } .btn-silent:hover { background: #555; }
            .btn-c2 { background: #006644; } .btn-c2:hover { background: #009966; }
            .btn-radio { background: #995500; } .btn-radio:hover { background: #cc7700; }
        </style>
    </head>
    <body>
        <h2>📡 OPERATIONS CENTER</h2>
        
        <div class="layout">
            <div class="panel panel-c2">
                <h3 class="c2-title">🧠 AI Database Link</h3>
                <div id="chat-box-c2" class="chat-box"></div>
                <div class="input-area">
                    <input type="text" id="cmd-input" placeholder="e.g., !tac Status report...">
                </div>
                <div style="display: flex; gap: 10px;">
                    <button type="button" class="btn-silent" onclick="sendC2(true)">[🤫] Ask Silently</button>
                    <button type="button" class="btn-c2" onclick="sendC2(false)">[📻] Ask + Broadcast</button>
                </div>
            </div>

            <div class="panel panel-radio">
                <h3 class="radio-title">📻 Live Squad Comms</h3>
                <div id="chat-box-radio" class="chat-box"></div>
                <div class="input-area">
                    <select id="channel-select">
                        <option value="0">Ch 0</option>
                        <option value="1">Ch 1</option>
                        <option value="2">Ch 2</option>
                        <option value="3">Ch 3</option>
                        <option value="4">Ch 4</option>
                        <option value="5">Ch 5</option>
                    </select>
                    <input type="text" id="radio-input" placeholder="Type message to squad or !weather City...">
                </div>
                <div style="display: flex; gap: 10px; justify-content: flex-end;">
                    <button type="button" class="btn-radio" onclick="sendRadio()">[📡] Transmit to Mesh</button>
                </div>
            </div>
        </div>

        <script>
            setInterval(async () => {
                const response = await fetch('/messages');
                const data = await response.json();
                
                const boxC2 = document.getElementById('chat-box-c2');
                let htmlC2 = '';
                data.c2.forEach(msg => {
                    let cssClass = msg.sender === 'Web-Dashboard' ? 'sender-you' : 'c2-sender';
                    htmlC2 += `<div class="message"><span class="${cssClass}">[${msg.sender}]</span> ${msg.text}</div>`;
                });
                if (boxC2.innerHTML !== htmlC2) { boxC2.innerHTML = htmlC2; boxC2.scrollTop = boxC2.scrollHeight; }

                const boxRadio = document.getElementById('chat-box-radio');
                let htmlRadio = '';
                data.radio.forEach(msg => {
                    let cssClass = msg.sender.startsWith('HQ-') ? 'sender-you' : 'radio-sender';
                    htmlRadio += `<div class="message"><span class="${cssClass}">[${msg.sender}]</span> ${msg.text}</div>`;
                });
                if (boxRadio.innerHTML !== htmlRadio) { boxRadio.innerHTML = htmlRadio; boxRadio.scrollTop = boxRadio.scrollHeight; }
                
            }, 1000);

            async function sendC2(isSilent) {
                const input = document.getElementById('cmd-input');
                const text = input.value.trim();
                if (!text) return;
                input.value = ''; 
                await fetch('/send/c2', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text, web_only: isSilent })
                });
            }

            async function sendRadio() {
                const input = document.getElementById('radio-input');
                const channelSelect = document.getElementById('channel-select');
                const text = input.value.trim();
                const channel = parseInt(channelSelect.value);

                if (!text) return;
                input.value = ''; 
                await fetch('/send/radio', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text, channel: channel })
                });
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html_content)

@app.route("/messages", methods=["GET"])
def get_messages():
    return jsonify(chat_history)

@app.route("/send/c2", methods=["POST"])
def send_c2():
    data = request.json
    text = data.get("text", "")
    web_only = bool(data.get("web_only", True))
    
    if text:
        mode = "SILENT" if web_only else "BROADCAST"
        chat_history["c2"].append({"sender": "Web-Dashboard", "text": f"[{mode}] {text}"})
        
        payload = {"from": "Web-Dashboard", "channel": 2, "web_only": web_only, "payload": {"text": text}}
        mqtt_client.publish(C2_PUBLISH_TOPIC, json.dumps(payload, ensure_ascii=False))
        print(f"[💻] C2 ACTION: Sent {mode} AI command.")
        
    return jsonify({"status": "sent"})

@app.route("/send/radio", methods=["POST"])
def send_radio():
    data = request.json
    raw_text = data.get("text", "")
    channel = int(data.get("channel", 0))
    
    if raw_text:
        # Determine if it's a Weather API call or Normal Text
        if raw_text.lower().startswith("!weather"):
            location = raw_text[8:].strip() 
            text_to_send = get_weather(location)
            display_sender = f"HQ-Auto [Ch {channel}]"
        else:
            text_to_send = raw_text
            display_sender = f"HQ-Op [Ch {channel}]"

        # 1. Instantly display the full message in the Web UI
        chat_history["radio"].append({"sender": display_sender, "text": text_to_send})
        
        # 2. Define the background chunking process
        def background_transmit(full_text, ch):
            max_chunk_length = 190 # Safe limit leaving room for the (1/3) tag
            words = full_text.split()
            chunks = []
            current_chunk = ""
            
            for word in words:
                if len(current_chunk) + len(word) + 1 > max_chunk_length:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = word
                else:
                    if current_chunk:
                        current_chunk += " " + word
                    else:
                        current_chunk = word
            if current_chunk:
                chunks.append(current_chunk)

            total_chunks = len(chunks)

            # Loop through and send each chunk
            for i, chunk in enumerate(chunks):
                if total_chunks > 1:
                    final_text = f"{chunk} ({i+1}/{total_chunks})"
                else:
                    final_text = chunk
                
                payload = {
                    "channel": ch, 
                    "from": HELTEC_NODE_ID_DEC, 
                    "type": "sendtext",
                    "payload": final_text
                }
                mqtt_client.publish(RADIO_PUBLISH_TOPIC, json.dumps(payload, ensure_ascii=False))
                print(f"[🎙️] Transmitted Part {i+1}/{total_chunks} on Channel {ch}: '{final_text}'")
                
                # Apply the 12-second delay to protect the radio mesh
                if total_chunks > 1 and i < total_chunks - 1:
                    print(f"[⌛] Waiting 12 seconds for radio duty cycle...")
                    time.sleep(12)

        # 3. Spin up the background thread so the web UI stays lightning fast
        threading.Thread(target=background_transmit, args=(text_to_send, channel)).start()
            
    return jsonify({"status": "sent"})

# ==========================================
# START FLASK & PRINT IPs
# ==========================================
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    local_ip = get_local_ip()
    print("\n" + "="*50)
    print(" === 📡 MESH C2 DASHBOARD ACTIVE ===")
    print(" " + "="*50)
    print(f" [*] Local PC URL : http://127.0.0.1:5000")
    print(f" [*] Network URL  : http://{local_ip}:5000")
    print(" " + "="*50 + "\n")
    
    app.run(host="0.0.0.0", port=5000, debug=False)
