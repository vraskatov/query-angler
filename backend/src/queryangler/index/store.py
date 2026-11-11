"""Vektorindex mit Chroma: aufbauen und öffnen."""

from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings

from queryangler.config import COLLECTION
from queryangler.index.embedder import Embedder

BATCH_SIZE = 256


class IndexUnavailableError(RuntimeError):
    """Index fehlt oder passt nicht zum Embedding-Modell."""


def _client(index_dir: Path) -> chromadb.ClientAPI:
    # Chroma sendet sonst anonyme Nutzungsstatistiken. Ein souveränes System tut das nicht.
    return chromadb.PersistentClient(
        path=str(index_dir), settings=Settings(anonymized_telemetry=False)
    )


def build_index(chunks: list[dict[str, Any]], embedder: Embedder, index_dir: Path) -> Collection:
    """Baut den Index komplett neu auf."""
    client = _client(index_dir)
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        COLLECTION,
        configuration={"hnsw": {"space": "cosine"}},
        # Wir berechnen die Embeddings selbst. Ohne diese Angabe würde Chroma
        # bei Bedarf sein eigenes, englisches Standardmodell verwenden.
        embedding_function=None,
        metadata=_model_metadata(embedder),
    )
    vectors = embedder.embed_documents([c["text"] for c in chunks])
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        collection.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
            embeddings=vectors[start : start + BATCH_SIZE],
        )
    return collection


def _model_metadata(embedder: Embedder) -> dict[str, str]:
    meta = {"embedding_model": embedder.name}
    revision = getattr(embedder, "revision", None)
    if revision:
        meta["embedding_revision"] = revision
    return meta


def open_index(index_dir: Path, embedder_name: str, revision: str | None = None) -> Collection:
    """Öffnet den Index und prüft, ob er mit demselben Modell gebaut wurde.

    Sucht man mit einem anderen Modell als dem, mit dem der Index gebaut wurde,
    entstehen sinnlose Treffer, ohne dass ein Fehler auftritt. Deshalb die Prüfung.
    """
    # Ohne diese Prüfung würde Chroma den Ordner stillschweigend leer anlegen.
    if not index_dir.exists():
        raise IndexUnavailableError(
            f"Kein Index in {index_dir}. Bitte zuerst `index build` ausführen."
        )
    client = _client(index_dir)
    if COLLECTION not in [c.name for c in client.list_collections()]:
        raise IndexUnavailableError(
            f"Kein Index in {index_dir}. Bitte zuerst `index build` ausführen."
        )
    collection = client.get_collection(COLLECTION)
    meta = collection.metadata or {}
    built_with = meta.get("embedding_model")
    built_revision = meta.get("embedding_revision")
    if built_with != embedder_name or (revision and built_revision not in (None, revision)):
        raise IndexUnavailableError(
            f"Der Index wurde mit '{built_with}' (Revision {built_revision}) gebaut, "
            f"gesucht wird mit '{embedder_name}' (Revision {revision}). "
            "Bitte den Index neu bauen."
        )
    return collection
