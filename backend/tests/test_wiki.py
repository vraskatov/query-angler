"""Tests des API-Clients und der Seitenauswahl, ohne Netzwerk."""

from pathlib import Path

import httpx
import pytest

from queryangler.config import BOOK_PREFIX
from queryangler.ingest import wiki
from queryangler.ingest.util import slugify
from queryangler.ingest.wiki import WikiClient, build_user_agent, is_content_page

PAGE_LIST = Path(__file__).parent / "fixtures" / "book_pages.txt"


def real_titles() -> list[str]:
    lines = PAGE_LIST.read_text(encoding="utf-8").splitlines()
    return [f"{BOOK_PREFIX} {line}" for line in lines if line and not line.startswith("#")]


# -- Seitenauswahl --------------------------------------------------------------


def test_echte_seitenliste_ergibt_38_kapitel():
    titles = real_titles()
    assert len(titles) == 68
    assert sum(is_content_page(t) for t in titles) == 38


@pytest.mark.parametrize(
    "chapter",
    [
        "Druckversion: Grundlagen",
        "Downloads",
        "Downloads: CreateScript-SQLite.sql",
        "Vorlage: Navigation",
        "Inhaltsverzeichnis",
        "Lizenz",
        "Zielgruppe",
        "Projektorganisation",
    ],
)
def test_ausgeschlossene_seiten(chapter):
    assert not is_content_page(f"{BOOK_PREFIX} {chapter}")


@pytest.mark.parametrize(
    "chapter",
    [
        "Einleitung",
        "Beispieldatenbank",
        "OUTER JOIN",
        "Funktionen (2)",
        "Änderung der Datenbankstruktur",
    ],
)
def test_lehrinhalte_bleiben(chapter):
    assert is_content_page(f"{BOOK_PREFIX} {chapter}")


def test_fremde_seiten_und_leerer_titel():
    assert not is_content_page("Anderes Buch: Einleitung")
    assert not is_content_page(BOOK_PREFIX)


# -- Hilfsfunktionen ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Einfache Tabellenverknüpfung", "einfache-tabellenverknuepfung"),
        ("Größe & Maße", "groesse-masse"),
        ("DML (1) - Daten abfragen", "dml-1-daten-abfragen"),
        ("???", "seite"),
    ],
)
def test_slugify(text, slug):
    assert slugify(text) == slug


def test_user_agent_enthaelt_kontakt():
    assert build_user_agent("test@example.org").startswith("query-angler/")
    assert "(test@example.org)" in build_user_agent("test@example.org")


# -- Client mit simulierter API -------------------------------------------------


def test_paginierung_und_user_agent_im_header():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "apcontinue" not in request.url.params:
            first_page = {
                "continue": {"apcontinue": "Einführung in SQL: M", "continue": "-||"},
                "query": {"allpages": [{"title": "Einführung in SQL: A"}]},
            }
            return httpx.Response(200, json=first_page)
        last_page = {"query": {"allpages": [{"title": "Einführung in SQL: M"}]}}
        return httpx.Response(200, json=last_page)

    with WikiClient("test@example.org", delay=0, transport=httpx.MockTransport(handler)) as client:
        titles = client.list_book_pages()

    assert titles == ["Einführung in SQL: A", "Einführung in SQL: M"]
    assert len(requests) == 2
    assert requests[1].url.params["apcontinue"] == "Einführung in SQL: M"
    assert "test@example.org" in requests[0].headers["User-Agent"]


def test_wiederholung_bei_503(monkeypatch):
    monkeypatch.setattr(wiki.time, "sleep", lambda _seconds: None)  # nicht wirklich warten
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"query": {"rightsinfo": {"text": "CC", "url": "u"}}})

    with WikiClient("x", delay=0, transport=httpx.MockTransport(handler)) as client:
        assert client.license() == {"text": "CC", "url": "u"}
    assert calls["n"] == 2


def test_api_fehler_wird_gemeldet():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": "missingtitle", "info": "nope"}})

    transport = httpx.MockTransport(handler)
    with (
        WikiClient("x", delay=0, transport=transport) as client,
        pytest.raises(wiki.WikiApiError, match="missingtitle"),
    ):
        client.fetch_page("Gibt es nicht")
