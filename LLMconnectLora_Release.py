import paho.mqtt.client as mqtt
import json
import requests
import datetime
import warnings
import time  
import chromadb
import threading 
import random
import os
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ==========================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==========================================
load_dotenv() # Reads the .env file

BROKER_IP = os.getenv("BROKER_IP", "127.0.0.1")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")
LISTEN_TOPIC = os.getenv("LISTEN_TOPIC", "msh/2/#")

HELTEC_NODE_ID_DEC = int(os.getenv("HELTEC_NODE_ID_DEC", "0"))
HELTEC_HEX_ID = "!" + hex(HELTEC_NODE_ID_DEC)[2:]

# Parse allowed channels from comma-separated string
ALLOWED_AI_CHANNELS = [int(x.strip()) for x in os.getenv("ALLOWED_AI_CHANNELS", "2").split(",")]

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

DB_PATH = os.getenv("DB_PATH", "./chroma_db")

# ==========================================
# 2. DATABASE CLIENT & LOCK HANDLER
# ==========================================
try:
    chroma_client = chromadb.PersistentClient(path=DB_PATH)
    collection = chroma_client.get_or_create_collection(name="offline_knowledge_base")
except Exception as e:
    print(f"[!] Warning: Could not connect to ChromaDB on startup: {e}")

def safe_query(query_embeddings, n_results, where=None):
    """Prevents SQLite 'database is locked' crashes by retrying if the ingestion script is currently writing."""
    retries = 5
    for attempt in range(retries):
        try:
            if where:
                return collection.query(query_embeddings=query_embeddings, n_results=n_results, where=where)
            else:
                return collection.query(query_embeddings=query_embeddings, n_results=n_results)
        except Exception as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                time.sleep(random.uniform(0.2, 0.8))
            else:
                raise e

# ==========================================
# 3. MEMORY & PERSONAS
# ==========================================
MAX_MEMORY_LENGTH = 10 
conversation_history = {"!ai": {}, "!tac": {}, "!grump": {}, "!surv": {}}

PERSONAS = {
    "!ai": "You are an off-grid AI on a low-bandwidth radio. Answer directly. DO NOT repeat the prompt. Base answers STRICTLY on the Database results. If Database says 'No relevant database info found.', you MUST reply 'Error: No data available.' Do NOT invent answers.",
    "!tac": "You are a tactical radio operator. Use military brevity. Base answers STRICTLY on Database results. If Database says 'No relevant database info found.', you MUST reply 'NEGATIVE CONTACT. NO INTEL AVAILABLE.' Do NOT invent military events. No formatting.",
    "!grump": "You are a cynical AI trapped inside a radio. Base answers ONLY on Database results. If Database says 'No relevant database info found.', complain that your database is empty. Do not hallucinate. No formatting.",
    "!surv": "You are a hardened off-grid survival expert. Give rugged advice. Base answers STRICTLY on Database. If Database says 'No relevant database info found.', say you don't know. No formatting."
}

