"""Retrieval: zu einer Frage die passendsten Chunks finden."""

from dataclasses import dataclass
from typing import Any

from chromadb.api.models.Collection import Collection

from queryangler.index.embedder import Embedder


@dataclass
class Hit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float  # Kosinus-Ähnlichkeit: 1 = gleiche Richtung, 0 = nichts gemeinsam


class Retriever:
    def __init__(self, collection: Collection, embedder: Embedder) -> None:
        self._collection = collection
        self._embedder = embedder

    def search(self, question: str, k: int = 5, include_solutions: bool = True) -> list[Hit]:
        """Die k ähnlichsten Chunks, bester zuerst.

        include_solutions=False blendet Musterlösungen aus, etwa wenn der Tutor
        nur Hinweise geben soll.
        """
        question = question.strip()
        if not question:
            raise ValueError("Die Frage ist leer.")
        where = None if include_solutions else {"role": {"$ne": "solution"}}
        result = self._collection.query(
            query_embeddings=[self._embedder.embed_query(question)],
            n_results=k,
            where=where,
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
