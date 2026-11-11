"""Tests für Index und Retrieval mit einem Ersatzmodell statt des echten Embedding-Modells.

Das Ersatzmodell zählt Wörter (Bag of Words). Es versteht keine Bedeutung, aber
Texte mit gemeinsamen Wörtern liegen nah beieinander. Das reicht, um Index,
Filter und Reihenfolge zu prüfen, ohne ein Modell herunterzuladen.
"""

import pytest
from fakes import FakeEmbedder

from queryangler.index.store import IndexUnavailableError, build_index, open_index
from queryangler.retrieval import Retriever


def chunk(id_, text, role="content", exercise=0):
    return {
        "id": id_,
        "text": text,
        "metadata": {"chapter": "Test", "section": "S", "role": role, "exercise": exercise},
    }


CHUNKS = [
    chunk("a#001", "HAVING filtert Gruppen nach GROUP BY"),
    chunk("a#002", "WHERE filtert Zeilen vor der Gruppierung"),
    chunk("b#001", "INNER JOIN verknüpft zwei Tabellen über eine Bedingung"),
    chunk("b#002", "Übung: Schreiben Sie einen JOIN", role="exercise", exercise=1),
    chunk("b#003", "Lösung: SELECT mit JOIN über zwei Tabellen", role="solution", exercise=1),
]


@pytest.fixture
def retriever(tmp_path):
    embedder = FakeEmbedder()
    build_index(CHUNKS, embedder, tmp_path)
    return Retriever(open_index(tmp_path, embedder.name), embedder)


def test_bester_treffer_zuerst(retriever):
    hits = retriever.search("Was macht HAVING mit Gruppen?", k=3)
    assert hits[0].id == "a#001"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 + 1e-6 for s in scores)


def test_loesungen_ausblenden(retriever):
    with_solutions = retriever.search("JOIN über zwei Tabellen", k=5)
    without = retriever.search("JOIN über zwei Tabellen", k=5, include_solutions=False)
    assert "b#003" in [h.id for h in with_solutions]
    assert "b#003" not in [h.id for h in without]
    assert all(h.metadata["role"] != "solution" for h in without)


def test_treffer_enthalten_text_und_metadaten(retriever):
    hit = retriever.search("INNER JOIN", k=1)[0]
    assert hit.text.startswith("INNER JOIN")
    assert hit.metadata["chapter"] == "Test"


def test_leere_frage(retriever):
    with pytest.raises(ValueError):
        retriever.search("   ")


def test_neuaufbau_ersetzt_den_index(tmp_path):
    embedder = FakeEmbedder()
    build_index(CHUNKS, embedder, tmp_path)
    collection = build_index(CHUNKS[:2], embedder, tmp_path)
    assert collection.count() == 2


def test_fehlender_index(tmp_path):
    with pytest.raises(IndexUnavailableError, match="Kein Index"):
        open_index(tmp_path, "fake-bag-of-words")


def test_anderes_modell_wird_abgelehnt(tmp_path):
    build_index(CHUNKS, FakeEmbedder(), tmp_path)
    with pytest.raises(IndexUnavailableError, match="neu bauen"):
        open_index(tmp_path, "BAAI/bge-m3")
