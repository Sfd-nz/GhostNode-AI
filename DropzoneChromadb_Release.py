import os
import re
import gc
import time
import json
import queue
import hashlib
import random
import logging
import threading
import requests
import feedparser
import chromadb
import PyPDF2
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ============================================================
# MEMORY LIBRARIAN V3 (OFFLINE INTEL INGESTION ENGINE)
# ============================================================
# - Multi-collection design
# - RSS -> Link follow -> Full scrape
# - URL dedupe (persistent)
# - Optional raw HTML archive
# - Background worker queue
# - Chunk overlap for improved RAG continuity
# ============================================================

# ============================================================
# 0. LOGGING SETUP
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("MemoryLibrarianV3")

# ============================================================
# 1. LOAD ENVIRONMENT VARIABLES
# ============================================================
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./chroma_db")

DROPZONE_PATH = os.getenv("DROPZONE_PATH", "./Dropzone")
PROCESSED_PATH = os.getenv("PROCESSED_PATH", "./Dropzone/processed")

ARCHIVE_HTML = os.getenv("ARCHIVE_HTML", "true").lower() == "true"
ARCHIVE_PATH = os.getenv("ARCHIVE_PATH", "./ArchiveHTML")

OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

NEWS_PRUNE_DAYS = int(os.getenv("NEWS_PRUNE_DAYS", "90"))
NEWS_LOOP_SLEEP = int(os.getenv("NEWS_LOOP_SLEEP_SECONDS", "7200"))

MAX_EMBED_CHARS = int(os.getenv("MAX_EMBED_CHARS", "6500"))
CHUNK_WORDS = int(os.getenv("CHUNK_WORDS", "170"))
CHUNK_OVERLAP_WORDS = int(os.getenv("CHUNK_OVERLAP_WORDS", "35"))

UPSERT_BATCH_SIZE = int(os.getenv("UPSERT_BATCH_SIZE", "32"))

WORKER_COUNT = int(os.getenv("INGEST_WORKERS", "3"))

DEDUP_INDEX_FILE = os.getenv("DEDUP_INDEX_FILE", "./ingested_urls.json")

RSS_FEEDS = {
    "UK/World News": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "US News": "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
    "NZ News": "https://www.rnz.co.nz/rss/news",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "Defense News": "https://www.defensenews.com/arc/outboundfeeds/rss/",
    "War on the Rocks": "https://warontherocks.com/feed",
    "The War Zone": "https://www.twz.com/feed/",
    "Naval News": "https://www.navalnews.com/feed/",
    "UK Defence Journal": "https://ukdefencejournal.org.uk/feed/",
    "Small Wars Journal": "https://smallwarsjournal.com/rss",
    "DefenseScoop": "https://defensescoop.com/feed/",
    "CrisisGroup - Updates": "https://www.crisisgroup.org/rss",
    "Real Clear Defense": "https://www.realcleardefense.com/index.xml",
    "US Dept of Defense": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945",
    "UK Ministry of Defence": "https://www.gov.uk/government/organisations/ministry-of-defence.atom",
}

ON_DEMAND_WEBSITES = {
    "Wiki - Bushcraft": "https://en.wikipedia.org/wiki/Bushcraft",
    "Wiki - Survival Skills": "https://en.wikipedia.org/wiki/Survival_skills",
    "Wiki - First Aid": "https://en.wikipedia.org/wiki/First_aid"
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
]

# ============================================================
# 2. DIRECTORY SETUP
# ============================================================
os.makedirs(DROPZONE_PATH, exist_ok=True)
os.makedirs(PROCESSED_PATH, exist_ok=True)
if ARCHIVE_HTML:
    os.makedirs(ARCHIVE_PATH, exist_ok=True)

# ============================================================
# 3. HTTP SESSION
# ============================================================
session = requests.Session()

def random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.7",
    }

# ============================================================
# 4. CHROMADB INIT (MULTI COLLECTION)
# ============================================================
log.info(f"[+] Starting ChromaDB PersistentClient at: {DB_PATH}")

try:
    chroma_client = chromadb.PersistentClient(path=DB_PATH)

    news_collection = chroma_client.get_or_create_collection(name="intel_news")
    manuals_collection = chroma_client.get_or_create_collection(name="intel_manuals")
    web_collection = chroma_client.get_or_create_collection(name="intel_web")

except Exception as e:
    log.error(f"[!] CRITICAL: ChromaDB startup failure: {e}")
    raise SystemExit(1)

# ============================================================
# 5. DEDUPE INDEX
# ============================================================
dedupe_lock = threading.Lock()

