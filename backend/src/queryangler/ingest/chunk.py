"""Textblöcke eines Kapitels -> Chunks für den Vektorindex.

Vorgehen:
1. Abschnitte bilden: An jeder Überschrift beginnt ein neuer Abschnitt.
   Jeder Abschnitt kennt seinen Pfad (Überschriften darüber) und seine Rolle.
2. Kleine Nachbarabschnitte zusammenfassen, solange sie im selben Hauptabschnitt
   liegen und keine Übung oder Lösung sind.
3. Große Abschnitte an Blockgrenzen teilen. Ein Codeblock bleibt dabei immer bei
   dem Absatz, der ihn einleitet, und wird nie zerschnitten.
4. Jeder Chunk bekommt einen Kopf mit Kapitel und Abschnittspfad und Metadaten
   für Quellenangabe und Filter.
"""

from dataclasses import dataclass, field
from typing import Any

from queryangler.ingest.clean import Block, blocks_to_markdown

# Größen in Zeichen. Ein deutsches Wort entspricht grob ein bis zwei Tokens,
# 1500 Zeichen also etwa 300 bis 500 Tokens.
MAX_CHARS = 1500
MIN_CHARS = 400

INTRO = "Kapitelanfang"
CONTENT = "content"  # Rolle normaler Lehrtexte (Chroma erlaubt kein None als Metadatum)


@dataclass
class Section:
    path: list[str]
    role: str = CONTENT
    exercise: int = 0
    blocks: list[Block] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(blocks_to_markdown(self.blocks)) if self.blocks else 0


def chunk_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Zerlegt ein bereinigtes Kapitel (Datei aus data/clean/) in Chunks."""
    sections = _merge_small(_split_sections(page["blocks"]))
    chunks = []
    for section in sections:
        for part in _split_large(section):
            chunks.append(_make_chunk(page, section, part, number=len(chunks) + 1))
    return chunks


# -- 1. Abschnitte bilden -----------------------------------------------------


def _split_sections(blocks: list[Block]) -> list[Section]:
    sections = [Section(path=[])]
    stack: list[tuple[int, str]] = []  # (Ebene, Text) der Überschriften über der Position
    for block in blocks:
        if block["type"] == "heading":
            while stack and stack[-1][0] >= block["level"]:
                stack.pop()
            stack.append((block["level"], block["text"]))
            sections.append(
                Section(
                    path=[text for _level, text in stack],
                    role=block.get("role", CONTENT),
                    exercise=block.get("exercise", 0),
                )
            )
        else:
            sections[-1].blocks.append(block)
    return [s for s in sections if s.blocks]


# -- 2. Kleine Abschnitte zusammenfassen --------------------------------------


def _merge_small(sections: list[Section]) -> list[Section]:
    merged: list[Section] = []
    for section in sections:
        previous = merged[-1] if merged else None
        if previous is not None and _can_merge(previous, section):
            # Der Chunk-Kopf zeigt den gemeinsamen Pfad, die Überschriften der
            # zusammengefassten Unterabschnitte bleiben im Text sichtbar.
            common = _common_prefix(previous.path, section.path)
            if len(previous.path) > len(common):
                previous.blocks.insert(0, _heading(previous.path))
                previous.path = common
            previous.blocks.extend([_heading(section.path), *section.blocks])
        else:
            merged.append(section)
    return merged


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    prefix = []
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        prefix.append(x)
    return prefix


def _heading(path: list[str]) -> Block:
    return {"type": "heading", "level": len(path) + 1, "text": path[-1]}


def _can_merge(a: Section, b: Section) -> bool:
    return (
        a.role == b.role == CONTENT
        and len(b.path) >= 2  # b ist ein Unterabschnitt, keine neue Hauptüberschrift
        and a.path[:1] == b.path[:1]  # und liegt im selben Hauptabschnitt wie a
        and (a.size < MIN_CHARS or b.size < MIN_CHARS)
        and a.size + b.size <= MAX_CHARS
    )


# -- 3. Große Abschnitte teilen -----------------------------------------------


def _split_large(section: Section) -> list[list[Block]]:
    if section.size <= MAX_CHARS:
        return [section.blocks]
    parts: list[list[Block]] = []
    current: list[Block] = []
    for unit in _units(section.blocks):
        candidate = current + unit
        if current and len(blocks_to_markdown(candidate)) > MAX_CHARS:
            parts.append(current)
            current = list(unit)
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _units(blocks: list[Block]) -> list[list[Block]]:
    """Fasst Blöcke, die zusammengehören, zu Einheiten zusammen, die nie getrennt werden.

    - Code und Ausgabe gehören zum Block davor, meist dem einleitenden Satz.
    - Ein Absatz, der mit einem Doppelpunkt endet, kündigt das Folgende an
      („Natürlich sind Einschränkungen auf beide Tabellen möglich:“).
    """
    units: list[list[Block]] = []
    for block in blocks:
        if units and (block["type"] in ("code", "output") or _announces_next(units[-1][-1])):
            units[-1].append(block)
        else:
            units.append([block])
    return units


def _announces_next(block: Block) -> bool:
    return block["type"] == "paragraph" and block["text"].endswith(":")


# -- 4. Chunk erzeugen --------------------------------------------------------


def _make_chunk(
    page: dict[str, Any], section: Section, blocks: list[Block], number: int
) -> dict[str, Any]:
    section_path = " > ".join(section.path) or INTRO
    header = f"Kapitel: {page['chapter']}\nAbschnitt: {section_path}"
    text = f"{header}\n\n{blocks_to_markdown(blocks)}"
    return {
        "id": f"{page['slug']}#{number:03d}",
        "text": text,
        "metadata": {
            "chapter": page["chapter"],
            "slug": page["slug"],
            "section": section_path,
            "role": section.role,
            "exercise": section.exercise,
            "has_negative_example": any(b.get("verdict") == "negativ" for b in blocks),
            "url": page["url"],
            "permalink": page["permalink"],
            "revid": page["revid"],
            "chars": len(text),
        },
    }
