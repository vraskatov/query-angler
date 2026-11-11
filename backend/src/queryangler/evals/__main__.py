"""Evals: messen, wie gut Retrieval und Antworten sind.

uv run python -m queryangler.evals retrieval                 # ohne Sprachmodell, Sekunden
uv run python -m queryangler.evals answers --label ministral  # mit Ollama, einige Minuten
uv run python -m queryangler.evals rate                       # Antworten selbst bewerten
uv run python -m queryangler.evals report                     # Bericht aus allen Läufen

Ergebnisse landen in evals/results/ und werden versioniert: Sie sind der Beleg.
Das Modell für `answers` kommt wie beim Server aus QUERYANGLER_LLM_MODEL.
"""

import argparse
import asyncio
import json
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from queryangler import config
from queryangler.evals.checks import (
    Question,
    follows_socratic_form,
    gold_rank,
    has_code_block,
    hit_rate,
    is_refusal,
    leaked_keywords,
    load_questions,
    mean_reciprocal_rank,
    meta_references,
    missing_keywords,
)
from queryangler.llm import LLMError, LLMUnavailableError

QUESTIONS_FILE = config.EVALS_DIR / "questions.toml"
RESULTS_DIR = config.EVALS_DIR / "results"
K_VALUES = (1, 3, 5, 8)


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def _pct(value: float) -> str:
    return f"{value * 100:.0f} %"


# --- retrieval -----------------------------------------------------------------


RETRIEVAL_VARIANTS = (
    ("Vektor", None),
    ("BM25", None),
    ("RRF", ("rrf", 0.7)),
    ("Gewichtet 0,7", ("gewichtet", 0.7)),
    ("Gewichtet 0,5", ("gewichtet", 0.5)),
    ("Reißverschluss", ("reissverschluss", 0.7)),
)


def cmd_retrieval(args: argparse.Namespace) -> None:
    from queryangler.components import load_retriever
    from queryangler.retrieval import fuse

    questions = load_questions(QUESTIONS_FILE)
    retriever = load_retriever(rerank=args.mit_reranker, hybrid=True)
    n = retriever.candidates

    rows = []
    for q in questions:
        print(f"  {q.id}", flush=True)
        vector, lexical = retriever.candidate_lists(q.question, q.mode != "sokratisch")
        lists = {"Vektor": vector, "BM25": lexical}
        for name, method in RETRIEVAL_VARIANTS:
            if method:
                lists[name] = fuse(vector, lexical, method[0], n, method[1])
        final = lists["Vektor"]
        if retriever.reranker:
            lists["RRF + Reranker"] = retriever.reranker.rerank(q.question, lists["RRF"])
            final = lists["RRF + Reranker"]
        rows.append(
            {
                "id": q.id,
                "group": q.group,
                "question": q.question,
                "ranks": {name: _rank(hits, q.gold) for name, hits in lists.items()},
                "top_vector": round(vector[0].score, 4) if vector else None,
                "top_rerank": round(final[0].rerank_score, 4)
                if retriever.reranker and final
                else None,
                "top": [f"{h.id} ({h.score:.3f})" for h in lists["Vektor"][:3]],
            }
        )

    stamp = _stamp()
    _write_jsonl(RESULTS_DIR / f"retrieval_{stamp}.jsonl", rows)
    report = _retrieval_report(rows)
    (RESULTS_DIR / f"retrieval_{stamp}.md").write_text(report, encoding="utf-8")
    print(report)


def _rank(hits: list[Any], gold: list[str]) -> int | None:
    if not gold:
        return None
    return gold_rank([f"{h.metadata['chapter']} > {h.metadata['section']}" for h in hits], gold)


