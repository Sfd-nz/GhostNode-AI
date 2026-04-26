📻 GhostNode-AI: Off-Grid Tactical Mesh Server (LoRa / MQTT)
GhostNode-AI is a fully offline, autonomous AI "Librarian," Tactical Assistant, and Command & Control (C2) Dashboard designed for Meshtastic LoRa networks.

It operates entirely independent of the internet, scraping global intelligence and reading offline manuals into a local multi-collection vector database. It then answers queries from users on the mesh, broadcasting intelligent, context-aware answers over encrypted radio channels using strict transmission chunking to protect network bandwidth.
<img width="2752" height="1536" alt="{__image_generation_instructions___{_202604222223" src="https://github.com/user-attachments/assets/c6c35e21-cc6c-4840-8ab0-ae692a951e6a" />

⚠️ IMPORTANT: Running this on lower-end hardware? > The default model requires 24GB of RAM. If you are using a standard laptop or a Raspberry Pi, Please read the AI Model & Hardware Selection Guide to swap to a lighter model before continuing.

## 🧠 The Four Pillars of GhostNode-AI
This system relies on four interconnected Python engines running simultaneously:

1. **The Memory Librarian (`LLMchromadbScraper.py`):** Quietly builds the offline database. It utilizes stealth browser masquerading, multi-threaded worker queues, and URL deduplication to scrape RSS feeds without triggering bot-blockers. It separates data into specific collections and features a 90-day auto-pruning system.
2. **The AI Brain (`LoraConnectToLLM.py`):** The core radio listener. When an authorized user sends a command over the mesh (e.g., `!tac What is the latest news in the US?`), it securely queries the database using mathematical distance filtering to prevent hallucinations, and broadcasts a chunked response over LoRa.
3. **The Operations Center (`dashboard.py`):** A local Flask web UI designed for a field laptop or tablet. It allows the operator to monitor live squad chatter, query the AI silently, intercept commands like `!weather`, and manually transmit C2 text to specific radio channels.
4. **The Kinetic Dispatcher (`IoT_DispatcherV3.py`):** The hardware bridge. It intercepts natural language action commands (e.g., `turn on the blue led`), translates them into strict machine-readable JSON using a dedicated local coder LLM, and dispatches them to custom ESP32 hardware nodes. It then broadcasts a confirmation of the physical action back over the LoRa mesh.

---

## 📻 Phase 1: Hardware & The "Ghost Node" Problem
To build this system, you must use two separate Meshtastic radios, a local MQTT Broker, and optional Edge Nodes for physical interaction.

### The Hardware Loop Solution
* **The Base Station (Node A):** A dedicated radio (e.g., Heltec V3 plugged into the wall/server). Connected to local Wi-Fi, it acts solely as the AI's mouth and ears.
* **Your Personal Radio (Node B):** The radio in your pocket or tactical rig, connected to your phone.
* **The Local Broker:** A standalone MQTT server on your local network (e.g., Mosquitto on a Raspberry Pi or an ESP32 LilyGo).
* **Kinetic Edge Nodes (Optional):** ESP32 microcontrollers (D1 Minis, ESP32-S3s) running custom GhostNode firmware. These nodes listen on specific MQTT topics (`/basic` or `/claw`), parse the AI's JSON, trigger real-world actions, and survive power-spikes using custom Tasmota-style brownout overrides.

**How it flows:** You type a command on Node B. Node B transmits via LoRa. Node A hears it and routes it over Wi-Fi to your Local MQTT Broker. The Python AI Brain (or Dispatcher) reads it, thinks, and sends the answer back to the Broker. Node A broadcasts the answer/confirmation over LoRa. Your personal Node B receives it.

---

## 🛠️ Phase 2: Software Prerequisites
Before running the Python scripts, you must install the local AI engine and download the required models.

