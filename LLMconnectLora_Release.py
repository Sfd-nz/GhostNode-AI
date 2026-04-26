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
import re
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ==========================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==========================================
load_dotenv()

BROKER_IP = os.getenv("BROKER_IP", "127.0.0.1")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")
LISTEN_TOPIC = os.getenv("LISTEN_TOPIC", "msh/2/#")

HELTEC_NODE_ID_DEC = int(os.getenv("HELTEC_NODE_ID_DEC", "0"))
HELTEC_HEX_ID = "!" + hex(HELTEC_NODE_ID_DEC)[2:]

ALLOWED_AI_CHANNELS = [int(x.strip()) for x in os.getenv("ALLOWED_AI_CHANNELS", "2").split(",")]

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

DB_PATH = os.getenv("DB_PATH", "./chroma_db")

FETCH_LIMIT = int(os.getenv("FETCH_LIMIT", "40"))
TOP_DOCS_RETURNED = int(os.getenv("TOP_DOCS_RETURNED", "4"))

USE_DISTANCE_FILTER = os.getenv("USE_DISTANCE_FILTER", "false").lower() == "true"
MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "1.25"))

# ==========================================
# 2. DATABASE CLIENT & COLLECTION ROUTER
# ==========================================
try:
    chroma_client = chromadb.PersistentClient(path=DB_PATH)

    col_news = chroma_client.get_or_create_collection(name="intel_news")
    col_manuals = chroma_client.get_or_create_collection(name="intel_manuals")
    col_web = chroma_client.get_or_create_collection(name="intel_web")

except Exception as e:
    print(f"[!] CRITICAL: Could not connect to ChromaDB: {e}")
    raise SystemExit(1)

def safe_query(collection, query_embeddings, n_results, where=None):
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
conversation_history = {"!ai": {}, "!tac": {}, "!grump": {}, "!surv": {}, "!trip": {}}

PERSONAS = {
    "!ai": "You are an off-grid AI on a low-bandwidth radio. Answer directly. DO NOT repeat the prompt. Base answers STRICTLY on the Database results. If Database says 'No relevant database info found.', you MUST reply 'Error: No data available.' Do NOT invent answers.",
    # --- THE FIX: FORCING THE SITREP ---
    "!tac": "You are a tactical radio operator. Use military brevity. You MUST summarize the provided Database context as a SITREP, even if it does not perfectly match the user's requested date or specific war. Do not evaluate if the intel is perfect, just report what you are handed. If the Database literally says 'No relevant database info found.', ONLY THEN reply 'NEGATIVE CONTACT. NO INTEL AVAILABLE.' No formatting.",
    "!grump": "You are a cynical AI trapped inside a radio. Base answers ONLY on Database results. If Database says 'No relevant database info found.', complain that your database is empty. Do not hallucinate. No formatting.",
    "!surv": "You are a hardened off-grid survival expert. Give rugged advice. Base answers STRICTLY on Database. If Database says 'No relevant database info found.', say you don't know. No formatting.",
    "!trip": "You are a wild, creative, and hilarious AI companion. Completely IGNORE the 'Database Context'. Your only job is to tell funny stories, jokes, and have entertaining, imaginative conversations. You are fully authorized to hallucinate and make things up. Keep it fun, but concise enough for a radio transmission."
}

# ==========================================
# 4. COLLECTION ROUTING
# ==========================================
COLLECTION_MAP = {
    "!tac": ["news"],
    "!surv": ["manuals"],
    "!grump": ["web"],
    "!ai": ["news", "manuals", "web"],
    "!trip": []
}

COLLECTION_OBJECTS = {
    "news": col_news,
    "manuals": col_manuals,
    "web": col_web
}

# ==========================================
# 5. GEO-EXPANSION HYBRID RAG SEARCH
# ==========================================
GEO_SYNONYMS = {
    "nz": ["nz", "new zealand", "zealand", "kiwi", "auckland", "wellington", "luxon", "ardern", "rnz"],
    "us": ["us", "usa", "united states", "america", "american", "washington", "pentagon", "biden"],
    "uk": ["uk", "united kingdom", "britain", "british", "london", "england"],
    "iran": ["iran", "tehran", "iranian", "islamic republic"],
    "israel": ["israel", "israeli", "idf", "jerusalem", "tel aviv", "gaza"],
    "russia": ["russia", "russian", "moscow", "putin", "kremlin"],
    "china": ["china", "chinese", "beijing", "prc", "ccp"],
    "ukraine": ["ukraine", "kyiv", "ukrainian", "zelensky"]
}

STOP_WORDS = {
    "whats", "what", "is", "the", "latest", "news", "in", "on", "about",
    "today", "now", "current", "update", "a", "an", "of", "and", "to",
    "for", "with", "are", "do", "does", "did", "how", "why", "when",
    "where", "can", "could", "would", "should", "tell", "me", "give",
    "any", "some", "best", "route", "around", "get"
}

