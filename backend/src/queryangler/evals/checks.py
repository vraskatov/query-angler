"""Prüfungen für die Evals. Reine Funktionen, deshalb einzeln testbar."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from queryangler.guard import (  # noqa: F401  (für die Evals hier mit angeboten)
    follows_socratic_form,
    has_addition_after_refusal,
    has_code_block,
    is_refusal,
    leaked_keywords,
    meta_references,
)
from queryangler.prompt import Mode

GROUPS = ("im_buch", "nah_dran", "fremd", "sokratisch")


@dataclass
class Question:
    id: str
    group: str
    question: str
    gold: list[str] = field(default_factory=list)
    keywords_all: list[str] = field(default_factory=list)
    keywords_any: list[str] = field(default_factory=list)
    # Standard: sokratisch nur in der Gruppe "sokratisch". Absagen werden zusätzlich
    # im sokratischen Modus geprüft, weil die feste Form dort gegen die Absage drückt.
    mode: Mode | None = None

    def __post_init__(self) -> None:
        if self.mode is None:
            self.mode = "sokratisch" if self.group == "sokratisch" else "erklaeren"


def load_questions(path: Path) -> list[Question]:
    with path.open("rb") as file:
        questions = [Question(**q) for q in tomllib.load(file)["question"]]
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Doppelte IDs in den Testfragen.")
    for q in questions:
        if q.group not in GROUPS:
            raise ValueError(f"{q.id}: unbekannte Gruppe {q.group!r}")
    return questions


# --- Retrieval ---------------------------------------------------------------


def matches_gold(path: str, gold: list[str]) -> bool:
    """path = "Kapitel > Abschnitt". Ein Gold-Eintrag passt auch auf seine Unterabschnitte."""
    return any(path == g or path.startswith(g + " > ") for g in gold)


def gold_rank(paths: list[str], gold: list[str]) -> int | None:
    """Rang (ab 1) des ersten Treffers, der zu einem Gold-Abschnitt gehört, sonst None."""
    for rank, path in enumerate(paths, start=1):
        if matches_gold(path, gold):
            return rank
    return None


def hit_rate(ranks: list[int | None], k: int) -> float:
    """Anteil der Fragen, bei denen ein Gold-Abschnitt unter den ersten k Treffern ist."""
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mean_reciprocal_rank(ranks: list[int | None]) -> float:
    """Mittelwert von 1/Rang; nicht gefunden zählt 0."""
    return sum(1 / r for r in ranks if r is not None) / len(ranks)


# --- Antworten ---------------------------------------------------------------


def missing_keywords(answer: str, all_of: list[str], any_of: list[str]) -> list[str]:
    """Fehlende Pflichtbegriffe (Groß- und Kleinschreibung egal). Leer = alles da."""
    text = answer.upper()
    missing = [k for k in all_of if k.upper() not in text]
    if any_of and not any(k.upper() in text for k in any_of):
        missing.append(" | ".join(any_of))
    return missing
