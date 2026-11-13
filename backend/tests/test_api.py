"""Tests für Tutor und API: echter Chroma-Index, aber Fake-Embedder und Fake-LLM."""

import pytest
from fakes import FakeEmbedder, FakeLLM
from fastapi.testclient import TestClient

from queryangler.api.app import create_app
from queryangler.index.store import IndexUnavailableError, build_index, open_index
from queryangler.llm import LLMError, LLMTimeoutError, LLMUnavailableError
from queryangler.prompt import NO_KNOWLEDGE
from queryangler.retrieval import Retriever
from queryangler.tutor import Tutor, cited_numbers


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
    assert "Frage der Lernenden: Was macht HAVING mit Gruppen?" in llm.last_messages[1]["content"]


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("HAVING filtert Gruppen [1].", {1}),
        ("Beispiel [1, 5]. Und [1,4].", {1, 4, 5}),  # Liste in einer Klammer
        ("Siehe [2][3].", {2, 3}),
        ("Kein Verweis, nur ein Array a[i].", set()),
    ],
)
def test_cited_numbers(text, expected):
    assert cited_numbers(text) == expected


# --- Prüfung der Ausgabe im Tutor ------------------------------------------------


def make_tutor(tmp_path, llm):
    embedder = FakeEmbedder()
    build_index(CHUNKS, embedder, tmp_path)
    return Tutor(Retriever(open_index(tmp_path, embedder.name), embedder), llm, context_tokens=8192)


def ask(tutor, question, mode):
    import asyncio

    return asyncio.run(tutor.ask(question, mode=mode))


def test_refusal_is_cut_after_the_sentence(tmp_path):
    llm = FakeLLM(text=f"{NO_KNOWLEDGE}\n\nIsolationsstufen sind Read Committed, …")
    answer = ask(make_tutor(tmp_path, llm), "Welche Isolationsstufen gibt es?", "erklaeren")
    assert answer.text == NO_KNOWLEDGE
    assert answer.checks == ["Absage: Zusatz nach dem Absagesatz entfernt"]
    assert not any(s.cited for s in answer.sources)


GOOD = "Hinweis: Überleg, wonach du zusammenfassen willst.\nFrage: Was ist bei dir eine Gruppe?"
LEAKY = "Hinweis: Nutze COUNT mit GROUP BY.\nFrage: Was zählst du?"


def test_socratic_clean_answer_needs_no_second_attempt(tmp_path):
    llm = FakeLLM(text=GOOD)
    answer = ask(make_tutor(tmp_path, llm), "Wie zähle ich Abteilungen pro Ort?", "sokratisch")
    assert (answer.text, answer.attempts, answer.checks) == (GOOD, 1, [])
    assert len(llm.calls) == 1


def test_socratic_leak_gets_a_second_attempt_with_reason(tmp_path):
    llm = FakeLLM(texts=[LEAKY, GOOD])
    answer = ask(make_tutor(tmp_path, llm), "Wie zähle ich Abteilungen pro Ort?", "sokratisch")
    assert answer.text == GOOD
    assert answer.attempts == 2
    assert "COUNT, GROUP BY" in answer.checks[0]
    retry = llm.calls[1]
    assert retry[-2] == {"role": "assistant", "content": LEAKY}
    assert "COUNT, GROUP BY" in retry[-1]["content"]


def test_socratic_keeps_the_better_answer_and_flags_it(tmp_path):
    worse = "Hier die Lösung: ```SELECT Ort, COUNT(*) FROM Abteilung GROUP BY Ort``` [1]"
    llm = FakeLLM(texts=[LEAKY, worse])
    answer = ask(make_tutor(tmp_path, llm), "Wie zähle ich Abteilungen pro Ort?", "sokratisch")
    assert answer.text == LEAKY
    assert "schlechter" in answer.checks[-1]


def test_explain_mode_is_not_checked_for_sql(tmp_path):
    llm = FakeLLM(text="Mit COUNT und GROUP BY [1].")
    answer = ask(make_tutor(tmp_path, llm), "Wie zähle ich Abteilungen pro Ort?", "erklaeren")
    assert answer.attempts == 1 and answer.checks == []


def test_socratic_refusal_in_second_attempt_wins(tmp_path):
    # Wie bei "Was ist das Leben?": erst ein gebastelter Hinweis, dann die Absage
    llm = FakeLLM(texts=["Hinweis: Denk an Trigger.\nFrage: Was passiert, wenn …?", NO_KNOWLEDGE])
    llm.texts[0] = "Hinweis: In den Beispielen steht etwas zu Triggern.\nFrage: Was passiert dann?"
    answer = ask(make_tutor(tmp_path, llm), "Was ist das Leben?", "sokratisch")
    assert answer.text == NO_KNOWLEDGE
    assert answer.attempts == 2
    assert "Absage im zweiten Versuch" in answer.checks[0]
    assert NO_KNOWLEDGE in llm.calls[1][-1]["content"]  # die Rückmeldung bietet den Ausweg an
