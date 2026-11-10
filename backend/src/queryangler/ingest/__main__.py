"""Kommandozeile für den Ingest.

    uv run python -m queryangler.ingest list           # welche Seiten gibt es?
    uv run python -m queryangler.ingest fetch          # Rohdaten laden -> data/raw/
    uv run python -m queryangler.ingest clean          # bereinigen     -> data/clean/
    uv run python -m queryangler.ingest show <slug>    # ein Kapitel als Markdown ansehen
    uv run python -m queryangler.ingest chunk          # in Chunks zerlegen -> data/chunks/
    uv run python -m queryangler.ingest chunks <slug>  # Chunks eines Kapitels ansehen

Rohdaten und abgeleitete Daten liegen bewusst getrennt: Abgerufen wird einmal,
bereinigt und gechunkt beliebig oft, ohne erneut ins Netz zu gehen.
"""

import argparse
import os
import statistics
import sys
from collections import Counter

from queryangler.config import ATTRIBUTION_FILE, CHUNKS_FILE, CLEAN_DIR, MANIFEST_FILE, RAW_DIR
from queryangler.ingest.attribution import render_attribution
from queryangler.ingest.chunk import MAX_CHARS, chunk_page
from queryangler.ingest.clean import blocks_to_markdown, html_to_blocks
from queryangler.ingest.util import (
    now_iso,
    read_json,
    read_jsonl,
    slugify,
    write_json,
    write_jsonl,
)
from queryangler.ingest.wiki import (
    WikiClient,
    chapter_name,
    history_url,
    is_content_page,
    page_url,
    permalink,
)


def _contact(args: argparse.Namespace) -> str:
    contact = args.contact or os.environ.get("QUERYANGLER_CONTACT")
    if not contact:
        sys.exit(
            "Fehler: Kontaktangabe fehlt. Wikimedia verlangt einen User-Agent, über den man "
            "den Betreiber erreichen kann.\n"
            "  export QUERYANGLER_CONTACT='https://github.com/<dein-name>'   (oder eine E-Mail)"
        )
    return contact


def cmd_list(args: argparse.Namespace) -> None:
    with WikiClient(_contact(args), delay=args.delay) as wiki:
        titles = wiki.list_book_pages()
    included = [t for t in titles if is_content_page(t)]
    for title in titles:
        mark = "+" if is_content_page(title) else "-"
        print(f"  {mark} {chapter_name(title)}")
    print(f"\n{len(titles)} Seiten gefunden, {len(included)} werden übernommen (+).")


def cmd_fetch(args: argparse.Namespace) -> None:
    with WikiClient(_contact(args), delay=args.delay) as wiki:
        license_info = wiki.license()
        titles = [t for t in wiki.list_book_pages() if is_content_page(t)]
        if args.limit:
            titles = titles[: args.limit]

        pages, written = [], set()
        for i, title in enumerate(titles, start=1):
            page = wiki.fetch_page(title)
            chapter = chapter_name(page["title"])
            slug = slugify(chapter)
            if f"{slug}.json" in written:  # zwei Kapitel mit gleichem Slug
                slug = f"{slug}-{page['pageid']}"
            meta = {
                "title": page["title"],
                "chapter": chapter,
                "slug": slug,
                "pageid": page["pageid"],
                "revid": page["revid"],
                "url": page_url(page["title"]),
                "permalink": permalink(page["revid"]),
                "history_url": history_url(page["title"]),
            }
            write_json(RAW_DIR / f"{slug}.json", {**meta, "html": page["html"]})
            written.add(f"{slug}.json")
            pages.append(meta)
            print(f"  [{i:>3}/{len(titles)}] {chapter} (Revision {page['revid']})")

    _remove_stale(RAW_DIR, keep=written | {MANIFEST_FILE.name})
    manifest = {"fetched_at": now_iso(), "license": license_info, "pages": pages}
    write_json(MANIFEST_FILE, manifest)
    ATTRIBUTION_FILE.write_text(render_attribution(manifest), encoding="utf-8")
    print(f"\n{len(pages)} Kapitel in {RAW_DIR} gespeichert.")
    print(f"Lizenz laut API: {license_info['text']} ({license_info['url']})")
    print(f"{ATTRIBUTION_FILE.name} erzeugt.")


