"""Der Tutor: suchen, Prompt bauen, Sprachmodell fragen, Antwort prüfen, mit Quellen liefern."""

import asyncio
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from queryangler.config import BOOK_LICENSE
from queryangler.guard import (
    asks_for_syntax,
    is_refusal,
    socratic_problems,
    socratic_retry_message,
    syntax_stage_problems,
    syntax_stage_retry_message,
)
from queryangler.llm import LLMClient, LLMError, LLMResult, LLMUnavailableError, Message
from queryangler.prompt import NO_KNOWLEDGE, Mode, Turn, build_prompt, strip_citations
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
    license: str  # Lizenz der Quelle: das Buch oder hochgeladenes Material
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
    history_used: int = 0  # Wortwechsel aus dem bisherigen Gespräch im Prompt
    search_query: str = ""  # wonach gesucht wurde (im Gespräch vom Modell umformuliert)
    syntax_stage: bool = False  # sokratisch: allgemeine Schreibweise mit Platzhaltern gezeigt
    attempts: int = 1  # Anfragen an das Sprachmodell (2 = zweiter Versuch nötig)
    checks: list[str] = field(default_factory=list)  # was die Prüfung der Ausgabe getan hat


class Tutor:
    def __init__(
        self, retriever: Retriever, llm: LLMClient, *, context_tokens: int, k: int = 5
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.context_tokens = context_tokens
        self.k = k

    async def ask(
        self,
        question: str,
        mode: Mode = "erklaeren",
        k: int | None = None,
        history: Sequence[Turn] = (),
    ) -> Answer:
        timings: dict[str, float] = {}
        query = question
        if history:
            start = time.perf_counter()
            query = await self._rewrite_query(question, history)
            timings["rewrite"] = time.perf_counter() - start

        start = time.perf_counter()
        # Die Suche rechnet auf der CPU. In einem eigenen Thread, damit der Server
        # währenddessen andere Anfragen annehmen kann.
        hits = await asyncio.to_thread(
            self.retriever.search,
            query,
            k=k or self.k,
            include_solutions=(mode != "sokratisch"),  # sokratisch: Lösungen gar nicht erst finden
        )
        timings["retrieval"] = time.perf_counter() - start

        if not hits:
            return Answer(
                text=NO_KNOWLEDGE,
                mode=mode,
                sources=[],
                model=None,
                timings=timings,
                search_query=query,
            )

        syntax_stage = mode == "sokratisch" and bool(history) and asks_for_syntax(question)
        prompt = build_prompt(question, hits, mode, self.context_tokens, history, syntax_stage)
        result = await self.llm.complete(prompt.messages)
        llm_seconds = result.seconds
        text, attempts, checks = result.text, 1, []

        if is_refusal(text):
            if text.strip() != NO_KNOWLEDGE:
                checks.append("Absage: Zusatz nach dem Absagesatz entfernt")
            text = NO_KNOWLEDGE
        elif mode == "sokratisch":
            text, retry, checks = await self._check_socratic(
                learner_text(question, history), prompt.messages, result, syntax_stage
            )
            if retry:
                attempts, llm_seconds = 2, llm_seconds + retry.seconds

        cited = cited_numbers(text)
        sources = [
            Source(
                number=n,
                id=hit.id,
                chapter=hit.metadata["chapter"],
                section=hit.metadata["section"],
                url=hit.metadata.get("url", ""),
                permalink=hit.metadata.get("permalink", ""),
                role=hit.metadata.get("role", ""),
                license=hit.metadata.get("license", BOOK_LICENSE),
                score=round(hit.score, 4),
                rerank_score=None if hit.rerank_score is None else round(hit.rerank_score, 4),
                cited=n in cited,
            )
            for n, hit in enumerate(prompt.hits, start=1)
        ]
        return Answer(
            text=text,
            mode=mode,
            sources=sources,
            model=result.model,
            timings={**timings, "llm": llm_seconds},
            prompt_tokens_estimated=prompt.estimated_tokens,
            prompt_tokens=result.prompt_tokens,
            excerpts_dropped=prompt.dropped,
            history_used=prompt.history_used,
            search_query=query,
            syntax_stage=syntax_stage,
            attempts=attempts,
            checks=checks,
        )

    async def _rewrite_query(self, question: str, history: Sequence[Turn]) -> str:
        """Query Rewriting im Gespräch: Das Modell formuliert eine Suchanfrage in Fachsprache.

        Lernende schreiben "zusammenführen" oder "Mir fehlt die Syntax", das Buch spricht von
        JOIN und Verknüpfung. Die Wortlücke überbrückt das Modell mit eigenem Wissen, aber nur
        für die Suche; die Antwort bleibt an die Quellen gebunden. Nur bei Folgenachrichten,
        damit einzelne Fragen so schnell bleiben wie bisher. Scheitert der Aufruf, wird wie
        vorher mit den Nachrichten der Lernenden gesucht.
        """
        lines = [
            f"Lernende: {turn.question}\nTutor: {strip_citations(turn.answer)[:400]}"
            for turn in history
        ]
        messages: list[Message] = [
            {"role": "system", "content": REWRITE_SYSTEM},
            {
                "role": "user",
                "content": "Bisheriges Gespräch:\n"
                + "\n\n".join(lines)
                + f"\n\nLetzte Nachricht der Lernenden: {question}\n\n{REWRITE_TASK}",
            },
        ]
        try:
            result = await self.llm.complete(messages)
        except LLMUnavailableError:
            raise  # Ollama läuft nicht: dann scheitert auch die Antwort, lieber gleich melden
        except LLMError:
            return search_query(question, history)
        rewritten = _first_line(result.text)
        return rewritten or search_query(question, history)

    async def _check_socratic(
        self, learner: str, messages: list[Message], first: LLMResult, syntax_stage: bool = False
    ) -> tuple[str, LLMResult | None, list[str]]:
        """Verrät die Antwort zu viel, bekommt das Modell genau einen zweiten Versuch.

        `learner` ist alles, was die Lernenden geschrieben haben: Begriffe, die sie selbst
        genannt haben, darf der Tutor aufgreifen, ohne dass es als Verrat zählt.

        Ausgeliefert wird die Antwort mit weniger Problemen. Bleiben welche, steht das in
        den Prüfhinweisen, damit es sichtbar ist (in der API-Antwort und in den Evals).
        Sagt der zweite Versuch ab, gilt die Absage: Meist war die Frage fachfremd, und der
        erste Versuch hatte nur wegen der festen Form trotzdem einen Hinweis gebastelt.
        """
        find_problems = syntax_stage_problems if syntax_stage else socratic_problems
        retry_message = syntax_stage_retry_message if syntax_stage else socratic_retry_message
        problems = find_problems(first.text, learner)
        if not problems:
            return first.text, None, []
        retry_messages = [
            *messages,
            {"role": "assistant", "content": first.text},
            {"role": "user", "content": retry_message(problems)},
        ]
        second = await self.llm.complete(retry_messages)
        if is_refusal(second.text):
            return (
                NO_KNOWLEDGE,
                second,
                [f"Absage im zweiten Versuch; erste Antwort: {'; '.join(problems)}"],
            )
        second_problems = find_problems(second.text, learner)
        checks = [f"Zweiter Versuch, weil {'; '.join(problems)}"]
        if not second_problems:
            return second.text, second, checks
        if len(second_problems) <= len(problems):
            return second.text, second, [*checks, f"Weiterhin: {'; '.join(second_problems)}"]
        return first.text, second, [*checks, "Zweiter Versuch schlechter, erste Antwort geliefert"]


REWRITE_SYSTEM = "Du hilfst bei der Suche in einem deutschsprachigen Lehrbuch über SQL."
REWRITE_TASK = (
    "Schreib eine kurze Suchanfrage (höchstens zwölf Wörter) mit den SQL-Fachbegriffen, unter "
    "denen das Lehrbuch das Thema behandelt, um das es den Lernenden gerade geht. "
    "Antworte nur mit der Suchanfrage."
)


def _first_line(text: str) -> str:
    """Erste nicht leere Zeile, ohne Anführungszeichen und Vorspann wie "Suchanfrage:"."""
    for line in text.splitlines():
        line = line.strip().strip('"„“*').strip()
        if line.lower().startswith("suchanfrage:"):
            line = line.split(":", 1)[1].strip().strip('"„“')
        if line:
            return line[:300]
    return ""


def search_query(question: str, history: Sequence[Turn] = ()) -> str:
    """Wonach gesucht wird. Bei einer Folgefrage ("Und mit drei Tabellen?") findet die Frage
    allein nichts Brauchbares; deshalb kommen die erste und die vorige Nachricht der
    Lernenden dazu. Die erste nennt meist das Thema: Bei "Mir fehlt die Syntax" in der
    dritten Runde fand die Suche sonst Trigger statt JOIN.
    Kein Umformulieren durch das Modell: Das kostete einen zweiten Aufruf, also Wartezeit."""
    parts = [history[0].question, history[-1].question] if history else []
    return "\n".join(dict.fromkeys([*parts, question]))  # dict.fromkeys: ohne Doppelte


def learner_text(question: str, history: Sequence[Turn] = ()) -> str:
    """Alles, was die Lernenden geschrieben haben (für die Prüfung auf verratene Begriffe)."""
    return "\n".join([*(turn.question for turn in history), question])


def cited_numbers(text: str) -> set[int]:
    """Quellennummern in der Antwort: [1], [1, 5] und [1][2] werden erkannt."""
    groups = re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", text)
    return {int(n) for group in groups for n in group.split(",")}
