"""Tests für den Prompt: Aufbau, Reihenfolge, Längenbegrenzung."""

from queryangler.prompt import (
    ANSWER_RESERVE_TOKENS,
    HISTORY_TURNS,
    NO_KNOWLEDGE,
    Turn,
    build_prompt,
    strip_citations,
)
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
    assert "Rot markiert im Buch" in system and NO_KNOWLEDGE in system
    assert "Belege jede Aussage" in system and 'mit "du" an, nie mit "ihr"' in system
    assert user.startswith("=== QUELLEN ===")
    # Frage nach den Auszügen, Erinnerung ganz am Schluss
    assert user.index("=== ENDE DER QUELLEN ===") < user.index(
        "Frage der Lernenden: Was macht HAVING?"
    )
    assert "[1] bis [2]" in user and NO_KNOWLEDGE in user.split("Erinnerung:")[1]
    assert user.endswith("belege mit [1], [2] usw. und ergänze nichts Eigenes.")
    assert "Eigenes Beispiel:" in system and "keine Rückfragen" in system
    assert "ohne Emojis" in system


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
    assert "Wie backe ich Brot?" in system  # Muster für die Absage im sokratischen Modus
    assert "Beispiel aus den Quellen" not in system + user  # würde wörtlich übernommen
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
    prompt = build_prompt("Frage?", hits, "erklaeren", ANSWER_RESERVE_TOKENS + 2800)

    assert [h.id for h in prompt.hits] == ["seite#001", "seite#002"]
    assert prompt.dropped == 3
    assert prompt.estimated_tokens <= 2800


def test_at_least_one_excerpt_is_kept():
    prompt = build_prompt("Frage?", [hit(1, text="x" * 30000)], "erklaeren", 2048)
    assert len(prompt.hits) == 1
    assert prompt.dropped == 0


# --- Gesprächsverlauf ------------------------------------------------------------


def test_history_comes_between_rules_and_question_without_old_citations():
    history = [Turn("Was macht ein INNER JOIN?", "Er verknüpft zwei Tabellen [1, 2].")]
    prompt = build_prompt("Und mit drei Tabellen?", [hit(1)], "erklaeren", 8192, history)
    roles = [m["role"] for m in prompt.messages]

    assert roles == ["system", "user", "assistant", "user"]
    assert prompt.messages[1]["content"] == "Was macht ein INNER JOIN?"
    # [1, 2] gehörte zu den Quellen der alten Frage
    assert prompt.messages[2]["content"] == "Er verknüpft zwei Tabellen."
    assert "Gespräch:" in prompt.messages[0]["content"]
    assert prompt.messages[-1]["content"].startswith("=== QUELLEN ===")
    assert prompt.history_used == 1


def test_without_history_there_is_no_conversation_rule():
    system = build_prompt("Frage?", [hit(1)], "erklaeren", 8192).messages[0]["content"]
    assert "Gespräch:" not in system


def test_socratic_history_rule_allows_the_learners_own_words():
    history = [Turn("Wie zähle ich pro Ort?", "Hinweis: …\nFrage: …?")]
    system = build_prompt("Mit GROUP BY?", [hit(1)], "sokratisch", 8192, history).messages[0]
    assert "selbst genannt" in system["content"] and "keine Lösung" in system["content"]


def test_only_the_last_turns_are_kept_and_the_oldest_go_first():
    history = [Turn(f"Frage {n}", "x" * 6000) for n in range(1, 8)]
    prompt = build_prompt("Jetzt?", [hit(1), hit(2)], "erklaeren", 8192, history)
    questions = [m["content"] for m in prompt.messages if m["role"] == "user"][:-1]

    assert len(history) > HISTORY_TURNS
    assert questions[-1] == "Frage 7"  # der jüngste Wortwechsel bleibt
    assert prompt.history_used < HISTORY_TURNS  # zu lang: ältere sind weggefallen
    assert len(prompt.hits) == 2  # die Quellen bleiben, solange Gespräch zu kürzen ist
    assert prompt.history_dropped == len(history) - prompt.history_used


def test_book_recommendations_are_passed_on_in_explain_mode():
    system = build_prompt("Frage?", [hit(1)], "erklaeren", 8192).messages[0]["content"]
    assert '"traditionell"' in system and '"vorzuziehen"' in system


def test_socratic_steers_towards_the_recommended_way_without_choice_questions():
    system = build_prompt("Frage?", [hit(1)], "sokratisch", 8192).messages[0]["content"]
    assert "lenke deinen Hinweis auf den empfohlenen Weg" in system
    assert "Nebensatz" in system and "Welche klingt besser?" in system
    assert '"vorzuziehen"' not in system  # die Regel des Modus Erklären gilt hier nicht


def test_strip_citations():
    assert strip_citations("A [1] und B [2, 3].") == "A und B."


def test_socratic_asks_for_the_task_when_the_request_is_vague():
    system = build_prompt("Ich habe ein Problem.", [hit(1)], "sokratisch", 8192).messages[0]
    assert "frag zuerst nach ihrer Aufgabe" in system["content"]
    assert "woran du gerade arbeitest" in system["content"]


def test_conversation_reminder_comes_at_the_end_only_with_history():
    history = [Turn("Wie mache ich einen JOIN?", "Hinweis: …\nFrage: …?")]
    with_history = build_prompt("fahrzeug_id", [hit(1)], "sokratisch", 8192, history)
    without = build_prompt("fahrzeug_id", [hit(1)], "sokratisch", 8192)
    last = with_history.messages[-1]["content"].split("Erinnerung:")[1]

    assert "wiederhole keine Frage, die schon beantwortet ist" in last
    assert last.endswith('Antworte nur mit "Hinweis:" und "Frage:".')
    assert "wiederhole keine Frage" not in without.messages[-1]["content"]
