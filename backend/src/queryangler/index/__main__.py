"""Kommandozeile für Index und Suche.

uv run python -m queryangler.index models                     # Modelle herunterladen (einmalig)
uv run python -m queryangler.index build                      # Index aus den Chunks bauen
uv run python -m queryangler.index search "Frage"             # ähnlichste Chunks anzeigen
uv run python -m queryangler.index search "Frage" --ohne-loesungen
uv run python -m queryangler.index search "Frage" --mit-hybrid      # zusätzlich BM25
uv run python -m queryangler.index search "Frage" --mit-reranker    # zusätzlich Reranker
"""

import argparse
import sys
import time

from queryangler.components import load_retriever
from queryangler.config import (
    CHUNKS_FILE,
    EMBED_DEVICE,
    EMBED_MODEL,
    EMBED_REVISION,
    INDEX_DIR,
    RERANK_MODEL,
    RERANK_REVISION,
    UPLOAD_DIR,
)
from queryangler.index.embedder import ModelUnavailableError, SentenceTransformerEmbedder
from queryangler.index.store import IndexUnavailableError, build_index
from queryangler.ingest.util import read_jsonl
from queryangler.sources import SourceManager


def cmd_build(args: argparse.Namespace) -> None:
    if not CHUNKS_FILE.exists():
        sys.exit("Keine Chunks gefunden. Bitte zuerst `ingest chunk` ausführen.")
    chunks = read_jsonl(CHUNKS_FILE)
    print(f"Lade Modell {EMBED_MODEL}@{EMBED_REVISION[:8]} (beim ersten Mal mit Download) ...")
    embedder = SentenceTransformerEmbedder(
        EMBED_MODEL, revision=EMBED_REVISION, device=EMBED_DEVICE
    )
    print(f"Bette {len(chunks)} Chunks ein ...")
    start = time.perf_counter()
    collection = build_index(chunks, embedder, INDEX_DIR)
    seconds = time.perf_counter() - start
    print(f"\n{collection.count()} Chunks in {seconds:.0f} s indiziert -> {INDEX_DIR}")

    # Hochgeladenes Material der Lehrenden geht beim Neubau sonst verloren
    uploads = SourceManager(collection, embedder, UPLOAD_DIR).reindex_all()
    if uploads:
        print(f"{uploads} Chunks aus hochgeladenem Material wieder aufgenommen")


def cmd_models(args: argparse.Namespace) -> None:
    """Lädt beide Modelle in den lokalen Zwischenspeicher, ohne den Index neu zu bauen."""
    from queryangler.index.reranker import CrossEncoderReranker

    print(f"Embedding-Modell {EMBED_MODEL}@{EMBED_REVISION[:8]} ...")
    SentenceTransformerEmbedder(EMBED_MODEL, revision=EMBED_REVISION, device=EMBED_DEVICE)
    print(f"Reranker {RERANK_MODEL} ...")
    CrossEncoderReranker(RERANK_MODEL, revision=RERANK_REVISION, device=EMBED_DEVICE)
    print("Fertig. Suche und Server laden die Modelle ab jetzt offline.")


def cmd_search(args: argparse.Namespace) -> None:
    try:
        retriever = load_retriever(rerank=args.mit_reranker, hybrid=args.mit_hybrid)
    except (ModelUnavailableError, IndexUnavailableError) as error:
        sys.exit(str(error))
    hits = retriever.search(args.question, k=args.k, include_solutions=not args.ohne_loesungen)
    for rank, hit in enumerate(hits, start=1):
        meta = hit.metadata
        rerank = "" if hit.rerank_score is None else f"  Reranker {hit.rerank_score:.3f}"
        if hit.fusion_score is not None:
            rerank = f"  Fusion {hit.fusion_score:.4f}" + rerank
        print(f"\n#{rank}  Ähnlichkeit {hit.score:.3f}{rerank}  {hit.id}  [{meta['role']}]")
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
    sub.add_parser("models", help="Modelle herunterladen (einmalig, mit Netz)").set_defaults(
        func=cmd_models
    )
    search = sub.add_parser("search", help="ähnlichste Chunks zu einer Frage anzeigen")
    search.add_argument("question", help="die Frage, in Anführungszeichen")
    search.add_argument("--k", type=int, default=5, help="Anzahl Treffer (Standard: 5)")
    search.add_argument("--ohne-loesungen", action="store_true", help="Musterlösungen ausblenden")
    search.add_argument("--mit-hybrid", action="store_true", help="Stichwortsuche zuschalten")
    search.add_argument("--mit-reranker", action="store_true", help="Reranker zuschalten (langsam)")
    search.set_defaults(func=cmd_search)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
