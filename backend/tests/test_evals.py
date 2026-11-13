"""Tests für die Prüfungen der Evals und für die Datei mit den Testfragen."""

import pytest

from queryangler.config import EVALS_DIR
from queryangler.evals.__main__ import _answers_report, _retrieval_report
from queryangler.evals.checks import (
    follows_socratic_form,
    gold_rank,
    has_addition_after_refusal,
    hit_rate,
    is_refusal,
    leaked_keywords,
    load_questions,
    mean_reciprocal_rank,
    meta_references,
    missing_keywords,
)
from queryangler.prompt import NO_KNOWLEDGE


def test_question_file_is_valid():
    questions = load_questions(EVALS_DIR / "questions.toml")
    groups = {q.group for q in questions}
    assert groups == {"im_buch", "nah_dran", "fremd", "sokratisch"}
    # Wer ein Retrieval-Ziel braucht, hat eines
    assert all(q.gold for q in questions if q.group in ("im_buch", "sokratisch"))
    assert all(q.mode == "sokratisch" for q in questions if q.group == "sokratisch")


def test_gold_rank_matches_section_and_subsections():
    paths = [
        "Funktionen (2) > Allgemeine Hinweise",
        "Gruppierungen > Erweiterungen > HAVING",
        "X > Y",
    ]
    assert gold_rank(paths, ["Gruppierungen > Erweiterungen"]) == 2
    assert gold_rank(paths, ["Gruppierungen"]) == 2
    # "Funktionen" darf nicht auf "Funktionen (2)" passen
    assert gold_rank(paths, ["Funktionen"]) is None


def test_hit_rate_and_mrr():
    ranks = [1, 2, None, 4]
    assert hit_rate(ranks, 1) == 0.25
    assert hit_rate(ranks, 3) == 0.5
    assert mean_reciprocal_rank(ranks) == pytest.approx((1 + 1 / 2 + 0 + 1 / 4) / 4)


def test_refusal_is_recognized_at_the_start():
    assert is_refusal(f"  {NO_KNOWLEDGE}\n")
    assert is_refusal(f'"{NO_KNOWLEDGE}"')
    assert is_refusal(f"**{NO_KNOWLEDGE}**")
    # So antwortete Ministral in der Eval: richtig abgesagt, aber mit Zusatz
    assert is_refusal(f"{NO_KNOWLEDGE}\n\nDie Quellen behandeln nur reguläre Views.")
    assert has_addition_after_refusal(f"{NO_KNOWLEDGE} Die Quellen behandeln …")
    assert not has_addition_after_refusal(NO_KNOWLEDGE)
    assert not is_refusal(f"Gute Frage. {NO_KNOWLEDGE}")


def test_missing_keywords():
    answer = "Mit group by und count(*) zählst du."
    assert missing_keywords(answer, ["GROUP BY", "COUNT"], []) == []
    assert missing_keywords(answer, ["HAVING"], ["LIMIT", "TOP"]) == ["HAVING", "LIMIT | TOP"]


def test_leaked_keywords_ignore_words_from_the_question_and_german_words():
    question = "Was unterscheidet WHERE von HAVING?"
    answer = (
        "Hinweis: WHERE wirkt vorher, COUNT erst nach dem GROUP BY. Das ist in min. 2 Fällen so."
    )
    assert leaked_keywords(answer, question) == ["COUNT", "GROUP BY"]


def test_meta_references():
    assert meta_references("In den Beispielen wird gezeigt, wie …") == ["In den Beispielen"]
    assert meta_references("Laut Quelle [2] gilt …") == ["Quelle", "[2"]
    assert meta_references("Hinweis: Denk an die Reihenfolge.") == []
    # aus der Eval: "Die Beispiele zeigen, wie man Daten aus Tabellen abfragt …"
    assert meta_references("Die Beispiele zeigen, wie …") == ["Die Beispiele"]
    assert meta_references("Ein Beispiel: Kunden nach Namen sortieren.") == []


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Hinweis: Denk an Gruppen.\nFrage: Wonach fasst du zusammen?", True),
        ("Hinweis: Denk an Gruppen.\nFrage: Wonach fasst du zusammen? Und dann?", True),
        ("Denk an Gruppen. Frage: Wonach?", False),  # Hinweis fehlt
        ("Hinweis: A\nFrage: B?\nFrage: C?", False),  # zwei Fragen
        ("Hinweis: A\nFrage: B.", False),  # keine Frage
    ],
)
def test_socratic_form(answer, ok):
    assert follows_socratic_form(answer) is ok


def test_report_recomputes_checks_from_the_answer_text():
    base = {"model": "m", "label": "m", "llm_seconds": 2.0}
    rows = [
        {**base, "id": "a", "group": "im_buch", "question": "Was ist GROUP BY?",
         "answer": "GROUP BY fasst zusammen [1].", "cited_any": True, "missing_keywords": [],
         "rating": {"correct": 2, "faithful": 1, "note": ""}},
        # alter Lauf: Absage mit Zusatz, damals als "keine Absage" gezählt
        {**base, "id": "b", "group": "nah_dran", "question": "Wie geht LAG?",
         "answer": f"{NO_KNOWLEDGE} Die Quellen nennen LAG nicht.", "refusal": False},
        {**base, "id": "c", "group": "fremd", "question": "Apfelkuchen?",
         "answer": "Man nehme Äpfel."},
        {**base, "id": "d", "group": "sokratisch", "question": "Wie zähle ich pro Ort?",
         "answer": "Hinweis: Nutze COUNT.\nFrage: Was zählst du?", "attempts": 2,
         "checks": ["Zweiter Versuch, weil …"]},
    ]  # fmt: skip
    report = _answers_report({"lauf": rows})
    assert "| Nah dran, nicht im Buch | 1/1 |" in report
    assert "| Themenfremd | 0/1 |" in report
    assert "| Modell hängte etwas an die Absage | 1/2 |" in report
    assert "| Richtigkeit (0–2) | 2.00 (n=1) |" in report
    assert "| Ohne SQL-Begriffe (Leck) | 0/1 |" in report
    assert "| Zweiter Versuch nötig | 1/1 |" in report
    assert "`c`: keine Absage" in report
    assert "`d`: Leck: COUNT" in report
    assert "`b`" not in report.split("Auffällige")[1]


def test_retrieval_report_compares_variants():
    rows = [
        {"id": "a", "group": "im_buch", "ranks": {"Vektor": 4, "RRF": 1}, "top_vector": 0.6,
         "top_rerank": None, "top": ["x"]},
        {"id": "b", "group": "sokratisch", "ranks": {"Vektor": None, "RRF": 15},
         "top_vector": 0.5, "top_rerank": None, "top": ["y"]},
        {"id": "c", "group": "fremd", "ranks": {"Vektor": None, "RRF": None}, "top_vector": 0.4,
         "top_rerank": None, "top": ["z"]},
    ]  # fmt: skip
    report = _retrieval_report(rows)
    assert "| Kennzahl | Vektor | RRF |" in report
    assert "| Trefferquote@1 | 0 % | 50 % |" in report
    assert "| Trefferquote@5 | 50 % | 50 % |" in report
    assert "| Trefferquote@20 | 50 % | 100 % |" in report
    assert "| `b` | – | 15 |" in report
    assert "| fremd | 0.400 | 0.400 | 0.400 |" in report