def _retrieval_report(rows: list[dict[str, Any]]) -> str:
    with_gold = [r for r in rows if r["group"] in ("im_buch", "sokratisch")]
    names = list(rows[0]["ranks"])
    reranked = any(r.get("top_rerank") is not None for r in rows)

    def ranks(name: str) -> list[int | None]:
        return [r["ranks"][name] for r in with_gold]

    lines = [
        "# Retrieval",
        "",
        f"{len(with_gold)} Fragen mit erwartetem Abschnitt. Übungsstellungen sind ausgeschlossen. "
        "Gemessen wird der Rang unter den ersten 20 Treffern.",
        "",
        "| Kennzahl | " + " | ".join(names) + " |",
        "|---" * (len(names) + 1) + "|",
    ]
    for k in (*K_VALUES, 20):
        lines.append(
            f"| Trefferquote@{k} | " + " | ".join(_pct(hit_rate(ranks(n), k)) for n in names) + " |"
        )
    lines.append(
        "| MRR | " + " | ".join(f"{mean_reciprocal_rank(ranks(n)):.2f}" for n in names) + " |"
    )

    lines += ["", "## Rang je Frage", "", "| Frage | " + " | ".join(names) + " |"]
    lines.append("|---" * (len(names) + 1) + "|")
    for r in with_gold:
        cells = [str(r["ranks"][n]) if r["ranks"][n] else "–" for n in names]
        lines.append(f"| `{r['id']}` | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Bester Wert je Gruppe",
        "",
        "Grundlage für eine mögliche Schwelle: Liegen die Gruppen klar getrennt?",
        "",
        "| Gruppe | Vektor min | Median | max |"
        + (" Reranker min | Median | max |" if reranked else ""),
        "|---|---|---|---|" + ("---|---|---|" if reranked else ""),
    ]
    for group in ("im_buch", "sokratisch", "nah_dran", "fremd"):
        line = f"| {group} |"
        for key in ("top_vector", "top_rerank") if reranked else ("top_vector",):
            scores = [r[key] for r in rows if r["group"] == group and r.get(key) is not None]
            line += (
                f" {min(scores):.3f} | {median(scores):.3f} | {max(scores):.3f} |"
                if scores
                else " – | – | – |"
            )
        lines.append(line)
    return "\n".join(lines) + "\n"


# --- answers -------------------------------------------------------------------


def cmd_answers(args: argparse.Namespace) -> None:
    from queryangler.api.app import build_default_tutor

    questions = load_questions(QUESTIONS_FILE)
    if args.only:
        questions = [q for q in questions if q.group == args.only]
    tutor = build_default_tutor()
    rows = asyncio.run(_run_answers(tutor, questions, args.label))
    path = RESULTS_DIR / f"answers_{args.label}_{_stamp()}.jsonl"
    _write_jsonl(path, rows)
    print(f"\n{len(rows)} Antworten -> {path}")
    print("Bewerten: uv run python -m queryangler.evals rate")


async def _run_answers(tutor: Any, questions: list[Question], label: str) -> list[dict[str, Any]]:
    rows = []
    try:
        for n, q in enumerate(questions, start=1):
            print(f"[{n}/{len(questions)}] {q.id} ...", flush=True)
            try:
                answer = await tutor.ask(q.question, mode=q.mode)
            except LLMUnavailableError:
                raise  # Ollama läuft nicht: abbrechen statt 40 Fehler sammeln
            except LLMError as error:
                print(f"    Fehler: {error}")
                continue
            rows.append(_answer_row(q, answer, label))
    finally:
        await tutor.llm.aclose()
    return rows


def _answer_row(q: Question, answer: Any, label: str) -> dict[str, Any]:
    text = answer.text
    row: dict[str, Any] = {
        "id": q.id,
        "group": q.group,
        "mode": q.mode,
        "label": label,
        "model": answer.model,
        "question": q.question,
        "answer": text,
        "sources": [
            {"number": s.number, "id": s.id, "path": f"{s.chapter} > {s.section}", "cited": s.cited}
            for s in answer.sources
        ],
        "llm_seconds": round(answer.timings.get("llm", 0.0), 2),
        "prompt_tokens": answer.prompt_tokens,
        "refusal": is_refusal(text),
    }
    if q.group == "im_buch":
        row["cited_any"] = any(s.cited for s in answer.sources)
        row["missing_keywords"] = missing_keywords(text, q.keywords_all, q.keywords_any)
        row["rating"] = None  # wird mit `rate` gefüllt
    if q.group == "sokratisch":
        row["form_ok"] = follows_socratic_form(text)
        row["leaked"] = leaked_keywords(text, q.question)
        row["code_block"] = has_code_block(text)
        row["meta"] = meta_references(text)
    return row


