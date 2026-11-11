"""Bausteine aus der Konfiguration zusammensetzen. Genutzt von Server, Kommandozeile und Evals."""

from queryangler import config
from queryangler.index.embedder import SentenceTransformerEmbedder
from queryangler.index.store import open_index
from queryangler.retrieval import Retriever


def load_retriever(
    rerank: bool = config.RERANK_ENABLED, hybrid: bool = config.HYBRID_ENABLED
) -> Retriever:
    """Index, Embedder, optional Stichwortsuche und Reranker, nur aus dem lokalen Speicher.

    Wirft IndexUnavailableError oder ModelUnavailableError mit einem Hinweis, was fehlt.
    """
    # Zuerst den Index öffnen: Das geht schnell und scheitert früh, falls er fehlt.
    collection = open_index(config.INDEX_DIR, config.EMBED_MODEL, config.EMBED_REVISION)
    embedder = SentenceTransformerEmbedder(
        config.EMBED_MODEL,
        revision=config.EMBED_REVISION,
        device=config.EMBED_DEVICE,
        offline=True,  # Suche und Server laden nie etwas aus dem Netz nach
    )
    reranker = None
    if rerank:
        from queryangler.index.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker(
            config.RERANK_MODEL,
            revision=config.RERANK_REVISION,
            device=config.EMBED_DEVICE,
            offline=True,
        )
    return Retriever(
        collection,
        embedder,
        reranker,
        candidates=config.RERANK_CANDIDATES,
        hybrid=hybrid,
        fusion=config.FUSION,  # type: ignore[arg-type]
        vector_weight=config.FUSION_VECTOR_WEIGHT,
    )
