import os
import json
import threading
import warnings
import logging
import socket
import requests
import time
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

IOT_LISTEN_TOPIC = "ghostnode/iot/#"
IOT_REQUEST_TOPIC = "ghostnode/iot/requests"
TELEMETRY_TOPIC = "ghostnode/iot/telemetry"

app = Flask(__name__)

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

chat_history = {"c2": [], "radio": []}
msg_counter = {"c2": 0, "radio": 0}

def add_c2_message(msg_data):
    msg_counter["c2"] += 1
    msg_data["id"] = msg_counter["c2"]
    chat_history["c2"].append(msg_data)
    if len(chat_history["c2"]) > 50: 
        chat_history["c2"].pop(0)

def add_radio_message(msg_data):
    msg_counter["radio"] += 1
    msg_data["id"] = msg_counter["radio"]
    chat_history["radio"].append(msg_data)
    if len(chat_history["radio"]) > 50: 
        chat_history["radio"].pop(0)

# ==========================================
# 2. MQTT BACKGROUND LISTENER & SORTER
# ==========================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        # THE FIX: IOT_LISTEN_TOPIC (ghostnode/iot/#) already grabs the telemetry folder.
        # Subscribing to TELEMETRY_TOPIC explicitly causes the double-echo. 
        client.subscribe(LISTEN_TOPIC)
        client.subscribe(IOT_LISTEN_TOPIC)
        print(f"[+] Dashboard MQTT Connected! Listening on {LISTEN_TOPIC} and {IOT_LISTEN_TOPIC}")
    else:
        print(f"[!] Dashboard MQTT Connection Failed: {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    
    # --- CATCH TELEMETRY DATA ---
    if topic == TELEMETRY_TOPIC:
        try:
            telemetry_data = json.loads(msg.payload.decode("utf-8"))
            node = telemetry_data.get("node_id", "UNKNOWN").upper()
            sensor = telemetry_data.get("sensor", "sensor").upper()
            value = telemetry_data.get("value", "N/A")
            
            display_text = f"[📡 SENSOR REPORT] {node} {sensor}: {value}"
            add_c2_message({"sender": "Telemetry-Hub", "text": display_text})
            
            # Restored Terminal Debug
            print(f"\n[📡] TELEMETRY DASH INTERCEPT: {node} {sensor} = {value}")
        except Exception:
            pass
        return

    # --- CATCH QWEN JSON COMMANDS ---
    if topic.startswith("ghostnode/iot/basic"):
        try:
            payload_str = msg.payload.decode("utf-8")
            parsed_json = json.loads(payload_str)
            pretty_json = json.dumps(parsed_json, indent=2)
            target = topic.split("/")[-1].upper()
            
            add_c2_message({
                "sender": "Kinetic-Dispatcher",
                "text": pretty_json,
                "is_json": True,
                "target_node": target
            })
            
            # --- THE RESTORED TERMINAL JSON DUMP ---
            print(f"\n[⚡] Dashboard intercepted Qwen JSON payload for {target} node:")
            print(pretty_json)
            print("-" * 40)
            
        except Exception:
            pass 
        return

    # --- CATCH STANDARD RADIO COMMS ---
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        
        if "payload" in payload and isinstance(payload["payload"], dict) and "text" in payload["payload"]:
            sender = str(payload.get("from", "Unknown"))
            text = payload["payload"]["text"]
            incoming_channel = int(payload.get("channel", 0))
            
            if sender == "Web-Dashboard" or sender == str(HELTEC_NODE_ID_DEC):
                return

            if text.lower().startswith("!weather"):
                add_radio_message({"sender": f"{sender} [Ch {incoming_channel}]", "text": text})
                location = text[8:].strip()
                api_reply = get_weather(location)
                add_radio_message({"sender": f"HQ-Auto [Ch {incoming_channel}]", "text": api_reply})
                
                reply_payload = {
                    "channel": incoming_channel, 
                    "from": HELTEC_NODE_ID_DEC, 
                    "type": "sendtext",
                    "payload": api_reply
                }
                mqtt_client.publish(RADIO_PUBLISH_TOPIC, json.dumps(reply_payload, ensure_ascii=False))
                
                # Restored Terminal Debug
                print(f"[🌤️] AUTO-REPLY: Sent weather to {sender} on Channel {incoming_channel}")
                return 

            elif sender == "AI-Bot" or text.startswith("!"):
                add_c2_message({"sender": sender, "text": text})
            else:
                add_radio_message({"sender": f"{sender} [Ch {incoming_channel}]", "text": text})
    except Exception:
        pass

if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

try:
    mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
    threading.Thread(target=mqtt_client.loop_forever, daemon=True).start()
except Exception as e:
    print(f"[!] Could not connect Dashboard to MQTT Broker: {e}")

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
        # Restored Terminal Debug
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
            
            button { padding: 12px 15px; border: none; cursor: pointer; font-weight: bold; font-family: monospace; border-radius: 4px; transition: 0.2s; color: white;}
            .btn-silent { background: #333; } .btn-silent:hover { background: #555; }
            .btn-c2 { background: #006644; } .btn-c2:hover { background: #009966; }
            .btn-radio { background: #995500; } .btn-radio:hover { background: #cc7700; }

            details { margin-top:5px; background:#111; padding:8px; border-radius:4px; border:1px solid #333; }
            summary { cursor:pointer; color:#888; font-size:14px; outline:none; font-weight: bold;}
            summary:hover { color:#00ffcc; }
            .json-pre { color:#00ffcc; margin:8px 0 0 0; font-size:13px; white-space: pre-wrap; }
        </style>
    </head>
    <body>
        <h2>📡 OPERATIONS CENTER</h2>
        
        <div class="layout">
            <div class="panel panel-c2">
                <h3 class="c2-title">🧠 AI Database Link & Kinetic C2</h3>
                <div id="chat-box-c2" class="chat-box"></div>
                <div class="input-area">
                    <input type="text" id="cmd-input" placeholder="!tac Status... OR !action turn on the led...">
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
                        <option value="0">Ch 0</option><option value="1">Ch 1</option>
                        <option value="2">Ch 2</option><option value="3">Ch 3</option>
                        <option value="4">Ch 4</option><option value="5">Ch 5</option>
                    </select>
                    <input type="text" id="radio-input" placeholder="Type message to squad or !weather...">
                </div>
                <div style="display: flex; justify-content: flex-end;">
                    <button type="button" class="btn-radio" onclick="sendRadio()">[📡] Transmit to Mesh</button>
                </div>
            </div>
        </div>

        <script>
            let lastC2Id = 0;
            let lastRadioId = 0;

            setInterval(async () => {
                const response = await fetch('/messages');
                const data = await response.json();
                
                const boxC2 = document.getElementById('chat-box-c2');
                let c2Updated = false;
                
                data.c2.forEach(msg => {
                    if (msg.id > lastC2Id) {
                        let div = document.createElement('div');
                        div.className = 'message';
                        
                        if (msg.is_json) {
                            div.innerHTML = `<span class="c2-sender" style="color:#ff00ff;">[🤖 QWEN -> ${msg.target_node} NODE]</span>
                                       <details>
                                           <summary>View Hardware JSON Payload</summary>
                                           <pre class="json-pre">${msg.text}</pre>
                                       </details>`;
                        } else {
                            let cssClass = msg.sender === 'Web-Dashboard' ? 'sender-you' : 'c2-sender';
                            if (msg.sender === 'Telemetry-Hub') {
                                cssClass = 'c2-sender';
                                div.style.color = '#ffff00'; // Highlight telemetry in yellow
                            }
                            div.innerHTML = `<span class="${cssClass}">[${msg.sender}]</span> ${msg.text}`;
                        }
                        
                        boxC2.appendChild(div);
                        lastC2Id = msg.id;
                        c2Updated = true;
                    }
                });
                if (c2Updated) boxC2.scrollTop = boxC2.scrollHeight;

                const boxRadio = document.getElementById('chat-box-radio');
                let radioUpdated = false;
                
                data.radio.forEach(msg => {
                    if (msg.id > lastRadioId) {
                        let div = document.createElement('div');
                        div.className = 'message';
                        
                        let cssClass = msg.sender.startsWith('HQ-') ? 'sender-you' : 'radio-sender';
                        div.innerHTML = `<span class="${cssClass}">[${msg.sender}]</span> ${msg.text}`;
                        
                        boxRadio.appendChild(div);
                        lastRadioId = msg.id;
                        radioUpdated = true;
                    }
                });
                if (radioUpdated) boxRadio.scrollTop = boxRadio.scrollHeight;
                
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
                if (!text) return;
                input.value = ''; 
                await fetch('/send/radio', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text, channel: parseInt(channelSelect.value) })
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
        add_c2_message({"sender": "Web-Dashboard", "text": f"[{mode}] {text}"})
        
        if text.lower().startswith("!action"):
            mqtt_client.publish(IOT_REQUEST_TOPIC, text)
            # Restored Terminal Debug
            print(f"\n[💻] KINETIC ROUTE: Passed '{text}' to Qwen Dispatcher.")
        else:
            payload = {"from": "Web-Dashboard", "channel": 2, "web_only": web_only, "payload": {"text": text}}
            mqtt_client.publish(C2_PUBLISH_TOPIC, json.dumps(payload, ensure_ascii=False))
            # Restored Terminal Debug
            print(f"\n[💻] C2 ACTION: Sent {mode} AI command.")
        
    return jsonify({"status": "sent"})

@app.route("/send/radio", methods=["POST"])
def send_radio():
    data = request.json
    raw_text = data.get("text", "")
    channel = int(data.get("channel", 0))
    
    if raw_text:
        if raw_text.lower().startswith("!weather"):
            location = raw_text[8:].strip() 
            text_to_send = get_weather(location)
            display_sender = f"HQ-Auto [Ch {channel}]"
        else:
            text_to_send = raw_text
            display_sender = f"HQ-Op [Ch {channel}]"

        add_radio_message({"sender": display_sender, "text": text_to_send})
        
        def background_transmit(full_text, ch):
            max_chunk_length = 190 
            words = full_text.split()
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
                payload = {"channel": ch, "from": HELTEC_NODE_ID_DEC, "type": "sendtext", "payload": final_text}
                mqtt_client.publish(RADIO_PUBLISH_TOPIC, json.dumps(payload, ensure_ascii=False))
                
                # Restored Terminal Debug
                print(f"[🎙️] Transmitted Part {i+1}/{total_chunks} on Channel {ch}: '{final_text}'")
                
                if total_chunks > 1 and i < total_chunks - 1:
                    print(f"[⌛] Waiting 12 seconds for radio duty cycle...")
                    time.sleep(12)

        threading.Thread(target=background_transmit, args=(text_to_send, channel)).start()
            
    return jsonify({"status": "sent"})

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
