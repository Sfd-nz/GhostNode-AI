# GhostNode-AI
📻 A 100% offline, LoRa-powered AI mesh network. Connects local LLMs (Ollama) and ChromaDB to a Meshtastic radio mesh via MQTT. Scrapes intel, reads offline manuals, and broadcasts tactical RAG database answers over encrypted radio.

<img width="2752" height="1536" alt="{__image_generation_instructions___{_202604222223" src="https://github.com/user-attachments/assets/c6c35e21-cc6c-4840-8ab0-ae692a951e6a" />

📻 Off-Grid Tactical AI Mesh (LoRa / MQTT)
This project creates a fully offline, autonomous AI "Librarian" and Tactical Assistant for a Meshtastic LoRa network. It operates independently of the internet, scraping global intelligence and reading offline manuals into a local vector database, and then broadcasts answers over an encrypted radio mesh.

This system relies on two main "brains":

The Ingestion Brain (DropzoneChromadb_Release.py): Quietly builds the offline database by parsing PDFs and scraping RSS intel feeds using stealth browser masquerading. Includes a 90-day auto-pruning system to protect RAM.

The Reader Brain (LLMconnectLora_Release.py): Listens to the radio mesh via MQTT. When an authorized user sends a command (e.g., !tac What is the latest news in the US?), it securely queries the database and broadcasts a highly concise, tactical response over the LoRa network.

📻 Phase 1: Hardware & The "Ghost Node" Problem
To build this system, you must use two separate Meshtastic radios and a local MQTT Broker.

Why do I need two radios? (The "Ghost Node" Problem)
Meshtastic radios are designed to prevent "echoes." If your phone is connected via Bluetooth to Node A, and you send a message, Node A broadcasts it. If the AI is also plugged into Node A, the AI will process your message and tell Node A to broadcast the reply.

However, because Node A is the one transmitting the reply, it assumes you already know what the message says. It will broadcast the AI's reply to the rest of the world, but it will not show the reply on your phone's screen. ### The Solution: The Hardware Loop
To fix this, you must separate the AI from your personal radio.

The Base Station (Node A): This is a dedicated radio (like a Heltec V3 plugged into your wall). It is connected to your local Wi-Fi network and acts solely as the AI's mouth and ears.

Your Personal Radio (Node B): This is the radio in your pocket, connected to your phone via Bluetooth.

The Local Broker: A standalone MQTT server on your home network. The easiest and lowest-power way to do this is to flash a cheap ESP32 board (like a LilyGo) with basic MQTT broker firmware, or run Mosquitto on a Raspberry Pi.

How it flows:
You type !tac on your phone (Node B). Node B broadcasts it over LoRa. The Base Station (Node A) hears it over LoRa, translates it, and sends it over Wi-Fi to your Local MQTT Broker. The Python script reads it from the Broker, thinks, and sends the answer back to the Broker. Node A broadcasts the answer over LoRa. Your phone (Node B) receives the LoRa signal, and the answer appears on your screen.

🛠️ Phase 2: Software Prerequisites
Before running the Python scripts, you must install the local AI engine and download the required models.

Install Ollama on your machine.

Open your terminal/command prompt and download the AI Chat Model (Requires ~24GB RAM/VRAM):

Bash
ollama run dolphin-mixtral:8x7b-v2.5-q4_K_M
Download the Database Embedding Model:

Bash
ollama pull nomic-embed-text


📦 Phase 3: Python Dependencies
This project requires specific Python libraries to handle MQTT routing, database vectoring, and web scraping. Install all required packages by running this command in your terminal:

Bash
pip install paho-mqtt requests chromadb feedparser beautifulsoup4 PyPDF2 python-dotenv


🔐 Phase 4: Configuration & Security (.env)
DO NOT hardcode your passwords or IP addresses into the Python scripts. 1. Create a new text file in the exact same folder as your Python scripts.
2. Name the file exactly .env (ensure it is not named .env.txt).
3. Paste the template below into the file, and fill in your specific network details and file paths.


🚀 Phase 5: Launching the System
You must run the scripts in separate terminal windows.

1. Start the Ingestion Brain
This builds your database. Run this script and let it run in the background. It will automatically wake up every 2 hours to pull fresh intelligence, and will safely clean up old memory.

Bash
python DropzoneChromadb_Release.py
Drop any .pdf or .txt survival manuals into your designated Dropzone folder, and use the terminal menu (Option 1) to permanently memorize them.

2. Start the Reader Brain
Once your database has some data, open a second terminal window and start the radio listener.

Bash
python LLMconnectLora_Release.py
3. Send a Radio Command
Grab your personal Meshtastic radio (Node B) and send a text to your designated AI channel:

!tac What is the latest global conflict update? (Tactical military response)

!ai Summarize the manual on basic first aid. (Direct, efficient response)

!surv How do I purify water? (Rugged survival advice)

!grump What is the news today? (Sarcastic, cynical response)


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
