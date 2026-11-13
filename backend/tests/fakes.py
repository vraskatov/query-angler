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
    """Antwortet mit festem Text (oder nacheinander mit mehreren) oder wirft einen Fehler.

    Merkt sich alle erhaltenen Nachrichten, damit Tests den Prompt prüfen können.
    """

    model = "fake-llm"

    def __init__(
        self,
        text: str = "Antwort [1].",
        error: Exception | None = None,
        texts: list[str] | None = None,
    ) -> None:
        self.texts = texts or [text]
        self.error = error
        self.reachable = True
        self.calls: list[list[Message]] = []

    @property
    def text(self) -> str:
        return self.texts[0]

    @text.setter
    def text(self, value: str) -> None:
        self.texts = [value]

    @property
    def last_messages(self) -> list[Message] | None:
        return self.calls[-1] if self.calls else None

    async def complete(self, messages: list[Message]) -> LLMResult:
        self.calls.append(messages)
        if self.error:
            raise self.error
        text = self.texts[min(len(self.calls), len(self.texts)) - 1]
        return LLMResult(text=text, model=self.model, seconds=0.01, prompt_tokens=123)

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