# --- rate ----------------------------------------------------------------------


def cmd_rate(args: argparse.Namespace) -> None:
    from queryangler.index.store import open_index

    files = [Path(f) for f in args.files] or sorted(RESULTS_DIR.glob("answers_*.jsonl"))
    if not files:
        sys.exit("Keine Antworten gefunden. Bitte zuerst `answers` ausführen.")
    collection = open_index(config.INDEX_DIR, config.EMBED_MODEL, config.EMBED_REVISION)

    print(
        "Bewertung je 0, 1 oder 2. Leer lassen = überspringen, q = beenden (alles ist gespeichert)."
    )
    print("Richtigkeit: 0 falsch, 1 teilweise richtig oder unvollständig, 2 richtig")
    print("Treue:       0 Aussagen ohne Grundlage in den Quellen, 1 teilweise, 2 alles gedeckt\n")
    for path in files:
        rows = _read_jsonl(path)
        todo = [r for r in rows if r["group"] == "im_buch" and r.get("rating") is None]
        for n, row in enumerate(todo, start=1):
            _show(row, collection, f"{path.name}  {n}/{len(todo)}")
            correct = _ask_score("Richtigkeit")
            if correct == "q":
                return
            faithful = _ask_score("Treue") if correct is not None else None
            if faithful == "q":
                return
            if correct is None or faithful is None:
                continue
            note = input("Notiz (optional): ").strip()
            row["rating"] = {"correct": correct, "faithful": faithful, "note": note}
            _write_jsonl(path, rows)  # nach jeder Bewertung speichern
    print("Fertig. Bericht: uv run python -m queryangler.evals report")


def _show(row: dict[str, Any], collection: Any, position: str) -> None:
    width = 100
    print("=" * width)
    print(f"{position}   [{row['label']}]   {row['id']}")
    print(f"FRAGE: {row['question']}\n")
    print("ANTWORT:")
    print(row["answer"])
    if row.get("missing_keywords"):
        print(f"\n(Stichwortprüfung: fehlt {', '.join(row['missing_keywords'])})")
    cited = [s for s in row["sources"] if s["cited"]] or row["sources"]
    texts = collection.get(ids=[s["id"] for s in cited], include=["documents"])
    by_id = dict(zip(texts["ids"], texts["documents"], strict=True))
    print("\n--- QUELLEN (zitiert, sonst alle) ---")
    for s in cited:
        print(f"\n[{s['number']}] {s['path']}")
        print(textwrap.indent(by_id.get(s["id"], "(nicht im Index)")[:2500], "    "))
    print()


def _ask_score(label: str) -> int | str | None:
    while True:
        value = input(f"{label} (0/1/2): ").strip().lower()
        if value in ("", "q"):
            return value or None
        if value in ("0", "1", "2"):
            return int(value)


# --- report --------------------------------------------------------------------


def cmd_report(args: argparse.Namespace) -> None:
    files = sorted(RESULTS_DIR.glob("answers_*.jsonl"))
    if not files:
        sys.exit("Keine Antworten gefunden.")
    runs = {path.stem: _read_jsonl(path) for path in files}
    report = _answers_report(runs)
    out = RESULTS_DIR / "bericht.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"-> {out}")