def cmd_clean(args: argparse.Namespace) -> None:
    if not MANIFEST_FILE.exists():
        sys.exit("Keine Rohdaten gefunden. Bitte zuerst `fetch` ausführen.")
    manifest = read_json(MANIFEST_FILE)

    block_types: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    stats_total: Counter[str] = Counter()
    empty_pages, written = [], set()
    for meta in manifest["pages"]:
        raw = read_json(RAW_DIR / f"{meta['slug']}.json")
        blocks, stats = html_to_blocks(raw["html"])
        block_types.update(b["type"] for b in blocks)
        roles.update(b["role"] for b in blocks if "role" in b)
        stats_total.update(stats)
        if not blocks:
            empty_pages.append(meta["chapter"])
        write_json(CLEAN_DIR / f"{meta['slug']}.json", {**meta, "blocks": blocks})
        written.add(f"{meta['slug']}.json")
    _remove_stale(CLEAN_DIR, keep=written)

    print(f"{len(written)} Kapitel bereinigt -> {CLEAN_DIR}\n")
    print("Erzeugte Blöcke:")
    for block_type, count in block_types.most_common():
        print(f"  {block_type:<10} {count:>5}")
    print(f"  davon in Übungen {roles['exercise']}, in Lösungen {roles['solution']}")
    for group in ("erkannt", "entfernt"):
        print(f"\n{group.capitalize()}:")
        for what, count in sorted(stats_total.items(), key=lambda item: -item[1]):
            if what.startswith(f"{group}: "):
                print(f"  {what.removeprefix(f'{group}: '):<32} {count:>5}")
    if empty_pages:
        print(f"\nWarnung: {len(empty_pages)} Kapitel ohne Inhalt: {', '.join(empty_pages)}")


def cmd_show(args: argparse.Namespace) -> None:
    path = CLEAN_DIR / f"{args.slug}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in CLEAN_DIR.glob("*.json")))
        sys.exit(f"Kein Kapitel '{args.slug}'. Verfügbar: {available}")
    page = read_json(path)
    print(f"<!-- {page['title']} | Revision {page['revid']} -->\n")
    print(blocks_to_markdown(page["blocks"]))


def cmd_chunk(args: argparse.Namespace) -> None:
    pages = sorted(CLEAN_DIR.glob("*.json"))
    if not pages:
        sys.exit("Keine bereinigten Daten gefunden. Bitte zuerst `clean` ausführen.")
    chunks = [chunk for path in pages for chunk in chunk_page(read_json(path))]
    write_jsonl(CHUNKS_FILE, chunks)

    sizes = [c["metadata"]["chars"] for c in chunks]
    roles = Counter(c["metadata"]["role"] for c in chunks)
    oversized = [c for c in chunks if c["metadata"]["chars"] > MAX_CHARS + 200]
    print(f"{len(chunks)} Chunks aus {len(pages)} Kapiteln -> {CHUNKS_FILE}\n")
    print("Nach Rolle:")
    for role, count in roles.most_common():
        print(f"  {role:<10} {count:>5}")
    print("\nGröße in Zeichen (inkl. Kopf):")
    print(f"  Minimum {min(sizes)}, Median {statistics.median(sizes):.0f}, Maximum {max(sizes)}")
    negatives = sum(c["metadata"]["has_negative_example"] for c in chunks)
    print(f"  mit Negativbeispiel: {negatives}")
    if oversized:
        print(
            f"\n{len(oversized)} Chunks deutlich über {MAX_CHARS} Zeichen (einzelne große Blöcke):"
        )
        for chunk in sorted(oversized, key=lambda c: -c["metadata"]["chars"])[:10]:
            print(
                f"  {chunk['metadata']['chars']:>6}  {chunk['id']}  {chunk['metadata']['section']}"
            )


def cmd_chunks(args: argparse.Namespace) -> None:
    if not CHUNKS_FILE.exists():
        sys.exit("Keine Chunks gefunden. Bitte zuerst `chunk` ausführen.")
    selected = [c for c in read_jsonl(CHUNKS_FILE) if c["metadata"]["slug"] == args.slug]
    if not selected:
        sys.exit(f"Keine Chunks für '{args.slug}'.")
    for chunk in selected:
        meta = chunk["metadata"]
        info = f"{meta['role']}, {meta['chars']} Zeichen"
        if meta["has_negative_example"]:
            info += ", enthält Negativbeispiel"
        print(f"===== {chunk['id']} ({info}) =====\n{chunk['text']}")


def _remove_stale(directory, keep: set[str]) -> None:
    """Entfernt JSON-Dateien von Kapiteln, die es nicht mehr gibt."""
    for path in directory.glob("*.json"):
        if path.name not in keep:
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m queryangler.ingest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--contact", help="Kontakt für den User-Agent (sonst QUERYANGLER_CONTACT)")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Pause zwischen API-Anfragen in Sekunden (Standard: 1.0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Seiten des Buchs auflisten").set_defaults(func=cmd_list)
    fetch = sub.add_parser("fetch", help="Kapitel laden und unverändert speichern")
    fetch.add_argument("--limit", type=int, help="nur die ersten N Kapitel (zum Testen)")
    fetch.set_defaults(func=cmd_fetch)
    sub.add_parser("clean", help="Rohdaten in Textblöcke umwandeln").set_defaults(func=cmd_clean)
    show = sub.add_parser("show", help="ein bereinigtes Kapitel als Markdown ausgeben")
    show.add_argument("slug", help="Dateiname ohne .json, z. B. einleitung")
    show.set_defaults(func=cmd_show)
    sub.add_parser("chunk", help="bereinigte Kapitel in Chunks zerlegen").set_defaults(
        func=cmd_chunk
    )
    chunks = sub.add_parser("chunks", help="die Chunks eines Kapitels ausgeben")
    chunks.add_argument("slug", help="Dateiname ohne .json, z. B. arbeiten-mit-join")
    chunks.set_defaults(func=cmd_chunks)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
