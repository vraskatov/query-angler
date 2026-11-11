"""Stichwortsuche mit BM25, als Ergänzung zur Vektorsuche.

Die Vektorsuche vergleicht Bedeutungen und übersieht dabei manchmal das Offensichtliche:
Bei "Wie sortiere ich absteigend?" stand "absteigend" wörtlich im passenden Chunk, er
landete trotzdem weit hinten. BM25 zählt Wörter und findet genau solche Fälle.

BM25 in einem Satz: Ein Chunk bekommt Punkte für jedes Wort der Frage, das er enthält;
seltene Wörter zählen mehr (IDF), Wiederholungen bringen immer weniger dazu (k1), und
lange Chunks werden etwas abgewertet, damit sie nicht allein durch ihre Länge gewinnen (b).

Der Index ist klein (unter 1000 Chunks) und wird beim Start aus Chroma im Speicher
aufgebaut, in deutlich unter einer Sekunde. Er muss deshalb nicht gespeichert werden.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import snowballstemmer

# Häufige Wörter ohne Aussagekraft für die Suche. Bewusst kurz gehalten.
# fmt: off
STOPWORDS = frozenset((
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer",
    "eines", "und", "oder", "aber", "wie", "was", "wer", "wo", "wann", "warum", "ich", "du",
    "er", "sie", "es", "wir", "ihr", "man", "mit", "von", "zu", "zum", "zur", "im", "in",
    "an", "am", "auf", "aus", "bei", "für", "nach", "über", "unter", "vor", "ist", "sind",
    "war", "wird", "werden", "kann", "können", "muss", "soll", "mich", "mir", "dir", "sich",
    "nicht", "auch", "als", "so", "dass", "da", "gibt", "mehr", "etwa", "alle", "alles",
))
# fmt: on

_stemmer = snowballstemmer.stemmer("german")


def tokenize(text: str) -> list[str]:
    """Kleinschreibung, Wörter, ohne Stoppwörter, auf den Wortstamm gebracht.

    Der Stamm sorgt dafür, dass "Abteilungen" auch "Abteilung" findet. Unregelmäßige
    Formen ("beginnen" / "beginnt") bleiben verschieden, eine bekannte Grenze.
    """
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOPWORDS]
    return _stemmer.stemWords(words)


@dataclass
class LexicalHit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float  # BM25-Punkte, nur innerhalb einer Suche vergleichbar


class LexicalIndex:
    def __init__(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._ids, self._texts, self._metas = ids, texts, metadatas
        self._k1, self._b = k1, b
        self._docs = [Counter(tokenize(t)) for t in texts]
        self._lengths = [sum(doc.values()) for doc in self._docs]
        self._avg_length = sum(self._lengths) / max(len(self._docs), 1)
        # Für jedes Wort: in welchen Chunks es vorkommt. So werden bei einer Suche
        # nur die Chunks angefasst, die mindestens ein Wort der Frage enthalten.
        self._postings: dict[str, list[int]] = defaultdict(list)
        for i, doc in enumerate(self._docs):
            for term in doc:
                self._postings[term].append(i)
        n = len(self._docs)
        self._idf = {
            term: math.log(1 + (n - len(docs) + 0.5) / (len(docs) + 0.5))
            for term, docs in self._postings.items()
        }

    @classmethod
    def from_collection(cls, collection: Any) -> "LexicalIndex":
        data = collection.get(include=["documents", "metadatas"])
        return cls(data["ids"], data["documents"], data["metadatas"])

    def search(self, question: str, k: int, roles: list[str]) -> list[LexicalHit]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(question)):
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i in self._postings[term]:
                tf = self._docs[i][term]
                norm = 1 - self._b + self._b * self._lengths[i] / self._avg_length
                scores[i] += idf * tf * (self._k1 + 1) / (tf + self._k1 * norm)
        ranked = sorted(
            (i for i in scores if self._metas[i].get("role") in roles),
            key=lambda i: scores[i],
            reverse=True,
        )
        return [
            LexicalHit(self._ids[i], self._texts[i], self._metas[i], scores[i]) for i in ranked[:k]
        ]
