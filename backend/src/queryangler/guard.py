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


# --- Stufe Schreibweise ----------------------------------------------------------
# Die Syntax kann man sich nicht erschließen. Bittet jemand im laufenden Gespräch
# ausdrücklich darum, zeigt der Tutor das allgemeine Schema mit Platzhaltern; das
# Einsetzen der eigenen Namen bleibt Aufgabe der Lernenden. Ob die Stufe gilt,
# entscheidet dieses Muster, nicht das Modell.
_SYNTAX_REQUEST = re.compile(
    r"\b(Syntax|Schreibweise|Code|Befehl)\b"
    r"|wie (?:man|ich) (?:das|den|die|es)?\s*(?:\w+ )?schreib"
    r"|wie schreib\w* (?:ich|man) (?:das|den|es|ihn)\b"
    r"|wie sieht (?:das|der|die) .{0,30}aus",
    re.IGNORECASE,
)


def asks_for_syntax(message: str) -> bool:
    return bool(_SYNTAX_REQUEST.search(message))


_CODE_BLOCK = re.compile(r"```[a-zA-Z]*\n?(.*?)```", re.DOTALL)
_PLACEHOLDER = re.compile(r"<[^<>\n]{1,40}>")
_WORD = re.compile(r"[A-Za-zÄÖÜäöüß_][A-Za-zÄÖÜäöüß0-9_]{2,}")


def names_in_code(answer: str, learner: str) -> list[str]:
    """Wörter der Lernenden (etwa Tabellen- und Spaltennamen), die im Code der Antwort stehen.

    Platzhalter in spitzen Klammern und SQL-Schlüsselwörter zählen nicht.
    """
    code = " ".join(_PLACEHOLDER.sub(" ", block) for block in _CODE_BLOCK.findall(answer))
    if not code:
        return []
    keywords = {k for keyword in SQL_KEYWORDS for k in keyword.split()} | {"ON", "AS", "AND", "OR"}
    learner_words = {w.lower() for w in _WORD.findall(learner) if w.upper() not in keywords}
    return sorted({w for w in _WORD.findall(code) if w.lower() in learner_words})


def syntax_stage_problems(answer: str, learner: str) -> list[str]:
    """Wie socratic_problems, aber für die Stufe Schreibweise: Code und Schlüsselwörter sind
    erlaubt, die Namen aus dem Gespräch im Code nicht. Sonst wäre es die fertige Lösung."""
    problems = []
    if names := names_in_code(answer, learner):
        problems.append(f"sie setzt Namen aus dem Gespräch in den Code ein ({', '.join(names)})")
    if meta := meta_references(answer):
        problems.append(
            f"sie verweist auf Material, das die Lernenden nicht sehen ({', '.join(meta)})"
        )
    return problems


def syntax_stage_retry_message(problems: list[str]) -> str:
    return (
        f"Deine Antwort passt noch nicht: {'; '.join(problems)}. "
        "Schreib sie neu. Zeig nur die allgemeine Schreibweise mit Platzhaltern in spitzen "
        "Klammern wie <tabelle1> und <spalte>. Das Einsetzen der eigenen Namen ist die Aufgabe "
        'der Lernenden. Antworte nur mit "Hinweis:" und "Frage:".'
    )


def socratic_retry_message(problems: list[str]) -> str:
    """Rückmeldung an das Modell für den zweiten Versuch."""
    return (
        f"Deine Antwort passt noch nicht: {'; '.join(problems)}. "
        "Schreib sie neu. Kein SQL, keine Fachbegriffe der Lösung. Sprich die Person direkt "
        'mit "du" an und erkläre in Alltagssprache, worüber sie nachdenken soll. '
        'Antworte nur mit "Hinweis:" und "Frage:". '
        # Ohne diesen Ausweg bastelte das Modell bei "Was ist das Leben?" einen Hinweis
        # auf Trigger, statt abzusagen.
        f'Hat die Frage nichts mit den Quellen zu tun, antworte stattdessen nur: "{NO_KNOWLEDGE}"'
    )