1. Install [Ollama](https://ollama.com/) on your machine.
2. Open your terminal and download the **AI Chat Model** (Requires ~24GB RAM/VRAM for Mixtral, or use `llama3:8b` for lower-end hardware):
   ```bash
   ollama run dolphin-mixtral:8x7b-v2.5-q4_K_M

Download the Database Embedding Model:
```bash
ollama pull nomic-embed-text 
```

Download the Kinetic Dispatcher Model (Optimized for JSON generation):
 ```bash
ollama pull qwen2.5-coder:7b
```


📦 Phase 3: Python Dependencies
This project requires specific Python libraries to handle MQTT routing, database vectoring, web scraping, and the C2 Dashboard. Install them via your terminal:
 ```bash
pip install paho-mqtt requests chromadb feedparser beautifulsoup4 PyPDF2 python-dotenv flask
 ```

🔐 Phase 4: Master Configuration (.env)
GhostNode-AI is heavily customizable. DO NOT hardcode your passwords or IP addresses into the Python scripts.

Create a new text file in the same folder as your Python scripts named exactly .env.

Copy the contents of the provided example.env file into your new .env file.

Configure your specific Broker IP, Heltec Node ID, and database file paths.

Tune your AI: The .env file controls the AI's hallucination safety net (MAX_DISTANCE), the scraper's stealth level (INGEST_WORKERS), and chunk overlap limits.

Tune your Dispatcher: Define your CODER_MODEL (default: qwen2.5-coder:7b) and your LISTEN_TOPIC for hardware handoffs.

🚀 Phase 5: Launching the System
You must run the scripts in separate terminal windows.

1. Start the Memory Librarian (Database Builder)
Run this script in the background. It will automatically wake up to pull fresh intelligence. Drop any .pdf survival manuals into your Dropzone folder and use Option 1 to memorize them.

Bash
python LLMchromadbScraper.py
2. Start the AI Brain (Radio Listener)
Connects the AI to your radio mesh to answer intelligence queries.

Bash
python LoraConnectToLLM.py
3. Start the Operations Center (C2 Web UI)
Launch your graphical interface and navigate to the local IP provided in the terminal.

Bash
python dashboard.py
4. Start the Kinetic Dispatcher (Hardware Bridge)
Launch the dual-path IoT router to enable physical hardware control via the mesh or dashboard.

Bash
python IoT_DispatcherV3.py
📡 Phase 6: Using the System
Radio Commands (Multi-Collection Routing)
Grab your personal Meshtastic radio (Node B) and send a text to your designated AI channel. The AI uses strict collection-routing to prevent cross-contamination of data:

!tac [query] -> Searches ONLY News intel. (Replies with a military SITREP).

!surv [query] -> Searches ONLY Manuals/PDFs. (Replies with rugged survival advice).

!grump [query] -> Searches ONLY Web scrapes. (Replies sarcastically).

!ai [query] -> Searches the entire database. (Direct, efficient response).

Kinetic Hardware Commands
Send natural language commands to interact with physical Edge Nodes. The Dispatcher will generate the JSON and broadcast a confirmation back to your radio:

!action [query] -> Routes to standard ESP32 Basic Nodes (e.g., !action turn on the blue led).

!action edge [query] or !action claw [query] -> Routes complex multi-axis commands to ESP32-S3 Smart Nodes (e.g., !action claw look to your top right).

Operations Center Dashboard Features
Split-Screen Comms: Separates your AI Database queries (Left Panel) from Live Squad Chatter (Right Panel).

Silent Mode: Ask the AI questions and read the answers on your screen without broadcasting them over the radio and congesting the LoRa network. (Works perfectly for testing !action commands silently).

Dynamic Channel Routing: Send manual human messages or broadcast AI intel to specific channels (0-5) using the dropdown menu.

Auto-Responder Intercepts: Squad mates can text !weather [City] over the radio, and the dashboard will silently fetch the internet data and broadcast the clean weather report automatically


```mermaid
graph TD
    %% Script 1 Workflow
    subgraph Script 1: Database Ingestion
        A[Dropzone PDFs & TXTs] -->|Read & Parse| C[(ChromaDB Vector Store)]
        B[RSS Feeds / Web] -->|Stealth Scrape| C
    end

    %% Script 2 Workflow
    subgraph Script 2: The Radio Brain
        E[Local MQTT Broker] -->|Receives Private Chat| D{Python Router}
        D <-->|1. Queries for Context| C
        D <-->|2. Sends Chat + DB Info| F[Ollama Local LLM]
        D -->|3. Sends Chunked Reply| E
    end

    %% Hardware/Mesh Workflow
    subgraph LoRa Mesh Network
        G(Base Station / Node A) <-->|WiFi Connect| E
        G <-->|LoRa Encrypted RF| H(Remote User / Node B)
    end
