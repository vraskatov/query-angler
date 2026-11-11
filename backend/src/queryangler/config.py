"""Pfade und Konstanten. Pfade lassen sich per Umgebungsvariable überschreiben."""

import os
from pathlib import Path

# backend/src/queryangler/config.py -> parents[3] ist die Wurzel des Repos
REPO_ROOT = Path(__file__).resolve().parents[3]

DATA_DIR = Path(os.environ.get("QUERYANGLER_DATA_DIR", REPO_ROOT / "data"))
EVALS_DIR = REPO_ROOT / "evals"  # Testfragen und Ergebnisse, versioniert
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
CHUNKS_FILE = DATA_DIR / "chunks" / "chunks.jsonl"
INDEX_DIR = DATA_DIR / "index"
MANIFEST_FILE = RAW_DIR / "manifest.json"
ATTRIBUTION_FILE = Path(
    os.environ.get("QUERYANGLER_ATTRIBUTION_FILE", REPO_ROOT / "ATTRIBUTION.md")
)

WIKI_BASE = "https://de.wikibooks.org"
WIKI_API = f"{WIKI_BASE}/w/api.php"
BOOK_TITLE = "Einführung in SQL"
BOOK_PREFIX = f"{BOOK_TITLE}:"
BOOK_AUTHOR = "Jürgen Thomas"  # laut Titelseite; weitere Autoren in den Versionsgeschichten

# Embeddings und Index
EMBED_MODEL = os.environ.get("QUERYANGLER_EMBED_MODEL", "BAAI/bge-m3")
# Feste Fassung des Modells auf Hugging Face, wie die Revisions-IDs der Kapitel
EMBED_REVISION = os.environ.get(
    "QUERYANGLER_EMBED_REVISION", "5617a9f61b028005a4858fdac845db406aefb181"
)
EMBED_DEVICE = os.environ.get("QUERYANGLER_EMBED_DEVICE", "cpu")  # GPU bleibt für das LLM frei
COLLECTION = "query-angler"

# Hybride Suche: Vektorsuche plus Stichwortsuche (BM25). Standard aus: In der Eval schlug
# keines der drei Fusionsverfahren die reine Vektorsuche (Trefferquote@5 61 %); der
# Reißverschluss kam gleich, RRF und Gewichtung lagen darunter. Ohne messbaren Gewinn
# bleibt das einfachere System.
HYBRID_ENABLED = os.environ.get("QUERYANGLER_HYBRID", "0") != "0"
# Wie die beiden Ranglisten zusammengeführt werden: rrf, gewichtet oder reissverschluss
FUSION = os.environ.get("QUERYANGLER_FUSION", "reissverschluss")
FUSION_VECTOR_WEIGHT = float(os.environ.get("QUERYANGLER_FUSION_VECTOR_WEIGHT", "0.7"))

# Reranker: bewertet die besten Kandidaten noch einmal genauer. Standard aus: In der Eval
# brachte er +3 Prozentpunkte Trefferquote@5 für 8 Sekunden pro Frage auf der CPU.
RERANK_ENABLED = os.environ.get("QUERYANGLER_RERANK", "0") != "0"
RERANK_MODEL = os.environ.get("QUERYANGLER_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
# Feste Fassung wie bei EMBED_REVISION
RERANK_REVISION = os.environ.get(
    "QUERYANGLER_RERANK_REVISION", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
)
RERANK_CANDIDATES = int(os.environ.get("QUERYANGLER_RERANK_CANDIDATES", "20"))

# Sprachmodell über eine OpenAI-kompatible Schnittstelle (Ollama, Mistral, vLLM ...)
LLM_BASE_URL = os.environ.get("QUERYANGLER_LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("QUERYANGLER_LLM_MODEL", "query-angler")  # aus ollama/Modelfile
LLM_API_KEY = os.environ.get("QUERYANGLER_LLM_API_KEY")  # nur für Anbieter mit Schlüssel
LLM_TEMPERATURE = float(os.environ.get("QUERYANGLER_LLM_TEMPERATURE", "0.2"))
LLM_TIMEOUT = float(os.environ.get("QUERYANGLER_LLM_TIMEOUT", "120"))
# Muss zum Kontextfenster des Modells passen (num_ctx im Modelfile)
LLM_CONTEXT_TOKENS = int(os.environ.get("QUERYANGLER_LLM_CONTEXT_TOKENS", "8192"))
