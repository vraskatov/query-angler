"""Retrieval: zu einer Frage die passendsten Chunks finden.

Bis zu drei Stufen:
1. Vektorsuche: vergleicht Bedeutungen. Frage und Chunk werden getrennt in Vektoren
   verwandelt und über den Winkel verglichen.
2. Hybride Suche (Standard): zusätzlich eine Stichwortsuche (BM25). Beide Ranglisten
   werden mit Reciprocal Rank Fusion zusammengeführt.
3. Reranker (optional, Standard aus): bewertet die besten Kandidaten noch einmal, indem
   er Frage und Chunk gemeinsam liest. Genauer, aber auf der CPU langsam.
"""

from dataclasses import dataclass, replace
from itertools import zip_longest
from typing import Any, Literal, Protocol

from chromadb.api.models.Collection import Collection

from queryangler.index.embedder import Embedder
from queryangler.index.lexical import LexicalIndex

RRF_K = 60  # üblicher Wert aus der Literatur; dämpft den Vorsprung der allerersten Plätze

Fusion = Literal["rrf", "gewichtet", "reissverschluss"]


@dataclass
class Hit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float  # Kosinus-Ähnlichkeit: 1 = gleiche Richtung, 0 = nichts gemeinsam
    lexical_score: float | None = None  # BM25-Punkte, falls die Stichwortsuche ihn fand
    fusion_score: float | None = None  # Punkte aus der hybriden Suche, falls verwendet
    rerank_score: float | None = None  # Bewertung des Rerankers, falls verwendet


class Reranker(Protocol):
    name: str

    def rerank(self, question: str, hits: list[Hit]) -> list[Hit]:
        """Gibt die Treffer mit rerank_score zurück, bester zuerst."""
        ...


class Retriever:
    def __init__(
        self,
        collection: Collection,
        embedder: Embedder,
        reranker: Reranker | None = None,
        candidates: int = 20,
        hybrid: bool = False,
        fusion: Fusion = "rrf",
        vector_weight: float = 0.7,
    ) -> None:
        self._collection = collection
        self._embedder = embedder
        self.reranker = reranker
        self.candidates = candidates
        self.lexical = LexicalIndex.from_collection(collection) if hybrid else None
        self.fusion: Fusion = fusion
        self.vector_weight = vector_weight

    def search(self, question: str, k: int = 5, include_solutions: bool = True) -> list[Hit]:
        """Die k passendsten Chunks, bester zuerst."""
        n = max(k, self.candidates) if self.reranker else k
        if self.lexical:
            hits = self.hybrid_search(question, n, include_solutions)
        else:
            hits = self.vector_search(question, n, include_solutions)
        if self.reranker:
            hits = self.reranker.rerank(question.strip(), hits)[:k]
        return hits

    def vector_search(self, question: str, k: int = 5, include_solutions: bool = True) -> list[Hit]:
        """Nur die Vektorsuche.

        Übungsstellungen werden nie gefunden: Sie ähneln Fragen von Lernenden sehr
        ("Geben Sie alle Abteilungen aus ..."), enthalten aber kein Wissen und haben in
        den Evals den Lehrtext verdrängt. Musterlösungen nur mit include_solutions=True.
        """
        return self._vector_search(self._query_vector(question), k, _roles(include_solutions))

    def hybrid_search(self, question: str, k: int = 5, include_solutions: bool = True) -> list[Hit]:
        """Vektor- und Stichwortsuche, zusammengeführt nach self.fusion (siehe fuse)."""
        vector_hits, lexical_hits = self.candidate_lists(question, include_solutions)
        return fuse(vector_hits, lexical_hits, self.fusion, k, self.vector_weight)

    def candidate_lists(
        self, question: str, include_solutions: bool = True
    ) -> tuple[list[Hit], list[Hit]]:
        """Je `candidates` Treffer aus Vektor- und Stichwortsuche, jede Liste bester zuerst."""
        if self.lexical is None:
            raise RuntimeError("Retriever ohne Stichwortindex (hybrid=False).")
        query_vector = self._query_vector(question)
        roles = _roles(include_solutions)
        vector_hits = self._vector_search(query_vector, self.candidates, roles)
        lexical = self.lexical.search(question, self.candidates, roles)

        # Für Treffer nur aus der Stichwortsuche die Kosinus-Ähnlichkeit nachrechnen,
        # damit jeder Treffer denselben Ähnlichkeitswert trägt.
        cosine = {h.id: h.score for h in vector_hits}
        missing = [h.id for h in lexical if h.id not in cosine]
        if missing:
            stored = self._collection.get(ids=missing, include=["embeddings"])
            for id_, vector in zip(stored["ids"], stored["embeddings"], strict=True):
                cosine[id_] = float(sum(a * b for a, b in zip(query_vector, vector, strict=True)))
        lexical_hits = [
            Hit(h.id, h.text, h.metadata, score=cosine[h.id], lexical_score=h.score)
            for h in lexical
        ]
        bm25 = {h.id: h.score for h in lexical}
        for h in vector_hits:
            h.lexical_score = bm25.get(h.id)
        return vector_hits, lexical_hits

    def _query_vector(self, question: str) -> list[float]:
        question = question.strip()
        if not question:
            raise ValueError("Die Frage ist leer.")
        return self._embedder.embed_query(question)

    def _vector_search(self, query_vector: list[float], k: int, roles: list[str]) -> list[Hit]:
        result = self._collection.query(
            query_embeddings=[query_vector],
            n_results=k,
            where={"role": {"$in": roles}},
            include=["documents", "metadatas", "distances"],
        )
        # Chroma liefert Listen pro Anfrage. Wir stellen nur eine, also jeweils [0].
        return [
            Hit(id=id_, text=text, metadata=meta, score=1.0 - distance)
            for id_, text, meta, distance in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
                strict=True,
            )
        ]


