import chromadb
import requests
import feedparser
import time
import os
import PyPDF2
from bs4 import BeautifulSoup
import threading
import gc
import random
import re
from dotenv import load_dotenv

# ==========================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==========================================
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./chroma_db")
DROPZONE_PATH = os.getenv("DROPZONE_PATH", "./Dropzone")
OLLAMA_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

RSS_FEEDS = {
    "UK/World News": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "US News": "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
    "NZ News": "https://www.rnz.co.nz/rss/news",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "ISW - War Analysis": "https://www.iswresearch.org/feeds/posts/default", 
    "Breaking Defense": "https://breakingdefense.com/feed/",                
    "Defense News": "https://www.defensenews.com/arc/outboundfeeds/rss/",
    "War on the Rocks": "https://warontherocks.com/feed",
    "The War Zone": "https://www.twz.com/feed/",
    "Naval News": "https://www.navalnews.com/feed/",
    "Janes Intelligence": "https://www.janes.com/defence-news/rss",          
    "UK Defence Journal": "https://ukdefencejournal.org.uk/feed/",
    "Small Wars Journal": "https://smallwarsjournal.com/rss",               
    "DefenseScoop": "https://defensescoop.com/feed/",
    "CrisisGroup - Updates": "https://www.crisisgroup.org/rss",
    "SIPRI - Arms/Conflict": "https://www.sipri.org/rss",                   
    "CFR - Global Intelligence": "https://www.cfr.org/rss",                 
    "ACLED - Conflict News": "https://acleddata.com/feed/",                 
    "Real Clear Defense": "https://www.realcleardefense.com/index.xml",
    "US Dept of Defense": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945",
    "UK Ministry of Defence": "https://www.gov.uk/government/organisations/ministry-of-defence.atom",
    "NATO News": "https://www.nato.int/rss/rss_news_en.xml",                 
    "ODNI Intel Updates": "https://www.dni.gov/index.php/newsroom/press-releases?format=feed&type=rss"
}

ON_DEMAND_WEBSITES = {
    "Wiki - Bushcraft": "https://en.wikipedia.org/wiki/Bushcraft",
    "Wiki - Survival Skills": "https://en.wikipedia.org/wiki/Survival_skills",
    "Wiki - First Aid": "https://en.wikipedia.org/wiki/First_aid"
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0'
]

if not os.path.exists(DROPZONE_PATH):
    os.makedirs(DROPZONE_PATH)

# ==========================================
# 2. SHARED DATABASE CLIENT & SAFE LOCKS
# ==========================================
print(f"[+] Spinning up Shared ChromaDB Client at: {DB_PATH}")
try:
    client = chromadb.PersistentClient(path=DB_PATH)
    collection = client.get_or_create_collection(name="offline_knowledge_base")
except Exception as e:
    print(f"[!] CRITICAL: Database Error. Check your .env paths and ensure no strict locks exist: {e}")
    exit()

def safe_upsert(**kwargs):
    retries = 5
    for attempt in range(retries):
        try:
            collection.upsert(**kwargs)
            return
        except Exception as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                time.sleep(random.uniform(0.2, 0.8))
            else:
                raise e

def safe_delete(**kwargs):
    retries = 5
    for attempt in range(retries):
        try:
            collection.delete(**kwargs)
            return
        except Exception as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                time.sleep(random.uniform(0.2, 0.8))
            else:
                raise e

# ==========================================
# 3. CORE FUNCTIONS (SENTENCE-AWARE CHUNKING)
# ==========================================
def get_embedding(text):
    try:
        with requests.post(OLLAMA_URL, json={"model": EMBED_MODEL, "prompt": text}, timeout=30) as r:
            r.raise_for_status()
            return r.json()["embedding"]
    except Exception as e:
        print(f"[!] Embedding failed. Is Ollama running? Error: {e}")
        return None

def chunk_text(text, max_words=150):
    text = text.replace('\n', ' ')
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks = []
    current_chunk = []
    current_length = 0
    
    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        if current_length + len(words) > max_words and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = words
            current_length = len(words)
        else:
            current_chunk.extend(words)
            current_length += len(words)
            
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

def extract_and_memorize_url(url, source_name):
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        with requests.get(url, headers=headers, timeout=15) as response:
            response.raise_for_status() 
            soup = BeautifulSoup(response.content, 'html.parser')
            paragraphs = soup.find_all('p')
            full_text = " ".join([str(p.get_text()) for p in paragraphs])
            soup.decompose()
            
            chunks = chunk_text(full_text)
            for i, chunk in enumerate(chunks):
                chunk_id = f"{source_name}_part_{i}"
                context_chunk = f"SOURCE ({source_name}): {chunk}"
                meta = {"source": source_name, "category": "web_scrape", "ingested_at": time.strftime("%Y-%m-%d"), "unix_time": int(time.time())}
                
                if not collection.get(ids=[chunk_id])['ids']:
                    vector = get_embedding(context_chunk)
                    if vector:
                        safe_upsert(documents=[context_chunk], embeddings=[vector], ids=[chunk_id], metadatas=[meta])
                    
        print(f"[✅] Memorized {source_name}!")
        gc.collect()
    except Exception as e:
        print(f"[!] Scraping failed: {e}")

