"""Prüfungen für die Evals. Reine Funktionen, deshalb einzeln testbar."""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from queryangler.prompt import NO_KNOWLEDGE, Mode

GROUPS = ("im_buch", "nah_dran", "fremd", "sokratisch")


@dataclass
class Question:
    id: str
    group: str
    question: str
    gold: list[str] = field(default_factory=list)
    keywords_all: list[str] = field(default_factory=list)
    keywords_any: list[str] = field(default_factory=list)

    @property
    def mode(self) -> Mode:
        return "sokratisch" if self.group == "sokratisch" else "erklaeren"


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


def is_refusal(answer: str) -> bool:
    return answer.strip().strip('"„“') == NO_KNOWLEDGE


def missing_keywords(answer: str, all_of: list[str], any_of: list[str]) -> list[str]:
    """Fehlende Pflichtbegriffe (Groß- und Kleinschreibung egal). Leer = alles da."""
    text = answer.upper()
    missing = [k for k in all_of if k.upper() not in text]
    if any_of and not any(k.upper() in text for k in any_of):
        missing.append(" | ".join(any_of))
    return missing


# Begriffe, die im sokratischen Modus die Lösung verraten. Nur in Großbuchstaben gesucht:
# So schreibt man SQL, und deutsche Wörter wie "in" oder "min" lösen keinen Fehlalarm aus.
SQL_KEYWORDS = (
    "SELECT", "FROM", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "COUNT", "SUM", "AVG",
    "MIN", "MAX", "JOIN", "INNER", "OUTER", "LEFT", "RIGHT", "LIKE", "IS NULL", "UPDATE",
    "SET", "INSERT", "DELETE", "DISTINCT", "EXISTS", "IN", "UNION", "CASE", "DESC", "ASC",
    "BETWEEN", "LIMIT",
)  # fmt: skip


def _keywords_in(text: str) -> set[str]:
    return {k for k in SQL_KEYWORDS if re.search(rf"(?<![A-Za-z_]){k}(?![A-Za-z_])", text)}


def leaked_keywords(answer: str, question: str) -> list[str]:
    """SQL-Begriffe in der Antwort, die nicht schon in der Frage stehen."""
    return sorted(_keywords_in(answer) - _keywords_in(question))


def has_code_block(answer: str) -> bool:
    return "```" in answer


# Verweise auf Material, das die Lernenden nicht sehen.
_META = re.compile(
    r"\b(Quellen?|Auszüge|Auszügen|Auszug|in den Beispielen|im Beispiel oben)\b|\[\d+",
    re.IGNORECASE,
)


def meta_references(answer: str) -> list[str]:
    return sorted({m.group(0) for m in _META.finditer(answer)})


def follows_socratic_form(answer: str) -> bool:
    """Genau ein "Hinweis:" am Anfang, genau ein "Frage:" danach, Schluss mit Fragezeichen."""
    text = answer.strip()
    return (
        text.startswith("Hinweis:")
        and text.count("Hinweis:") == 1
        and text.count("Frage:") == 1
        and text.index("Frage:") > text.index("Hinweis:")
        and text.endswith("?")
    )
