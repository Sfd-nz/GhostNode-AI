🧠 AI Model & Hardware Selection Guide
The Off-Grid Tactical AI Mesh relies on local Large Language Models (LLMs) running via Ollama. Because this system is entirely offline, the "brain" relies 100% on the physical hardware of the Base Station PC.

If your computer does not have the hardware to run the default heavy model, you can easily swap it for a smaller, faster one.

1. The Default Heavyweight: dolphin-mixtral:8x7b-v2.5-q4_K_M
Hardware Required: 32GB+ System RAM and 8GB+ GPU VRAM.

Why we use it: Mixtral 8x7B is an exceptionally smart "Mixture of Experts" model. The "Dolphin" version is specifically chosen because it is uncensored. Standard AI models often have "safety rails" that will refuse to answer questions about tactical military movements, global conflict, or gritty survival medicine. Dolphin will answer the prompt directly and factually without arguing with you.

The Catch: It requires roughly 24GB to 26GB of total memory to load.

2. Mid-Tier Alternative (Recommended for 16GB RAM / 6GB+ GPU)
If you have a standard gaming PC or a modern laptop, you should step down to an 8-Billion parameter model. It will run lightning fast and still provide excellent RAG (Database reading) capabilities.

Top Choice: dolphin-llama3:8b

Command: ollama run dolphin-llama3:8b

Why: This gives you the incredible reasoning speed of Meta's Llama 3, combined with the "Dolphin" uncensored training. It is the perfect balance of high intelligence, tactical compliance, and low memory usage.

Memory Cost: ~5.5GB of RAM/VRAM.

Runner Up: mistral:7b

Command: ollama run mistral

Why: The gold standard for lightweight local AI. Extremely stable, great at summarizing news, and uses very little power.

3. Low-Tier / Mini-PC Alternative (Recommended for 8GB RAM / No GPU)
If you are running your Base Station on an old office PC, a cheap Mini-PC, or a Raspberry Pi 5, you need a "Small Language Model" (SLM).

Top Choice: phi3:mini

Command: ollama run phi3:mini

Why: Built by Microsoft, this 3.8-Billion parameter model punches way above its weight class. It was specifically trained to read documents and answer questions based on them (which is exactly what this project does). It will run on almost anything.

Memory Cost: ~2.5GB of RAM.

Runner Up: qwen2:1.5b

Command: ollama run qwen2:1.5b

Why: Insanely fast on low-end hardware, though its reasoning skills are slightly lower than Phi-3.

⚠️ A Note on the Embedding Model
In your .env file, you will see EMBED_MODEL="nomic-embed-text".
Do not change this. The embedding model is the mathematical engine that translates news articles into vectors so they can be saved to the database. nomic-embed-text is incredibly tiny (only 274 MB) and runs instantly on any CPU. You never need to downgrade this, regardless of how slow your PC is.

⚙️ How to Swap Your Model
Open your terminal and download your new model (e.g., ollama pull dolphin-llama3:8b).

Open the .env file in your project folder.

Find this line:
OLLAMA_MODEL="dolphin-mixtral:8x7b-v2.5-q4_K_M"

Change it to your new model name:
OLLAMA_MODEL="dolphin-llama3:8b"

Save the .env file and restart the LLMconnectLora_Release.py script. The system will automatically start using the lighter brain.