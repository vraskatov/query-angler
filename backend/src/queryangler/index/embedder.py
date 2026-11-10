"""Embedding-Modelle: Text -> Vektor.

Der Rest des Projekts kennt nur das Protokoll `Embedder`. Welches Modell dahinter
steckt, entscheidet die Konfiguration. In den Tests ersetzt ein einfaches
Ersatzmodell das echte, damit nichts heruntergeladen werden muss.
"""

from typing import Protocol


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """Modell über sentence-transformers, z. B. BAAI/bge-m3.

    BGE-M3 braucht für die dichte Suche keine Präfixe wie "query:" oder "passage:".
    Andere Modelle schon (multilingual-e5, Qwen3-Embedding). Wer das Modell wechselt,
    muss das hier berücksichtigen.
    """

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        # Import erst hier: lädt PyTorch, was ein paar Sekunden dauert und
        # für Tests und Befehle ohne Embeddings nicht nötig ist.
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name, device=device)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            batch_size=16,
            normalize_embeddings=True,  # Länge 1: Kosinus und Skalarprodukt fallen zusammen
            show_progress_bar=True,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()