# ==========================================
# 4. AI LOGIC & DATABASE SEARCH
# ==========================================
def ask_ollama(trigger, user_question, sender_id):
    current_time = datetime.datetime.now().strftime("%I:%M %p")
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n[🧠] {trigger} is thinking about user {sender_id}'s question...")

    retrieved_knowledge = "No relevant database info found."
    if user_question.strip():
        try:
            embed_payload = {"model": EMBED_MODEL, "prompt": user_question, "keep_alive": "0m"}
            embed_resp = requests.post(OLLAMA_EMBED_URL, json=embed_payload, timeout=30)
            
            if embed_resp.status_code == 200:
                question_vector = embed_resp.json()["embedding"]
                
                time_keywords = ["news", "latest", "today", "update", "recent", "now", "current"]
                is_time_sensitive = any(kw in user_question.lower() for kw in time_keywords)
                
                search_filter = None
                if is_time_sensitive:
                    search_filter = {"$and": [{"category": "news"}, {"ingested_at": today_date}]}
                
                if search_filter:
                    results = safe_query(query_embeddings=[question_vector], n_results=5, where=search_filter)
                    
                    if not results['documents'] or not results['documents'][0]:
                        print("[!] No news found for today specifically. Falling back to general news...")
                        backup_filter = {"category": "news"}
                        results = safe_query(query_embeddings=[question_vector], n_results=5, where=backup_filter)
                        
                    if results['documents'] and results['documents'][0]:
                        docs = results['documents'][0]
                        metas = results['metadatas'][0] if results['metadatas'] else [{}] * len(docs)
                        
                        combined = list(zip(docs, metas))
                        try:
                            combined.sort(key=lambda x: x[1].get("unix_time", 0), reverse=True)
                        except Exception as sort_err:
                            print(f"[!] Sorting notice: {sort_err}. Using default order.")
                        
                        top_docs = [item[0] for item in combined[:4]]
                        retrieved_knowledge = " ".join(top_docs)
                        print(f"[💾] Database match found! (Sorted by newest. Filter applied: {search_filter})")
                else:
                    results = safe_query(query_embeddings=[question_vector], n_results=4)
                    if results['documents'] and results['documents'][0]:
                        retrieved_knowledge = " ".join(results['documents'][0])
                        print(f"[💾] Database match found! (Filter applied: None)")
                        
        except Exception as e:
            print(f"[!] RAG Error: {e}")
            if "Error finding id" in str(e):
                print("[!] CRITICAL: Stale index detected. You must restart this Python script to sync with the pruned database.")

    db_stat = f"[Database: {retrieved_knowledge}] "
    context_question = f"Time: {current_time}. {db_stat}User asks: {user_question}"

    if sender_id not in conversation_history[trigger]:
        conversation_history[trigger][sender_id] = []

    messages = [{"role": "system", "content": PERSONAS[trigger]}]
    messages.extend(conversation_history[trigger][sender_id])
    
    new_user_msg = {"role": "user", "content": context_question}
    messages.append(new_user_msg)
    
    payload = {
        "model": OLLAMA_MODEL, 
        "messages": messages, 
        "stream": False,
        "keep_alive": "0m", 
        "options": {
            "num_ctx": 2048,   
            "num_predict": 150 
        }
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        reply_text = response.json().get('message', {}).get('content', '').strip()
        
        print("[⚡] Ollama finished generating. Model unloaded from RAM.")
        
        conversation_history[trigger][sender_id].append(new_user_msg)
        conversation_history[trigger][sender_id].append({"role": "assistant", "content": reply_text})
        
        if len(conversation_history[trigger][sender_id]) > MAX_MEMORY_LENGTH:
            conversation_history[trigger][sender_id] = conversation_history[trigger][sender_id][-MAX_MEMORY_LENGTH:]
            
        return reply_text
    except Exception as e:
        return f"Error: Brain offline."

# ==========================================
# 5. CENTRAL COMMAND ROUTER
# ==========================================
def process_ai_command(text, sender_id, incoming_channel, client, msg_topic):
    if text.startswith("[") or sender_id == "AI-Bot": 
        return
        
    message_lower = text.lower()
    is_trigger = any(message_lower.startswith(t) for t in PERSONAS.keys())
    
    if is_trigger and incoming_channel not in ALLOWED_AI_CHANNELS:
        print(f"\n[🔒] FIREWALL BLOCKED: '{text}' requested on Channel {incoming_channel}. (Allowed: {ALLOWED_AI_CHANNELS})")
        return
    elif not is_trigger:
        return 
        
    for trigger in PERSONAS.keys():
        if message_lower.startswith(trigger):
            question = text[len(trigger):].strip()
            print(f"\n[📡] Received '{trigger}' command from {sender_id} on Channel {incoming_channel}")
            
            def background_processor(trig=trigger, q=question, sid=sender_id, ch=incoming_channel, topic=msg_topic):
                answer = ask_ollama(trig, q, sid)
                
                prefix = f"[{trig}] "
                max_chunk_length = 175 - len(prefix) 
                
                words = answer.split()
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
                
                # Base MQTT Topic to send replies out based on the listening topic root
                topic_root = LISTEN_TOPIC.split('/#')[0]
                heltec_downlink_topic = f"{topic_root}/json/mqtt/{HELTEC_HEX_ID}"
                
                original_topic_parts = topic.split('/')
                if len(original_topic_parts) > 0:
                    original_topic_parts[-1] = "!aibot"
                lilygo_display_topic = "/".join(original_topic_parts)

                for i, chunk in enumerate(chunks):
                    if total_chunks > 1:
                        final_text = f"{prefix}{chunk} ({i+1}/{total_chunks})"
                    else:
                        final_text = f"{prefix}{chunk}"
                        
                    print(f"[🤖] {trig} Reply Part {i+1}/{total_chunks}: {final_text}")
                    
                    heltec_payload = {
                        "channel": ch,
                        "from": HELTEC_NODE_ID_DEC, 
                        "type": "sendtext",
                        "payload": final_text
                    }
                    client.publish(heltec_downlink_topic, json.dumps(heltec_payload), retain=True)
                    
                    lilygo_payload = {"from": "AI-Bot", "type": "text", "payload": {"text": final_text}}
                    client.publish(lilygo_display_topic, json.dumps(lilygo_payload))
                    
                    if total_chunks > 1 and i < total_chunks - 1:
                        print(f"[⌛] Waiting 12 seconds for LoRa radio to transmit...")
                        time.sleep(12) 
                        
            threading.Thread(target=background_processor).start()
            break

# ==========================================
# 6. PURE MQTT EVENT LOOP
# ==========================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[+] Connected to Broker at {BROKER_IP}!")
        client.subscribe(LISTEN_TOPIC)
        print(f"[+] Listening on {LISTEN_TOPIC} for commands ({', '.join(PERSONAS.keys())})")
    else:
        print(f"[!] Failed to connect to Broker, return code {rc}")

def on_message(client, userdata, msg):
    try:
        decoded_payload = msg.payload.decode("utf-8")
        payload = json.loads(decoded_payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return

    sender_id = str(payload.get("from", "Unknown"))
    
    if "payload" in payload and isinstance(payload["payload"], dict) and "text" in payload["payload"]:
        text = payload["payload"]["text"]
        try:
            incoming_channel = int(payload.get("channel", 0))
        except ValueError:
            incoming_channel = 0
            
        process_ai_command(text, sender_id, incoming_channel, client, msg.topic)

# ==========================================
# START ENGINE
# ==========================================
print("=== Starting Pure MQTT AI Mesh Server ===")
client = mqtt.Client()
if MQTT_USER and MQTT_PASS:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message

print(f"Connecting to Broker at {BROKER_IP}...")
try:
    client.connect(BROKER_IP, BROKER_PORT, 60)
    client.loop_forever()
except Exception as e:
    print(f"[!] Could not connect to MQTT Broker. Please check your .env settings. Error: {e}")