# ==========================================
# 4. DATABASE PRUNING 
# ==========================================
def prune_old_news(days=90):
    try:
        cutoff_time = int(time.time()) - (days * 24 * 3600)
        safe_delete(where={"$and": [{"category": "news"}, {"unix_time": {"$lt": cutoff_time}}]})
        print(f"[🗑️] Database pruned: All news articles older than {days} days purged.")
    except Exception as e:
        pass # Silent skip if empty

# ==========================================
# 5. BACKGROUND NEWS LOOP
# ==========================================
def start_daily_news_loop():
    print(f"\n[📰] Background News Scraper Active...")
    while True:
        prune_old_news(days=90)
        
        print(f"\n[🌍] {time.strftime('%I:%M %p')} - Fetching Intel...")
        total_new = 0
        
        for name, url in RSS_FEEDS.items():
            print(f"  -> Scanning {name}...")
            try:
                headers = {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5'
                }
                
                with requests.get(url, headers=headers, timeout=25) as r:
                    r.raise_for_status()
                    feed = feedparser.parse(r.content)
                
                if not feed.entries:
                    print(f"    [!] Warning: Access denied or empty feed for {name}")
                else:
                    for entry in feed.entries:
                        title = str(entry.get('title', 'No Title'))
                        summary = str(entry.get('summary', entry.get('description', '')))
                        clean_summary = BeautifulSoup(summary, "html.parser").get_text()
                        full_txt = f"NEWS ({name}): {title}. SUMMARY: {clean_summary}"
                        
                        meta = {"source": name, "category": "news", "ingested_at": time.strftime("%Y-%m-%d"), "unix_time": int(time.time())}
                        
                        if not collection.get(ids=[title])['ids']:
                            v = get_embedding(full_txt)
                            if v:
                                safe_upsert(documents=[full_txt], embeddings=[v], ids=[title], metadatas=[meta])
                                total_new += 1
                
                gc.collect()
                time.sleep(random.uniform(3.0, 8.0))
                
            except Exception as e:
                print(f"    [!] Connection Failure for {name}: {e}")
                
        print(f"[✅] Cycle complete. Articles added: {total_new}.")
        print(f"[zzZ] Sleeping for 2 hours...\n")
        time.sleep(7200)

# ==========================================
# MAIN MENU
# ==========================================
if __name__ == "__main__":
    news_thread = threading.Thread(target=start_daily_news_loop, daemon=True)
    news_thread.start()

    while True:
        print("\n" + "="*40 + "\n 🧠 MASTER LIBRARIAN MENU (RELEASE) \n" + "="*40)
        print("1. Process Dropzone (Manuals stay permanently)")
        print("2. Scrape Reference Sites")
        print("3. Exit")
        c = input("\nSelect: ").strip()
        if c == '1': 
            files = [f for f in os.listdir(DROPZONE_PATH) if f.endswith('.txt') or f.endswith('.pdf')]
            if not files:
                print("Dropzone is empty.")
            for filename in files:
                filepath = os.path.join(DROPZONE_PATH, filename)
                print(f"[📖] Reading {filename}...")
                full_text = ""
                if filename.endswith('.txt'):
                    with open(filepath, 'r', encoding='utf-8') as f: full_text = f.read()
                elif filename.endswith('.pdf'):
                    with open(filepath, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        for page in reader.pages:
                            extracted = page.extract_text()
                            if extracted: full_text += str(extracted) + " "
                
                chunks = chunk_text(full_text)
                for i, chunk in enumerate(chunks):
                    chunk_id = f"{filename}_part_{i}"
                    meta = {"source": filename, "category": "manual", "ingested_at": time.strftime("%Y-%m-%d"), "unix_time": int(time.time())}
                    if not collection.get(ids=[chunk_id])['ids']:
                        vector = get_embedding(chunk)
                        if vector: safe_upsert(documents=[chunk], embeddings=[vector], ids=[chunk_id], metadatas=[meta])
                os.rename(filepath, filepath + ".done")
                print(f"[✅] Memorized {filename}!")
        elif c == '2': 
            for s, u in ON_DEMAND_WEBSITES.items(): extract_and_memorize_url(u, s)
        elif c == '3': break
        gc.collect()