def load_dedupe_index():
    if not os.path.exists(DEDUP_INDEX_FILE):
        return {}
    try:
        with open(DEDUP_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_dedupe_index(index_data):
    try:
        with open(DEDUP_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2)
    except Exception as e:
        log.warning(f"[!] Failed saving dedupe index: {e}")

dedupe_index = load_dedupe_index()

def already_ingested(url):
    if not url:
        return False
    with dedupe_lock:
        return url in dedupe_index

def mark_ingested(url, meta):
    if not url:
        return
    with dedupe_lock:
        dedupe_index[url] = meta
        save_dedupe_index(dedupe_index)

# ============================================================
# 6. UTILS
# ============================================================
def sha_id(*parts):
    raw = "|".join([str(p) for p in parts if p is not None])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def clamp_text(text, max_chars):
    return text if len(text) <= max_chars else text[:max_chars]

def clean_html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    soup.decompose()
    return text

def sentence_chunks(text, max_words=170, overlap_words=35):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = []

    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue

        current_words = sum(len(x.split()) for x in current)

        if current_words + len(words) > max_words and current:
            chunk_text = " ".join(current).strip()
            if chunk_text:
                chunks.append(chunk_text)

            tail = chunk_text.split()[-overlap_words:] if overlap_words > 0 else []
            current = [" ".join(tail)] if tail else []
            current.append(sentence)
        else:
            current.append(sentence)

    final_chunk = " ".join(current).strip()
    if final_chunk:
        chunks.append(final_chunk)

    return chunks

# ============================================================
# 7. EMBEDDING + UPSERT
# ============================================================
def get_embedding(text):
    payload = {"model": EMBED_MODEL, "prompt": text}
    r = session.post(OLLAMA_EMBED_URL, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]

def embed_batch(text_list):
    vectors = []
    for i, txt in enumerate(text_list):
        try:
            vectors.append(get_embedding(txt))
        except Exception as e:
            log.warning(f"[!] Embed failed item {i}: {e}")
            vectors.append(None)
    return vectors

def safe_upsert_batch(collection, documents, ids, metadatas):
    if not documents:
        return 0

    total_upserted = 0

    for start in range(0, len(documents), UPSERT_BATCH_SIZE):
        batch_docs = documents[start:start+UPSERT_BATCH_SIZE]
        batch_ids = ids[start:start+UPSERT_BATCH_SIZE]
        batch_meta = metadatas[start:start+UPSERT_BATCH_SIZE]

        embed_inputs = [clamp_text(d, MAX_EMBED_CHARS) for d in batch_docs]
        vectors = embed_batch(embed_inputs)

        filtered_docs, filtered_ids, filtered_meta, filtered_vecs = [], [], [], []
        for d, i_, m, v in zip(batch_docs, batch_ids, batch_meta, vectors):
            if v is None:
                continue
            filtered_docs.append(d)
            filtered_ids.append(i_)
            filtered_meta.append(m)
            filtered_vecs.append(v)

        if not filtered_docs:
            continue

        retries = 6
        for attempt in range(retries):
            try:
                collection.upsert(
                    documents=filtered_docs,
                    embeddings=filtered_vecs,
                    ids=filtered_ids,
                    metadatas=filtered_meta
                )
                total_upserted += len(filtered_docs)
                break
            except Exception as e:
                if "locked" in str(e).lower() and attempt < retries - 1:
                    time.sleep(random.uniform(0.25, 1.0))
                else:
                    log.warning(f"[!] Upsert failed: {e}")

    return total_upserted

# ============================================================
# 8. RAW HTML ARCHIVE
# ============================================================
def archive_html(url, html):
    if not ARCHIVE_HTML:
        return
    try:
        safe_name = sha_id(url)[:20]
        filename = os.path.join(ARCHIVE_PATH, f"{safe_name}.html")
        with open(filename, "w", encoding="utf-8", errors="ignore") as f:
            f.write(html)
    except Exception as e:
        log.warning(f"[!] HTML archive failed: {e}")

# ============================================================
# 9. ARTICLE SCRAPER (RSS LINK FOLLOW)
# ============================================================
def scrape_article_text(url):
    """
    Scrapes readable article text from <p> tags.
    Returns extracted text and raw HTML.
    """
    try:
        r = session.get(url, headers=random_headers(), timeout=25)
        r.raise_for_status()

        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        # Remove scripts/styles/nav/footer garbage
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        paragraphs = soup.find_all("p")
        text = " ".join([p.get_text(" ", strip=True) for p in paragraphs])

        soup.decompose()

        return text.strip(), html

    except Exception as e:
        log.warning(f"[!] Article scrape failed: {url} | {e}")
        return "", ""

# ============================================================
# 10. INGESTION CORE
# ============================================================
def ingest_chunks(collection, category, source, title, url, text, extra_meta=None):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return 0

    unix_time = int(time.time())
    date_str = time.strftime("%Y-%m-%d")

    chunks = sentence_chunks(text, CHUNK_WORDS, CHUNK_OVERLAP_WORDS)
    if not chunks:
        return 0

    documents, ids, metadatas = [], [], []

    for idx, chunk in enumerate(chunks):
        doc_id = sha_id(category, source, title, url, idx, chunk[:120])

        meta = {
            "category": category,
            "source": source,
            "title": title or "",
            "url": url or "",
            "chunk_index": idx,
            "ingested_at": date_str,
            "unix_time": unix_time
        }

        if extra_meta:
            meta.update(extra_meta)

        documents.append(chunk)
        ids.append(doc_id)
        metadatas.append(meta)

    inserted = safe_upsert_batch(collection, documents, ids, metadatas)
    return inserted

# ============================================================
# 11. JOB QUEUE SYSTEM
# ============================================================
ingest_queue = queue.Queue()

def worker_loop(worker_id):
    log.info(f"[⚙️] Worker-{worker_id} online.")

    while True:
        job = ingest_queue.get()

        try:
            job_type = job.get("type")

            if job_type == "rss_article":
                handle_rss_article_job(job)

            elif job_type == "manual_file":
                handle_manual_job(job)

            elif job_type == "web_scrape":
                handle_web_job(job)

        except Exception as e:
            log.warning(f"[!] Worker-{worker_id} job error: {e}")

        ingest_queue.task_done()
        gc.collect()

def start_workers():
    for i in range(WORKER_COUNT):
        t = threading.Thread(target=worker_loop, args=(i+1,), daemon=True)
        t.start()

# ============================================================
# 12. JOB HANDLERS
# ============================================================
def handle_rss_article_job(job):
    source = job.get("source", "UnknownFeed")
    title = job.get("title", "No Title")
    url = job.get("url", "")
    summary = job.get("summary", "")

    if not url:
        return

    if already_ingested(url):
        log.info(f"[SKIP] Already ingested: {title}")
        return

    log.info(f"[RSS] Fetching full article: {title}")

    article_text, raw_html = scrape_article_text(url)

    if raw_html:
        archive_html(url, raw_html)

    # Always store summary even if article scrape failed
    combined_text = f"TITLE: {title}\nSOURCE: {source}\nSUMMARY: {summary}\nURL: {url}\n\nFULL TEXT:\n{article_text}"

    inserted = ingest_chunks(
        collection=news_collection,
        category="news",
        source=source,
        title=title,
        url=url,
        text=combined_text,
        extra_meta={"feed_source": source}
    )

    mark_ingested(url, {
        "title": title,
        "source": source,
        "unix_time": int(time.time())
    })

    log.info(f"[✅] NEWS memorized: {title} | chunks added: {inserted}")

def handle_manual_job(job):
    filepath = job.get("filepath")
    if not filepath or not os.path.exists(filepath):
        return

    filename = os.path.basename(filepath)
    log.info(f"[📖] Processing manual file: {filename}")

    full_text = ""

    try:
        if filename.lower().endswith(".txt"):
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                full_text = f.read()

        elif filename.lower().endswith(".pdf"):
            with open(filepath, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        full_text += extracted + " "

        inserted = ingest_chunks(
            collection=manuals_collection,
            category="manual",
            source=filename,
            title=filename,
            url="",
            text=full_text,
            extra_meta={"file_name": filename}
        )

        processed_name = f"{filename}.{int(time.time())}.done"
        dest = os.path.join(PROCESSED_PATH, processed_name)
        os.replace(filepath, dest)

        log.info(f"[✅] MANUAL memorized: {filename} | chunks added: {inserted}")

    except Exception as e:
        log.warning(f"[!] Manual ingest failed: {filename} | {e}")

def handle_web_job(job):
    source = job.get("source", "Web")
    url = job.get("url", "")

    if not url:
        return

    log.info(f"[🌐] Scraping website: {source}")

    if already_ingested(url):
        log.info(f"[SKIP] Already ingested website: {url}")
        return

    text, raw_html = scrape_article_text(url)

    if raw_html:
        archive_html(url, raw_html)

    inserted = ingest_chunks(
        collection=web_collection,
        category="web_scrape",
        source=source,
        title=source,
        url=url,
        text=text,
        extra_meta={"site_name": source}
    )

    mark_ingested(url, {
        "title": source,
        "source": "web_scrape",
        "unix_time": int(time.time())
    })

    log.info(f"[✅] WEB memorized: {source} | chunks added: {inserted}")

# ============================================================
# 13. RSS FETCHER (QUEUE LOADER)
# ============================================================
def fetch_rss_cycle():
    total_jobs = 0

    for feed_name, feed_url in RSS_FEEDS.items():
        log.info(f"[📰] Scanning RSS feed: {feed_name}")

        try:
            r = session.get(feed_url, headers=random_headers(), timeout=25)
            r.raise_for_status()

            feed = feedparser.parse(r.content)
            if not feed.entries:
                log.warning(f"[!] Empty feed: {feed_name}")
                continue

            for entry in feed.entries:
                title = str(entry.get("title", "No Title")).strip()
                link = str(entry.get("link", "")).strip()

                summary_raw = str(entry.get("summary", entry.get("description", "")))
                summary = clean_html_to_text(summary_raw)

                if not link:
                    continue

                if already_ingested(link):
                    continue

                ingest_queue.put({
                    "type": "rss_article",
                    "source": feed_name,
                    "title": title,
                    "url": link,
                    "summary": summary
                })
                total_jobs += 1

            time.sleep(random.uniform(1.5, 4.0))

        except Exception as e:
            log.warning(f"[!] RSS fetch failed: {feed_name} | {e}")

    log.info(f"[📥] RSS cycle queued {total_jobs} new article jobs.")
    return total_jobs

# ============================================================
# 14. PRUNING
# ============================================================
def prune_old_news(days=90):
    cutoff_time = int(time.time()) - (days * 24 * 3600)

    try:
        news_collection.delete(where={
            "$and": [
                {"category": "news"},
                {"unix_time": {"$lt": cutoff_time}}
            ]
        })
        log.info(f"[🗑️] Pruned NEWS older than {days} days.")
    except Exception as e:
        log.warning(f"[!] Prune failed: {e}")

# ============================================================
# 15. BACKGROUND RSS LOOP
# ============================================================
def background_rss_loop():
    time.sleep(10)
    log.info("[🛰️] Background RSS loop started.")

    while True:
        try:
            prune_old_news(days=NEWS_PRUNE_DAYS)
            fetch_rss_cycle()
        except Exception as e:
            log.warning(f"[!] RSS loop error: {e}")

        log.info(f"[zzZ] Sleeping {NEWS_LOOP_SLEEP} seconds...")
        time.sleep(NEWS_LOOP_SLEEP)

# ============================================================
# 16. DROPZONE PROCESSOR
# ============================================================
def process_dropzone():
    files = [
        f for f in os.listdir(DROPZONE_PATH)
        if f.lower().endswith(".txt") or f.lower().endswith(".pdf")
    ]

    if not files:
        log.info("Dropzone is empty.")
        return

    for filename in files:
        filepath = os.path.join(DROPZONE_PATH, filename)
        if os.path.isdir(filepath):
            continue

        ingest_queue.put({
            "type": "manual_file",
            "filepath": filepath
        })

    log.info(f"[📥] Dropzone queued {len(files)} manual ingestion jobs.")

# ============================================================
# 17. WEB SCRAPER (QUEUE LOADER)
# ============================================================
def scrape_reference_sites():
    for name, url in ON_DEMAND_WEBSITES.items():
        if already_ingested(url):
            continue

        ingest_queue.put({
            "type": "web_scrape",
            "source": name,
            "url": url
        })

    log.info("[📥] Reference site scraping jobs queued.")

# ============================================================
# 18. MENU UI
# ============================================================
def menu():
    while True:
        print("\n" + "=" * 65)
        print(" 🧠 MEMORY LIBRARIAN V3 (OFFLINE INTEL ENGINE) ")
        print("=" * 65)
        print("1. Process Dropzone Manuals (queue jobs)")
        print("2. Scrape Reference Sites (queue jobs)")
        print("3. Force RSS Cycle Now (queue jobs)")
        print("4. Show Queue Depth")
        print("5. Exit")

        choice = input("\nSelect: ").strip()

        if choice == "1":
            process_dropzone()

        elif choice == "2":
            scrape_reference_sites()

        elif choice == "3":
            fetch_rss_cycle()

        elif choice == "4":
            print(f"\n[📦] Queue depth: {ingest_queue.qsize()} jobs pending.")

        elif choice == "5":
            break

        else:
            print("Invalid option.")

        gc.collect()

# ============================================================
# ENTRYPOINT
# ============================================================
if __name__ == "__main__":
    log.info("=== MEMORY LIBRARIAN V3 BOOTING ===")

    start_workers()

    rss_thread = threading.Thread(target=background_rss_loop, daemon=True)
    rss_thread.start()

    menu()
