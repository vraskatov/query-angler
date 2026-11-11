"""Ersatzteile für Tests: kein Modell-Download, kein laufendes Ollama."""

import math
import re
import zlib
from dataclasses import replace

from queryangler.llm import LLMResult, Message

DIM = 256


class FakeEmbedder:
    """Bag of Words: Texte mit gemeinsamen Wörtern liegen nah beieinander."""

    name = "fake-bag-of-words"

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        vector = [0.0] * DIM
        for word in re.findall(r"\w+", text.lower()):
            vector[zlib.crc32(word.encode()) % DIM] += 1.0  # crc32: stabil über Programmläufe
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


class FakeLLM:
    """Antwortet mit festem Text oder wirft einen vorgegebenen Fehler.

    Merkt sich die zuletzt erhaltenen Nachrichten, damit Tests den Prompt prüfen können.
    """

    model = "fake-llm"

    def __init__(self, text: str = "Antwort [1].", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.reachable = True
        self.last_messages: list[Message] | None = None

    async def complete(self, messages: list[Message]) -> LLMResult:
        self.last_messages = messages
        if self.error:
            raise self.error
        return LLMResult(text=self.text, model=self.model, seconds=0.01, prompt_tokens=123)

    async def is_reachable(self) -> bool:
        return self.reachable


class FakeReranker:
    """Bewertet nach der Zahl gemeinsamer Wörter von Frage und Chunk."""

    name = "fake-overlap"

    def rerank(self, question, hits):
        words = set(re.findall(r"\w+", question.lower()))
        scored = [
            replace(h, rerank_score=float(len(words & set(re.findall(r"\w+", h.text.lower())))))
            for h in hits
        ]
        return sorted(scored, key=lambda h: h.rerank_score, reverse=True)
