"""Pfade und Konstanten. Pfade lassen sich per Umgebungsvariable überschreiben."""

import os
from pathlib import Path

# backend/src/queryangler/config.py -> parents[3] ist die Wurzel des Repos
REPO_ROOT = Path(__file__).resolve().parents[3]

DATA_DIR = Path(os.environ.get("QUERYANGLER_DATA_DIR", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
MANIFEST_FILE = RAW_DIR / "manifest.json"
ATTRIBUTION_FILE = Path(
    os.environ.get("QUERYANGLER_ATTRIBUTION_FILE", REPO_ROOT / "ATTRIBUTION.md")
)

WIKI_BASE = "https://de.wikibooks.org"
WIKI_API = f"{WIKI_BASE}/w/api.php"
BOOK_TITLE = "Einführung in SQL"
BOOK_PREFIX = f"{BOOK_TITLE}:"
BOOK_AUTHOR = "Jürgen Thomas"  # laut Titelseite; weitere Autoren in den Versionsgeschichten
