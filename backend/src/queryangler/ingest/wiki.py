"""Client für die MediaWiki-API von de.wikibooks.org."""

import time
from typing import Any, Self
from urllib.parse import quote

import httpx

from queryangler import __version__
from queryangler.config import BOOK_PREFIX, WIKI_API, WIKI_BASE

# Buchseiten ohne Lehrinhalt. Begründung: docs/ARCHITEKTUR.md, Abschnitt „Seitenauswahl“.
EXCLUDED_PREFIXES = (
    "Druckversion:",  # Zusammenfassungen anderer Kapitel, Inhalte wären doppelt im Index
    "Downloads",  # SQL-Skripte für die Beispieldatenbank, Code ohne Erklärung
    "Vorlage:",  # Navigationsvorlage des Buchs
)
EXCLUDED_CHAPTERS = {
    "Bibliografische Informationen",
    "Buchklappe",
    "Buchtitel",
    "Inhaltsverzeichnis",
    "Literaturverzeichnis",
    "Lizenz",
    "PDF-Version",
    "Projektorganisation",
    "Tipps zum Ausdrucken",
    "Weblinks",
    "Zielgruppe",
}

RETRY_STATUS = {429, 500, 502, 503, 504}


class WikiApiError(RuntimeError):
    """Die API hat geantwortet, meldet aber einen Fehler im JSON."""


def chapter_name(title: str) -> str:
    """'Einführung in SQL: Einleitung' -> 'Einleitung'"""
    return title.removeprefix(BOOK_PREFIX).strip()


def is_content_page(title: str) -> bool:
    if not title.startswith(BOOK_PREFIX):
        return False
    chapter = chapter_name(title)
    if not chapter or chapter in EXCLUDED_CHAPTERS:
        return False
    return not chapter.startswith(EXCLUDED_PREFIXES)


def _encode_title(title: str) -> str:
    return quote(title.replace(" ", "_"), safe=":")


def page_url(title: str) -> str:
    return f"{WIKI_BASE}/wiki/{_encode_title(title)}"


def permalink(revid: int) -> str:
    """Link auf genau die verwendete Fassung einer Seite."""
    return f"{WIKI_BASE}/w/index.php?oldid={revid}"


def history_url(title: str) -> str:
    """Versionsgeschichte einer Seite, dort stehen alle Autoren."""
    return f"{WIKI_BASE}/w/index.php?title={_encode_title(title)}&action=history"


def build_user_agent(contact: str) -> str:
    # Format nach Wikimedia-Richtlinie: Name/Version (Kontakt) Bibliothek/Version
    return f"query-angler/{__version__} ({contact}) httpx/{httpx.__version__}"


class WikiClient:
    """API-Client mit Pause zwischen Anfragen und Wiederholung bei Überlast."""

    def __init__(
        self,
        contact: str,
        *,
        delay: float = 1.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": build_user_agent(contact)},
            timeout=30.0,
            transport=transport,
        )
        self._delay = delay
        self._max_retries = max_retries
        self._last_request = 0.0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        params = {"format": "json", "formatversion": "2", **params}
        for attempt in range(self._max_retries + 1):
            self._wait_for_slot()
            last_try = attempt == self._max_retries
            try:
                response = self._client.get(WIKI_API, params=params)
            except httpx.TransportError:
                if last_try:
                    raise
                time.sleep(2**attempt)
                continue

            if response.status_code in RETRY_STATUS and not last_try:
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else 2**attempt)
                continue

            response.raise_for_status()
            data = response.json()
            # MediaWiki meldet viele Fehler mit Status 200 und einem Feld "error"
            if "error" in data:
                err = data["error"]
                raise WikiApiError(f"{err.get('code')}: {err.get('info')}")
            return data
        raise AssertionError("unreachable")  # für Typprüfer: jeder Weg endet oben

    def _wait_for_slot(self) -> None:
        # monotonic statt time(): springt nie zurück (Zeitumstellung, Uhrsynchronisation)
        wait = self._delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def list_book_pages(self) -> list[str]:
        """Alle Seitentitel mit dem Buchpräfix, ohne Weiterleitungen."""
        params = {
            "action": "query",
            "list": "allpages",
            "apprefix": BOOK_PREFIX,
            "apnamespace": 0,
            "apfilterredir": "nonredirects",
            "aplimit": "max",
        }
        titles: list[str] = []
        cont: dict[str, Any] = {}
        while True:  # Paginierung: weiter, solange die API ein "continue" schickt
            data = self._get({**params, **cont})
            titles.extend(page["title"] for page in data["query"]["allpages"])
            if "continue" not in data:
                return titles
            cont = data["continue"]

    def fetch_page(self, title: str) -> dict[str, Any]:
        """Gerendertes HTML einer Seite (Vorlagen aufgelöst) plus Revisions-ID."""
        data = self._get(
            {
                "action": "parse",
                "page": title,
                "prop": "text|revid",
                "redirects": 1,
                "disableeditsection": 1,
                "disabletoc": 1,
            }
        )
        parsed = data["parse"]
        return {
            "title": parsed["title"],
            "pageid": parsed["pageid"],
            "revid": parsed["revid"],
            "html": parsed["text"],
        }

    def license(self) -> dict[str, str]:
        """Lizenz des Wikis laut API (Felder 'text' und 'url')."""
        data = self._get({"action": "query", "meta": "siteinfo", "siprop": "rightsinfo"})
        return data["query"]["rightsinfo"]