def _roles(include_solutions: bool) -> list[str]:
    return ["content", "solution"] if include_solutions else ["content"]


def fuse(
    vector_hits: list[Hit],
    lexical_hits: list[Hit],
    method: Fusion,
    k: int,
    vector_weight: float = 0.7,
) -> list[Hit]:
    """Zwei Ranglisten zu einer zusammenführen.

    rrf:             Platz r bringt 1 / (60 + r) Punkte je Liste, die Punkte werden addiert.
                     Nur Plätze zählen. Bevorzugt Chunks, die in beiden Listen vorkommen.
    gewichtet:       Werte beider Listen auf 0 bis 1 normiert (Min-Max), dann
                     vector_weight * Vektor + (1 - vector_weight) * BM25.
                     Fehlt ein Chunk in einer Liste, zählt dort 0.
    reissverschluss: abwechselnd der nächste Treffer aus Vektor- und Stichwortsuche,
                     ohne Doppelte. Ein klarer BM25-Treffer kommt so sicher nach vorn.
    """
    by_id = {h.id: h for h in [*lexical_hits, *vector_hits]}  # Vektortreffer haben Vorrang
    if method == "rrf":
        points: dict[str, float] = {}
        for ranking in (vector_hits, lexical_hits):
            for rank, h in enumerate(ranking, start=1):
                points[h.id] = points.get(h.id, 0.0) + 1 / (RRF_K + rank)
    elif method == "gewichtet":
        v = _normalized({h.id: h.score for h in vector_hits})
        lex = _normalized({h.id: h.lexical_score or 0.0 for h in lexical_hits})
        points = {
            id_: vector_weight * v.get(id_, 0.0) + (1 - vector_weight) * lex.get(id_, 0.0)
            for id_ in by_id
        }
    elif method == "reissverschluss":
        order: list[str] = []
        for pair in zip_longest(vector_hits, lexical_hits):
            for h in pair:
                if h is not None and h.id not in order:
                    order.append(h.id)
        points = {id_: 1 / rank for rank, id_ in enumerate(order, start=1)}
    else:
        raise ValueError(f"Unbekanntes Fusionsverfahren: {method}")
    best = sorted(points, key=lambda id_: points[id_], reverse=True)[:k]
    return [_with_fusion(by_id[id_], points[id_]) for id_ in best]


def _normalized(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    if high == low:
        return {id_: 1.0 for id_ in scores}
    return {id_: (s - low) / (high - low) for id_, s in scores.items()}


def _with_fusion(hit: Hit, fusion_score: float) -> Hit:
    return replace(hit, fusion_score=fusion_score)
