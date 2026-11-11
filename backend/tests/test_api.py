"""Tests für Tutor und API: echter Chroma-Index, aber Fake-Embedder und Fake-LLM."""

import pytest
from fakes import FakeEmbedder, FakeLLM
from fastapi.testclient import TestClient

from queryangler.api.app import create_app
from queryangler.index.store import IndexUnavailableError, build_index, open_index
from queryangler.llm import LLMError, LLMTimeoutError, LLMUnavailableError
from queryangler.retrieval import Retriever
from queryangler.tutor import Tutor


def chunk(id_, text, role="content"):
    return {
        "id": id_,
        "text": f"Kapitel: Gruppierungen\nAbschnitt: {id_}\n\n{text}",
        "metadata": {
            "chapter": "Gruppierungen",
            "section": id_,
            "role": role,
            "url": f"https://de.wikibooks.org/wiki/{id_}",
            "permalink": f"https://de.wikibooks.org/w/index.php?oldid=1#{id_}",
        },
    }


CHUNKS = [
    chunk("having", "HAVING filtert Gruppen nach GROUP BY"),
    chunk("where", "WHERE filtert Zeilen vor der Gruppierung"),
    chunk("loesung", "Lösung: HAVING mit GROUP BY und COUNT", role="solution"),
]


@pytest.fixture
def llm():
    return FakeLLM(text="HAVING filtert Gruppen [1].")


@pytest.fixture
def client(tmp_path, llm):
    embedder = FakeEmbedder()
    build_index(CHUNKS, embedder, tmp_path)

    def build_tutor():
        retriever = Retriever(open_index(tmp_path, embedder.name), embedder)
        return Tutor(retriever, llm, context_tokens=8192)

    with TestClient(create_app(build_tutor)) as client:  # "with" startet und beendet die App
        yield client


def test_ask_returns_answer_with_sources(client, llm):
    response = client.post("/ask", json={"question": "Was macht HAVING mit Gruppen?", "k": 2})

    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "HAVING filtert Gruppen [1]."
    assert data["model"] == "fake-llm"
    assert data["prompt_tokens"] == 123
    first, second = data["sources"]
    assert (first["number"], first["cited"]) == (1, True)
    assert (second["number"], second["cited"]) == (2, False)
    assert first["url"].startswith("https://de.wikibooks.org/")
    assert "Frage: Was macht HAVING mit Gruppen?" in llm.last_messages[1]["content"]


def test_socratic_mode_never_sees_solutions(client, llm):
    response = client.post(
        "/ask", json={"question": "HAVING GROUP BY COUNT Lösung", "mode": "sokratisch", "k": 3}
    )

    roles = {s["role"] for s in response.json()["sources"]}
    assert "solution" not in roles
    assert "Musterlösung" not in llm.last_messages[1]["content"]


@pytest.mark.parametrize(
    "body",
    [
        {"question": "?"},  # zu kurz
        {"question": "x" * 1001},  # zu lang
        {"question": "Was ist SQL?", "mode": "frei"},  # unbekannter Modus
        {"question": "Was ist SQL?", "k": 0},
        {"question": "     "},  # nur Leerzeichen, fällt erst im Retriever auf
    ],
)
def test_invalid_requests_are_rejected(client, body):
    assert client.post("/ask", json=body).status_code == 422


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (LLMUnavailableError("aus"), 503),
        (LLMTimeoutError("langsam"), 504),
        (LLMError("kaputt"), 502),
    ],
)
def test_llm_errors_map_to_status_codes(client, llm, error, status):
    llm.error = error
    response = client.post("/ask", json={"question": "Was macht HAVING?"})
    assert response.status_code == status


def test_health_ok(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "tutor_ready": True,
        "llm_reachable": True,
        "llm_model": "fake-llm",
        "detail": None,
    }


def test_server_starts_without_index_and_reports_why():
    def build_tutor():
        raise IndexUnavailableError("Kein Index. Bitte zuerst `index build` ausführen.")

    with TestClient(create_app(build_tutor)) as client:
        health = client.get("/health").json()
        ask = client.post("/ask", json={"question": "Was ist SQL?"})

    assert health["status"] == "eingeschränkt"
    assert "index build" in health["detail"]
    assert ask.status_code == 503
    assert "index build" in ask.json()["detail"]
