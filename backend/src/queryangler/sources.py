"""Eigenes Material von Lehrenden: hochladen, zerlegen, in den Index aufnehmen, entfernen.

Erlaubt sind Markdown (.md) und reiner Text (.txt) in UTF-8. Markdown wird an den
Überschriften zerlegt, wie das Buch; reiner Text an Leerzeilen. PDF und Word sind bewusst
nicht dabei: Text aus PDFs ist fehleranfällig (Spalten, Kopfzeilen, Silbentrennung).

Hochgeladene Chunks tragen in den Metadaten source="upload" und eine source_id. So lassen
sie sich gezielt wieder entfernen und beim Neubau des Index wieder aufnehmen. Die Liste
der Quellen steht in data/uploads/sources.json, die Originaldateien daneben.
"""

import json
import re
import secrets
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from queryangler.index.embedder import Embedder

ALLOWED_SUFFIXES = (".md", ".txt")
MAX_BYTES = 1_000_000
MAX_CHARS = 1500  # wie beim Buch


class SourceError(ValueError):
    """Die Datei kann nicht aufgenommen werden; die Meldung ist für Lehrende gedacht."""


@dataclass
class Source:
    id: str
    title: str
    license: str
    filename: str
    chunks: int
    uploaded_at: str


# --- Zerlegen ------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def chunk_document(text: str, title: str, suffix: str, source_id: str, license: str) -> list[dict]:
    """Zerlegt einen Text in Chunks im selben Format wie die Buch-Chunks."""
    sections = _markdown_sections(text) if suffix == ".md" else [("", text)]
    chunks: list[dict[str, Any]] = []
    for path, body in sections:
        for part in _split(body):
            number = len(chunks) + 1
            section = path or "Text"
            chunks.append(
                {
                    "id": f"upload-{source_id}#{number:03d}",
                    "text": f"Kapitel: {title}\nAbschnitt: {section}\n\n{part}",
                    "metadata": {
                        "chapter": title,
                        "section": section,
                        "role": "content",
                        "exercise": 0,
                        "has_negative_example": False,
                        "url": "",
                        "permalink": "",
                        "source": "upload",
                        "source_id": source_id,
                        "license": license,
                        "chars": len(part),
                    },
                }
            )
    return chunks


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    """(Pfad der Überschriften, Text darunter). Überschriften in Codeblöcken zählen nicht."""
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sections.append((" > ".join(t for _, t in stack), body))
        lines.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
        else:
            lines.append(line)
    flush()
    return sections


def _split(body: str) -> list[str]:
    """Teilt zu lange Abschnitte an Leerzeilen, nie innerhalb eines Codeblocks."""
    blocks: list[str] = []
    current: list[str] = []
    in_code = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not line.strip() and not in_code:
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    parts: list[str] = []
    buffer = ""
    for block in blocks:
        candidate = f"{buffer}\n\n{block}" if buffer else block
        if buffer and len(candidate) > MAX_CHARS:
            parts.append(buffer)
            buffer = block
        else:
            buffer = candidate
    if buffer:
        parts.append(buffer)
    return parts


# --- Verwalten -----------------------------------------------------------------


class SourceManager:
    def __init__(self, collection: Any, embedder: Embedder, upload_dir: Path) -> None:
        self.collection = collection
        self.embedder = embedder
        self.upload_dir = upload_dir
        self._manifest = upload_dir / "sources.json"

    def entries(self) -> list[Source]:
        if not self._manifest.exists():
            return []
        return [Source(**s) for s in json.loads(self._manifest.read_text(encoding="utf-8"))]

    def add(self, filename: str, data: bytes, title: str, license: str) -> Source:
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise SourceError("Erlaubt sind nur Markdown (.md) und Text (.txt).")
        if len(data) > MAX_BYTES:
            raise SourceError("Die Datei ist größer als 1 MB.")
        title, license = title.strip(), license.strip()
        if not title or not license:
            raise SourceError("Titel und Lizenzangabe sind Pflicht.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceError("Die Datei ist nicht in UTF-8 gespeichert.") from error

        source_id = f"{_slug(title)}-{secrets.token_hex(3)}"
        chunks = chunk_document(text, title, suffix, source_id, license)
        if not chunks:
            raise SourceError("Die Datei enthält keinen Text.")
        self._add_to_index(chunks)

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{source_id}{suffix}"
        (self.upload_dir / stored_name).write_bytes(data)
        source = Source(
            id=source_id,
            title=title,
            license=license,
            filename=stored_name,
            chunks=len(chunks),
            uploaded_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._save([*self.entries(), source])
        return source

    def remove(self, source_id: str) -> None:
        sources = self.entries()
        source = next((s for s in sources if s.id == source_id), None)
        if source is None:
            raise KeyError(source_id)
        self.collection.delete(where={"source_id": source_id})
        (self.upload_dir / source.filename).unlink(missing_ok=True)
        self._save([s for s in sources if s.id != source_id])

    def reindex_all(self) -> int:
        """Nach einem Neubau des Index: alle hochgeladenen Quellen wieder aufnehmen."""
        total = 0
        for source in self.entries():
            path = self.upload_dir / source.filename
            text = path.read_text(encoding="utf-8")
            chunks = chunk_document(text, source.title, path.suffix, source.id, source.license)
            self._add_to_index(chunks)
            total += len(chunks)
        return total

    def _add_to_index(self, chunks: list[dict]) -> None:
        self.collection.add(
            ids=[c["id"] for c in chunks],
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks],
            embeddings=self.embedder.embed_documents([c["text"] for c in chunks]),
        )

    def _save(self, sources: list[Source]) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._manifest.write_text(
            json.dumps([asdict(s) for s in sources], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40] or "quelle"