# ==========================================
# 6. EMBEDDING CALL
# ==========================================
def embed_question(question):
    payload = {"model": EMBED_MODEL, "prompt": question, "keep_alive": "0m"}
    resp = requests.post(OLLAMA_EMBED_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["embedding"]

# ==========================================
# 7. MULTI-COLLECTION RAG QUERY
# ==========================================
def rag_query(trigger, question_vector, user_question):
    collections_to_use = COLLECTION_MAP.get(trigger, ["news"])
    
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    time_keywords = ["news", "latest", "today", "update", "recent", "now", "current"]
    is_time_sensitive = any(kw in user_question.lower() for kw in time_keywords)

    all_docs = []
    all_meta = []
    all_distances = []

    for col_key in collections_to_use:
        col_obj = COLLECTION_OBJECTS.get(col_key)
        if not col_obj:
            continue

        try:
            results = None
            if is_time_sensitive and col_key == "news":
                search_filter = {"ingested_at": today_date}
                results = safe_query(col_obj, query_embeddings=[question_vector], n_results=FETCH_LIMIT, where=search_filter)
                
                if not results or not results.get("documents") or not results["documents"][0]:
                    print(f"[!] No today's news found in {col_key}. Stripping date filter...")
                    results = safe_query(col_obj, query_embeddings=[question_vector], n_results=FETCH_LIMIT)
            else:
                results = safe_query(col_obj, query_embeddings=[question_vector], n_results=FETCH_LIMIT)

            docs = results.get("documents", [[]])[0] if results and results.get("documents") else []
            metas = results.get("metadatas", [[]])[0] if results and results.get("metadatas") else []
            dists = results.get("distances", [[]])[0] if results and "distances" in results else [None] * len(docs)

            dropped = 0
            
            # --- CALIBRATION PRINT BLOCK ---
            print(f"\n[📊] --- CALIBRATION MODE: DISTANCE SCORES ---")
            
            for d, m, dist in zip(docs, metas, dists):
                if not d:
                    continue
                
                # Print the exact score for you to see
                if dist is not None:
                    score_val = float(dist)
                    title = m.get('title', 'Unknown Title') if m else 'Unknown'
                    
                    status = "✅ ACCEPTED"
                    if USE_DISTANCE_FILTER and score_val > MAX_DISTANCE:
                        status = "❌ DROPPED"
                        dropped += 1
                        print(f"   -> {status} | Score: {score_val:.4f} | Title: {title}")
                        continue
                        
                    print(f"   -> {status} | Score: {score_val:.4f} | Title: {title}")

                all_docs.append(d)
                all_meta.append(m)
                all_distances.append(dist)
                
            print(f"[📊] -----------------------------------------\n")

            if dropped > 0:
                print(f"[!] Distance filter blocked {dropped} docs from {col_key} (Max allowed: {MAX_DISTANCE})")

        except Exception as e:
            print(f"[!] RAG query failure on {col_key}: {e}")

    if not all_docs:
        return "No relevant database info found."

    base_keywords = [w for w in re.findall(r"\b\w+\b", user_question.lower()) if w not in STOP_WORDS]
    has_geo_intent = any(kw in GEO_SYNONYMS for kw in base_keywords)

    if has_geo_intent:
        expanded_keywords = set(base_keywords)
        for kw in base_keywords:
            if kw in GEO_SYNONYMS:
                expanded_keywords.update(GEO_SYNONYMS[kw])

        print(f"[🔍] Geo-Intent Detected. Searching for: {list(expanded_keywords)}")
        scored_docs = []
        for doc in all_docs:
            score = 0
            doc_lower = doc.lower()
            for kw in expanded_keywords:
                if re.search(r"\b" + re.escape(kw) + r"\b", doc_lower):
                    score += 1
            scored_docs.append((score, doc))

        scored_docs.sort(key=lambda x: x[0], reverse=True)
        
        if scored_docs[0][0] > 0:
            top_docs = [item[1] for item in scored_docs[:TOP_DOCS_RETURNED]]
            print(f"[💾] Geo-ranked match found! (Top Score: {scored_docs[0][0]})")
            return " ".join(top_docs)
        else:
            print(f"[!] Geo-rank fallback. No keywords found in the {len(all_docs)} fetched docs.")
            top_docs = all_docs[:TOP_DOCS_RETURNED]
            return " ".join(top_docs)
    else:
        print(f"[🔍] Pure Semantic Search (No Geo-Intent).")
        top_docs = all_docs[:TOP_DOCS_RETURNED]
        return " ".join(top_docs)

# ==========================================
# 8. ASK OLLAMA
# ==========================================
def ask_ollama(trigger, user_question, sender_id):
    current_time = datetime.datetime.now().strftime("%I:%M %p")
    print(f"\n[🧠] {trigger} thinking about {sender_id}...")

    retrieved_knowledge = "No relevant database info found."

    if user_question.strip():
        try:
            question_vector = embed_question(user_question)
            retrieved_knowledge = rag_query(trigger, question_vector, user_question)
        except Exception as e:
            print(f"[!] Embedding/RAG error: {e}")

    db_stat = f"### DATABASE CONTEXT ###\n{retrieved_knowledge}\n### END CONTEXT ###"
    context_question = f"Time: {current_time}\n{db_stat}\nUser asks: {user_question}"

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
            "num_predict": 170
        }
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()

        reply_text = response.json().get("message", {}).get("content", "").strip()

        conversation_history[trigger][sender_id].append(new_user_msg)
        conversation_history[trigger][sender_id].append({"role": "assistant", "content": reply_text})

        if len(conversation_history[trigger][sender_id]) > MAX_MEMORY_LENGTH:
            conversation_history[trigger][sender_id] = conversation_history[trigger][sender_id][-MAX_MEMORY_LENGTH:]

        return reply_text

    except Exception:
        return "Error: Brain offline."

