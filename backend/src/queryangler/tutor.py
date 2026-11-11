"""Der Tutor: suchen, Prompt bauen, Sprachmodell fragen, Antwort mit Quellen liefern."""

import asyncio
import re
import time
from dataclasses import dataclass, field

from queryangler.llm import LLMClient
from queryangler.prompt import NO_KNOWLEDGE, Mode, build_prompt
from queryangler.retrieval import Retriever


@dataclass
class Source:
    number: int  # wie im Prompt und in der Antwort: [1], [2] ...
    id: str  # Chunk-ID, z. B. "gruppierungen#011"
    chapter: str
    section: str
    url: str
    permalink: str
    role: str
    score: float  # Kosinus-Ähnlichkeit der Vektorsuche
    rerank_score: float | None  # Bewertung des Rerankers, falls verwendet
    cited: bool  # in der Antwort tatsächlich genannt?


@dataclass
class Answer:
    text: str
    mode: Mode
    sources: list[Source]
    model: str | None
    timings: dict[str, float] = field(default_factory=dict)  # Sekunden je Schritt
    prompt_tokens_estimated: int = 0
    prompt_tokens: int | None = None  # wie das Modell sie gezählt hat
    excerpts_dropped: int = 0


class Tutor:
    def __init__(
        self, retriever: Retriever, llm: LLMClient, *, context_tokens: int, k: int = 5
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.context_tokens = context_tokens
        self.k = k

    async def ask(self, question: str, mode: Mode = "erklaeren", k: int | None = None) -> Answer:
        start = time.perf_counter()
        # Die Suche rechnet auf der CPU. In einem eigenen Thread, damit der Server
        # währenddessen andere Anfragen annehmen kann.
        hits = await asyncio.to_thread(
            self.retriever.search,
            question,
            k=k or self.k,
            include_solutions=(mode != "sokratisch"),  # sokratisch: Lösungen gar nicht erst finden
        )
        retrieval_seconds = time.perf_counter() - start

        if not hits:
            return Answer(
                text=NO_KNOWLEDGE,
                mode=mode,
                sources=[],
                model=None,
                timings={"retrieval": retrieval_seconds},
            )

        prompt = build_prompt(question, hits, mode, self.context_tokens)
        result = await self.llm.complete(prompt.messages)
        cited = cited_numbers(result.text)
        sources = [
            Source(
                number=n,
                id=hit.id,
                chapter=hit.metadata["chapter"],
                section=hit.metadata["section"],
                url=hit.metadata.get("url", ""),
                permalink=hit.metadata.get("permalink", ""),
                role=hit.metadata.get("role", ""),
                score=round(hit.score, 4),
                rerank_score=None if hit.rerank_score is None else round(hit.rerank_score, 4),
                cited=n in cited,
            )
            for n, hit in enumerate(prompt.hits, start=1)
        ]
        return Answer(
            text=result.text,
            mode=mode,
            sources=sources,
            model=result.model,
            timings={"retrieval": retrieval_seconds, "llm": result.seconds},
            prompt_tokens_estimated=prompt.estimated_tokens,
            prompt_tokens=result.prompt_tokens,
            excerpts_dropped=prompt.dropped,
        )


def cited_numbers(text: str) -> set[int]:
    """Quellennummern in der Antwort: [1], [1, 5] und [1][2] werden erkannt."""
    groups = re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", text)
    return {int(n) for group in groups for n in group.split(",")}
