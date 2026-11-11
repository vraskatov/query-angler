"""Reranker: Frage und Chunk gemeinsam bewerten (Cross-Encoder).

Das Embedding-Modell verwandelt Frage und Chunk getrennt in Vektoren; was in beiden
steht, erfährt es nie gleichzeitig. Ein Cross-Encoder liest beide zusammen und kann
so erkennen, ob der Chunk die Frage tatsächlich beantwortet. Dafür muss er jedes Paar
einzeln rechnen, deshalb nur für die besten Kandidaten der Vektorsuche.

Modell: BAAI/bge-reranker-v2-m3 (Apache 2.0), dieselbe Familie wie BGE-M3, mehrsprachig.
"""

from dataclasses import replace

from queryangler.index.embedder import ModelUnavailableError, forbid_hub_access
from queryangler.retrieval import Hit


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str,
        *,
        revision: str | None = None,
        device: str = "cpu",
        offline: bool = False,
        max_length: int = 512,  # Token je Paar; begrenzt die Rechenzeit auf der CPU
    ) -> None:
        if offline:
            forbid_hub_access()
        from sentence_transformers import CrossEncoder

        self.name = model_name
        self.revision = revision
        try:
            self._model = CrossEncoder(
                model_name,
                revision=revision,
                device=device,
                max_length=max_length,
                local_files_only=offline,
            )
        except OSError as error:
            if not offline:
                raise
            raise ModelUnavailableError(
                f"Reranker {model_name} liegt nicht lokal vor. "
                "Bitte zuerst `index models` ausführen, das lädt ihn herunter."
            ) from error

    def rerank(self, question: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return []
        scores = self._model.predict([(question, hit.text) for hit in hits], batch_size=8)
        rescored = [
            replace(hit, rerank_score=float(s)) for hit, s in zip(hits, scores, strict=True)
        ]
        return sorted(rescored, key=lambda h: h.rerank_score, reverse=True)