# ==========================================
# 9. CENTRAL COMMAND ROUTER
# ==========================================
def process_ai_command(text, sender_id, incoming_channel, client, msg_topic, is_web_only=False):
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

            mode = "[SILENT/WEB]" if is_web_only else "[RADIO]"
            print(f"\n[📡] {mode} Received '{trigger}' command from {sender_id} on Channel {incoming_channel}")

            def background_processor(trig=trigger, q=question, sid=sender_id, ch=incoming_channel, topic=msg_topic, web_only=is_web_only):
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
                        current_chunk = word if not current_chunk else current_chunk + " " + word

                if current_chunk:
                    chunks.append(current_chunk)

                total_chunks = len(chunks)

                topic_root = LISTEN_TOPIC.split("/#")[0]
                heltec_downlink_topic = f"{topic_root}/json/mqtt/{HELTEC_HEX_ID}"

                original_topic_parts = topic.split("/")
                if len(original_topic_parts) > 0:
                    original_topic_parts[-1] = "!aibot"
                lilygo_display_topic = "/".join(original_topic_parts)

                for i, chunk in enumerate(chunks):
                    final_text = f"{prefix}{chunk}"
                    if total_chunks > 1:
                        final_text += f" ({i+1}/{total_chunks})"

                    print(f"[🤖] {trig} Reply {i+1}/{total_chunks}: {final_text}")

                    lilygo_payload = {"from": "AI-Bot", "type": "text", "payload": {"text": final_text}}
                    client.publish(lilygo_display_topic, json.dumps(lilygo_payload))

                    if not web_only:
                        heltec_payload = {
                            "channel": ch,
                            "from": HELTEC_NODE_ID_DEC,
                            "type": "sendtext",
                            "payload": final_text
                        }
                        client.publish(heltec_downlink_topic, json.dumps(heltec_payload), retain=True)

                        if total_chunks > 1 and i < total_chunks - 1:
                            print(f"[⌛] Waiting 12 seconds for LoRa transmit...")
                            time.sleep(12)

            threading.Thread(target=background_processor).start()
            break

# ==========================================
# 10. MQTT EVENT LOOP
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

        # ==========================================
        # --- THE NEW IOT INTERCEPTOR ---
        # ==========================================
        if text.lower().startswith("!action"):
            print(f"\n[⚡] IoT Hardware command detected from {sender_id}! Routing to Dispatcher...")
            print(f"    Command: {text}")
            # Publish to the holding topic for the secondary python script
            client.publish("ghostnode/iot/requests", text)
            # Return immediately to STOP the main AI from trying to answer
            return 
        # ==========================================

        is_web_only = payload.get("web_only", False)

        try:
            incoming_channel = int(payload.get("channel", 0))
        except ValueError:
            incoming_channel = 0

        process_ai_command(text, sender_id, incoming_channel, client, msg.topic, is_web_only)

# ==========================================
# START ENGINE
# ==========================================
print("=== Starting Pure MQTT AI Mesh Server (Calibration Edition) ===")
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

if MQTT_USER and MQTT_PASS:
    client.username_pw_set(MQTT_USER, MQTT_PASS)

client.on_connect = on_connect
client.on_message = on_message

print(f"Connecting to Broker at {BROKER_IP}...")

try:
    client.connect(BROKER_IP, BROKER_PORT, 60)
    client.loop_forever()
except Exception as e:
    print(f"[!] Could not connect to MQTT Broker. Check .env. Error: {e}")
