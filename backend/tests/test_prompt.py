"""Tests für den Prompt: Aufbau, Reihenfolge, Längenbegrenzung."""

from queryangler.prompt import ANSWER_RESERVE_TOKENS, NO_KNOWLEDGE, build_prompt
from queryangler.retrieval import Hit


def hit(n, text="Inhalt", role="content", score=0.5):
    return Hit(
        id=f"seite#{n:03d}",
        text=f"Kapitel: Kapitel {n}\nAbschnitt: Abschnitt {n}\n\n{text}",
        metadata={"chapter": f"Kapitel {n}", "section": f"Abschnitt {n}", "role": role},
        score=score,
    )


def test_rules_in_system_question_and_reminder_at_the_end():
    prompt = build_prompt("Was macht HAVING?", [hit(1), hit(2)], "erklaeren", 8192)
    system, user = (m["content"] for m in prompt.messages)

    assert [m["role"] for m in prompt.messages] == ["system", "user"]
    assert "Negativbeispiel" in system and NO_KNOWLEDGE in system
    assert "Belege jede Aussage" in system and "sehen den Text der Quellen nicht" in system
    assert user.startswith("=== QUELLEN ===")
    # Frage nach den Auszügen, Erinnerung ganz am Schluss
    assert user.index("=== ENDE DER QUELLEN ===") < user.index(
        "Frage der Lernenden: Was macht HAVING?"
    )
    assert "[1] bis [2]" in user and NO_KNOWLEDGE in user.split("Erinnerung:")[1]
    assert user.endswith("Sonst: Belege mit [1], [2] usw.")


def test_excerpts_are_numbered_with_path_and_role_label():
    prompt = build_prompt("Frage?", [hit(1), hit(2, role="exercise")], "erklaeren", 8192)
    user = prompt.messages[1]["content"]

    assert "[1] Kapitel 1 > Abschnitt 1\nInhalt" in user
    assert "[2] Kapitel 2 > Abschnitt 2 (Übungsaufgabe)" in user
    assert "Kapitel: Kapitel 1" not in user  # Kopfzeile des Chunks nicht doppelt


def test_socratic_mode_in_rules_and_in_final_reminder():
    system, user = (
        m["content"] for m in build_prompt("Frage?", [hit(1)], "sokratisch", 8192).messages
    )
    assert "Verboten sind: SQL-Code" in system
    assert "Hinweis: <" in system and "Frage: <" in system  # feste Form
    assert "Belege jede Aussage" not in system  # Verweise würden auf die Lösung zeigen
    assert NO_KNOWLEDGE in user.split("Erinnerung:")[1]  # Absage gilt auch hier
    assert user.endswith('Antworte nur mit "Hinweis:" und "Frage:".')  # Modus ganz am Ende


def test_explain_mode_has_no_socratic_rules():
    system = build_prompt("Frage?", [hit(1)], "erklaeren", 8192).messages[0]["content"]
    assert "Verboten sind" not in system and "Hinweis: <" not in system


def test_weakest_excerpts_are_dropped_when_too_long():
    hits = [hit(n, text="x" * 3000, score=1 - n / 10) for n in range(1, 6)]
    # Budget für Regeln, Frage und etwa zwei Auszüge à 1000 Token
    prompt = build_prompt("Frage?", hits, "erklaeren", ANSWER_RESERVE_TOKENS + 2600)

    assert [h.id for h in prompt.hits] == ["seite#001", "seite#002"]
    assert prompt.dropped == 3
    assert prompt.estimated_tokens <= 2600


def test_at_least_one_excerpt_is_kept():
    prompt = build_prompt("Frage?", [hit(1, text="x" * 30000)], "erklaeren", 2048)
    assert len(prompt.hits) == 1
    assert prompt.dropped == 0
