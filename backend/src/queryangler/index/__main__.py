"""Kommandozeile für Index und Suche.

uv run python -m queryangler.index build                      # Index aus den Chunks bauen
uv run python -m queryangler.index search "Frage"             # ähnlichste Chunks anzeigen
uv run python -m queryangler.index search "Frage" --ohne-loesungen
"""

import argparse
import sys
import time

from queryangler.config import CHUNKS_FILE, EMBED_DEVICE, EMBED_MODEL, INDEX_DIR
from queryangler.index.embedder import SentenceTransformerEmbedder
from queryangler.index.store import IndexUnavailableError, build_index, open_index
from queryangler.ingest.util import read_jsonl
from queryangler.retrieval import Retriever


def cmd_build(args: argparse.Namespace) -> None:
    if not CHUNKS_FILE.exists():
        sys.exit("Keine Chunks gefunden. Bitte zuerst `ingest chunk` ausführen.")
    chunks = read_jsonl(CHUNKS_FILE)
    print(f"Lade Modell {EMBED_MODEL} auf {EMBED_DEVICE} (beim ersten Mal mit Download) ...")
    embedder = SentenceTransformerEmbedder(EMBED_MODEL, device=EMBED_DEVICE)
    print(f"Bette {len(chunks)} Chunks ein ...")
    start = time.perf_counter()
    collection = build_index(chunks, embedder, INDEX_DIR)
    seconds = time.perf_counter() - start
    print(f"\n{collection.count()} Chunks in {seconds:.0f} s indiziert -> {INDEX_DIR}")


def cmd_search(args: argparse.Namespace) -> None:
    embedder = SentenceTransformerEmbedder(EMBED_MODEL, device=EMBED_DEVICE)
    try:
        collection = open_index(INDEX_DIR, embedder.name)
    except IndexUnavailableError as error:
        sys.exit(str(error))
    hits = Retriever(collection, embedder).search(
        args.question, k=args.k, include_solutions=not args.ohne_loesungen
    )
    for rank, hit in enumerate(hits, start=1):
        meta = hit.metadata
        print(f"\n#{rank}  Ähnlichkeit {hit.score:.3f}  {hit.id}  [{meta['role']}]")
        print(f"    {meta['chapter']} > {meta['section']}")
        body = hit.text.split("\n\n", 1)[-1]  # ohne den Kopf mit Kapitel und Abschnitt
        print("    " + body[:300].replace("\n", "\n    ") + (" …" if len(body) > 300 else ""))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m queryangler.index",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="Index aus data/chunks/ bauen").set_defaults(func=cmd_build)
    search = sub.add_parser("search", help="ähnlichste Chunks zu einer Frage anzeigen")
    search.add_argument("question", help="die Frage, in Anführungszeichen")
    search.add_argument("--k", type=int, default=5, help="Anzahl Treffer (Standard: 5)")
    search.add_argument("--ohne-loesungen", action="store_true", help="Musterlösungen ausblenden")
    search.set_defaults(func=cmd_search)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