def _answers_report(runs: dict[str, list[dict[str, Any]]]) -> str:
    names = list(runs)
    lines = [
        "# Antworten",
        "",
        "| Kennzahl | " + " | ".join(names) + " |",
        "|---" * (len(names) + 1) + "|",
    ]

    def row(label: str, fn: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(runs[n]) for n in names) + " |")

    def group(rows: list[dict[str, Any]], g: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["group"] == g]

    def share(rows: list[dict[str, Any]], pred: Any) -> str:
        return f"{sum(1 for r in rows if pred(r))}/{len(rows)}" if rows else "–"

    def rated(rows: list[dict[str, Any]], key: str) -> str:
        scores = [r["rating"][key] for r in group(rows, "im_buch") if r.get("rating")]
        return f"{mean(scores):.2f} (n={len(scores)})" if scores else "–"

    row("Modell", lambda rs: rs[0]["model"] or "–")
    row("**Im Buch**", lambda rs: "")
    row("Quelle zitiert", lambda rs: share(group(rs, "im_buch"), lambda r: r["cited_any"]))
    row(
        "Stichworte vollständig",
        lambda rs: share(group(rs, "im_buch"), lambda r: not r["missing_keywords"]),
    )
    row("Fälschlich abgesagt", lambda rs: share(group(rs, "im_buch"), lambda r: r["refusal"]))
    row("Richtigkeit (0–2)", lambda rs: rated(rs, "correct"))
    row("Treue zur Quelle (0–2)", lambda rs: rated(rs, "faithful"))
    row("**Absage, wo nötig**", lambda rs: "")
    row("Nah dran, nicht im Buch", lambda rs: share(group(rs, "nah_dran"), lambda r: r["refusal"]))
    row("Themenfremd", lambda rs: share(group(rs, "fremd"), lambda r: r["refusal"]))
    row("**Sokratisch**", lambda rs: "")
    row("Form eingehalten", lambda rs: share(group(rs, "sokratisch"), lambda r: r["form_ok"]))
    row(
        "Ohne SQL-Begriffe (Leck)",
        lambda rs: share(
            group(rs, "sokratisch"), lambda r: not r["leaked"] and not r["code_block"]
        ),
    )
    row(
        "Ohne Verweis auf Quellen",
        lambda rs: share(group(rs, "sokratisch"), lambda r: not r["meta"]),
    )
    row("**Laufzeit**", lambda rs: "")
    row("Median Sekunden (LLM)", lambda rs: f"{median(r['llm_seconds'] for r in rs):.1f}")

    lines += ["", "## Auffällige Antworten", ""]
    for name in names:
        for r in runs[name]:
            problems = []
            if r["group"] in ("nah_dran", "fremd") and not r["refusal"]:
                problems.append("keine Absage")
            if r["group"] == "im_buch" and r["refusal"]:
                problems.append("fälschlich abgesagt")
            if r["group"] == "sokratisch":
                if r["leaked"] or r["code_block"]:
                    problems.append(
                        "Leck: "
                        + ", ".join(r["leaked"] + (["Codeblock"] if r["code_block"] else []))
                    )
                if r["meta"]:
                    problems.append("Verweis: " + ", ".join(r["meta"]))
                if not r["form_ok"]:
                    problems.append("Form")
            if problems:
                lines.append(f"- {name} `{r['id']}`: {'; '.join(problems)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m queryangler.evals")
    sub = parser.add_subparsers(dest="command", required=True)
    retrieval = sub.add_parser("retrieval", help="Retrieval messen (ohne Sprachmodell)")
    retrieval.add_argument(
        "--mit-reranker", action="store_true", help="Reranker zusätzlich messen (langsam)"
    )
    retrieval.set_defaults(func=cmd_retrieval)
    answers = sub.add_parser("answers", help="Antworten erzeugen und automatisch prüfen")
    answers.add_argument("--label", required=True, help="Name des Laufs, z. B. ministral")
    answers.add_argument("--only", choices=["im_buch", "nah_dran", "fremd", "sokratisch"])
    answers.set_defaults(func=cmd_answers)
    rate = sub.add_parser("rate", help="Antworten der Gruppe im_buch selbst bewerten")
    rate.add_argument("files", nargs="*", help="Standard: alle answers_*.jsonl")
    rate.set_defaults(func=cmd_rate)
    sub.add_parser("report", help="Bericht über alle Läufe").set_defaults(func=cmd_report)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
