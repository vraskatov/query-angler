"""Tests für Index und Retrieval mit einem Ersatzmodell statt des echten Embedding-Modells.

Das Ersatzmodell zählt Wörter (Bag of Words). Es versteht keine Bedeutung, aber
Texte mit gemeinsamen Wörtern liegen nah beieinander. Das reicht, um Index,
Filter und Reihenfolge zu prüfen, ohne ein Modell herunterzuladen.
"""

import pytest
from fakes import FakeEmbedder, FakeReranker

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


def test_uebungsstellungen_werden_nie_gefunden(retriever):
    # Die Übung enthält wörtlich die Frage, wird aber trotzdem nicht geliefert
    hits = retriever.search("Übung: Schreiben Sie einen JOIN", k=5)
    assert "b#002" not in [h.id for h in hits]
    assert {h.metadata["role"] for h in hits} <= {"content", "solution"}


def test_reranker_sortiert_kandidaten_neu_und_kuerzt(tmp_path):
    embedder = FakeEmbedder()
    build_index(CHUNKS, embedder, tmp_path)
    retriever = Retriever(
        open_index(tmp_path, embedder.name), embedder, FakeReranker(), candidates=4
    )

    hits = retriever.search("Gruppen filtern mit HAVING nach GROUP BY", k=2)

    assert len(hits) == 2
    assert hits[0].id == "a#001"
    assert all(h.rerank_score is not None for h in hits)
    assert hits[0].rerank_score >= hits[1].rerank_score
    # Der Ähnlichkeitswert der Vektorsuche bleibt erhalten
    assert 0.0 < hits[0].score <= 1.0 + 1e-6


def test_hybride_suche_findet_auch_reine_stichworttreffer(tmp_path):
    # Der Ersatz-Embedder zählt Wörter in ihrer genauen Form: "Abteilungen" und "Abteilung"
    # sind für ihn verschiedene Wörter, für die Stichwortsuche (Wortstamm) dasselbe.
    # Die y-Chunks teilen dagegen Füllwörter mit der Frage und gewinnen die Vektorsuche.
    chunks = [
        chunk("x#001", "Abteilung zählen"),
        *[chunk(f"y#{i:03d}", f"Wie gibt es Nummer {i}") for i in range(8)],
    ]
    embedder = FakeEmbedder()
    build_index(chunks, embedder, tmp_path)
    collection = open_index(tmp_path, embedder.name)
    vector_only = Retriever(collection, embedder, candidates=3)
    hybrid = Retriever(collection, embedder, candidates=3, hybrid=True)

    question = "Wie viele Abteilungen gibt es?"
    assert "x#001" not in [h.id for h in vector_only.search(question, k=3)]
    hits = {h.id: h for h in hybrid.search(question, k=3)}
    assert "x#001" in hits
    assert hits["x#001"].fusion_score is not None
    assert hits["x#001"].score == pytest.approx(0.0, abs=1e-6)  # Kosinus nachgerechnet


@pytest.mark.parametrize("method", ["rrf", "gewichtet", "reissverschluss"])
def test_fusionsverfahren(method):
    from queryangler.retrieval import Hit, fuse

    def hit(id_, score, lexical=None):
        return Hit(id_, id_, {}, score=score, lexical_score=lexical)

    vector = [hit("v1", 0.9), hit("v2", 0.8), hit("beide", 0.7)]
    lexical = [hit("l1", 0.2, 9.0), hit("beide", 0.7, 5.0), hit("l2", 0.1, 1.0)]

    fused = fuse(vector, lexical, method, k=4, vector_weight=0.5)
    ids = [h.id for h in fused]

    assert len(ids) == len(set(ids)) == 4  # keine Doppelten
    assert all(h.fusion_score is not None for h in fused)
    if method == "rrf":
        assert ids[0] == "beide"  # in beiden Listen: gewinnt bei RRF
    if method == "reissverschluss":
        assert ids == ["v1", "l1", "v2", "beide"]
    if method == "gewichtet":
        assert set(ids[:2]) == {"v1", "l1"}  # die jeweils Besten
