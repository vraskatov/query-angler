"""Ausgabe des Sprachmodells prüfen, statt sich allein auf den Prompt zu verlassen.

Die Evals haben gezeigt, wo Regeln im Prompt bei einem 8B-Modell nicht reichen:
- Absagen: Das Modell beginnt richtig mit dem Absagesatz, hängt dann aber oft etwas an,
  teils mit eigenem Wissen (z. B. die Namen der Isolationsstufen), obwohl Regel 2 das
  verbietet. Deshalb wird nach dem Absagesatz abgeschnitten.
- Sokratischer Modus: Hinweise verraten SQL-Begriffe der Lösung oder verweisen auf
  "Quellen" und "Beispiele", die die Lernenden nicht sehen. Deshalb wird eine solche
  Antwort einmal mit Begründung zurückgegeben.

Dieselben Prüfungen nutzen die Evals; so misst die Eval genau das, was der Tutor prüft.
"""

import re

from queryangler.prompt import NO_KNOWLEDGE

# --- Absage --------------------------------------------------------------------


def is_refusal(answer: str) -> bool:
    """Beginnt die Antwort mit dem Absagesatz? (Anführungszeichen und Fettdruck egal)"""
    return _strip_decoration(answer).startswith(NO_KNOWLEDGE)


def has_addition_after_refusal(answer: str) -> bool:
    return is_refusal(answer) and _strip_decoration(answer) != NO_KNOWLEDGE


def _strip_decoration(answer: str) -> str:
    return answer.strip().lstrip('"„“*_ ').replace("**", "").strip().rstrip('"“')


# --- Sokratischer Modus --------------------------------------------------------

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
    r"\b(Quellen?|Auszüge|Auszügen|Auszug|(?:die|den|in den) Beispielen?|im Beispiel oben)\b"
    r"|\[\d+",
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


def socratic_problems(answer: str, question: str, include_form: bool = False) -> list[str]:
    """Was an einer sokratischen Antwort nicht stimmt, in Worten für das Modell.

    Leer heißt: in Ordnung. Die Form allein ist standardmäßig kein Grund für einen
    zweiten Versuch: Ein Satz nach der Frage ist ein Schönheitsfehler, kein Verrat der
    Lösung, und kostet sonst jedes Mal ein paar Sekunden.
    """
    problems = []
    if leaked := leaked_keywords(answer, question):
        problems.append(f"sie nennt die SQL-Begriffe {', '.join(leaked)}")
    if has_code_block(answer):
        problems.append("sie enthält SQL-Code")
    if meta := meta_references(answer):
        problems.append(
            f"sie verweist auf Material, das die Lernenden nicht sehen ({', '.join(meta)})"
        )
    if include_form and not follows_socratic_form(answer):
        problems.append('sie hat nicht die Form "Hinweis: …" und "Frage: …?"')
    return problems


def socratic_retry_message(problems: list[str]) -> str:
    """Rückmeldung an das Modell für den zweiten Versuch."""
    return (
        f"Deine Antwort passt noch nicht: {'; '.join(problems)}. "
        "Schreib sie neu. Kein SQL, keine Fachbegriffe der Lösung. Sprich die Lernenden "
        "direkt an und erkläre in Alltagssprache, worüber sie nachdenken sollen. "
        'Antworte nur mit "Hinweis:" und "Frage:". '
        # Ohne diesen Ausweg bastelte das Modell bei "Was ist das Leben?" einen Hinweis
        # auf Trigger, statt abzusagen.
        f'Hat die Frage nichts mit den Quellen zu tun, antworte stattdessen nur: "{NO_KNOWLEDGE}"'
    )
