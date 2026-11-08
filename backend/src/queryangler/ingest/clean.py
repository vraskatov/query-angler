"""HTML einer Wikibook-Seite -> Liste von Textblöcken.

Ein Block ist ein Dict mit 'type' und 'text':

    {"type": "heading", "level": 2, "text": "Einführung"}
    {"type": "paragraph", "text": "Die Abfragesprache SQL ..."}
    {"type": "code", "lang": "sql", "text": "SELECT *\\nFROM Mitarbeiter;"}
    {"type": "output", "text": "NAME    VORNAME\\n------  -------"}
    {"type": "list", "text": "- Firebird\\n- MySQL"}
    {"type": "table", "text": "Spalte | Typ\\nID | INTEGER"}

Optionale Felder:
- Code aus Beispielkästen: 'title' (Kastentitel), 'note' (Anmerkung) und 'verdict'
  ("negativ" für rot markierte, "positiv" für grün markierte Beispiele).
- Blöcke in Übungen und Lösungen: 'role' ("exercise" oder "solution") und 'exercise' (Nummer).

Die Blockstruktur bleibt erhalten, damit das Chunking an Überschriften schneiden
und Codeblöcke zusammenhalten kann.
"""

import re
from collections import Counter
from typing import Any

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

Block = dict[str, Any]

# Elemente, die komplett entfernt werden (CSS-Selektoren)
REMOVE_SELECTORS = [
    "style",
    "script",
    "link",
    "meta",
    ".mw-editsection",
    ".noprint",
    ".navbox",
    ".metadata",
    ".toc",
    "#toc",
    ".catlinks",
    ".printfooter",
    ".mw-empty-elt",
    ".sistersitebox",
    ".mw-references-wrap",
    "sup.reference",
    ".linenos",
    "figure",
    "img",
]

# Absätze, die nur Navigation oder Querverweise enthalten
BOILERPLATE_PATTERNS = [
    re.compile(r"zurück zu:.*weiter zu:", re.IGNORECASE),
    re.compile(r"^zum Anfang des Inhaltsverzeichnisses"),
    re.compile(r"^Seitentitel:"),
    re.compile(r"^\(Einführung in SQL:.*\)$"),
    re.compile(r"hat einen Artikel zum Thema"),
    re.compile(r"^Wikibooks hat das Lehrbuch"),
]

# Abschnitte, die nur Querverweise enthalten
DROPPED_SECTIONS = {"Siehe auch"}

# Kopfzeilen der Übungs- und Lösungskästen: "Übung 3 | Titel | Zur Lösung"
EXERCISE_HEAD = re.compile(r"^Übung (\d+)$")
SOLUTION_HEAD = re.compile(r"^Lösung zu Übung (\d+)$")

# Rahmenfarbe der Beispielkästen -> Bewertung (blau = normales Beispiel)
VERDICT_BY_BORDER = {"#FF7777": "negativ", "#77FF77": "positiv"}

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "del", "em", "font", "i", "ins",
    "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time",
    "tt", "u", "var",
}  # fmt: skip
CODE_DIV_CLASSES = {"mw-highlight", "mw-code"}


def html_to_blocks(html: str) -> tuple[list[Block], Counter]:
    """Bereinigt das HTML und gibt (Blöcke, Statistik) zurück."""
    soup = BeautifulSoup(html, "html.parser")
    stats: Counter = Counter()

    for selector in REMOVE_SELECTORS:
        for element in soup.select(selector):
            if not element.decomposed:  # kann schon mit dem Elternelement weg sein
                element.decompose()
                stats[f"entfernt: {selector}"] += 1

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # Inline-Code (`SELECT` im Fließtext) als Markdown-Backticks erhalten.
    # Das Sprachmodell erkennt so, was Schlüsselwort und was Prosa ist.
    for code in soup.find_all(["code", "tt"]):
        if code.find_parent("pre") is None:
            text = code.get_text()
            if text.strip():
                code.replace_with(f"`{text}`")
            else:
                code.replace_with(text)
                stats["entfernt: leerer Inline-Code"] += 1

    root = soup.select_one(".mw-parser-output") or soup
    blocks: list[Block] = []
    _walk(root, blocks, stats)

    blocks = _drop_boilerplate(blocks, stats)
    blocks = _drop_sections(blocks, stats)
    _assign_roles(blocks)
    return blocks, stats


# -- Durchlauf durch den Baum -------------------------------------------------


def _walk(node: Tag, blocks: list[Block], stats: Counter) -> None:
    """Geht die Kinder eines Containers durch.

    Aufeinanderfolgender Inline-Inhalt (Text, Links, Fettdruck ...) wird zu
    einem Absatz gesammelt. Sobald ein Block-Element kommt, wird der Absatz
    abgeschlossen und das Block-Element behandelt.
    """
    buffer: list[str] = []

    def flush() -> None:
        text = _norm("".join(buffer))
        buffer.clear()
        if text:
            blocks.append({"type": "paragraph", "text": text})

    for child in node.children:
        if isinstance(child, NavigableString):
            buffer.append(str(child))
        elif isinstance(child, Tag):
            if child.name in INLINE_TAGS:
                buffer.append(child.get_text())
            else:
                flush()
                _handle_block(child, blocks, stats)
    flush()


def _handle_block(tag: Tag, blocks: list[Block], stats: Counter) -> None:
    name = tag.name
    classes = tag.get("class", [])
    if name in HEADINGS:
        blocks.append({"type": "heading", "level": int(name[1]), "text": _norm(tag.get_text())})
    elif name == "p":
        blocks.append({"type": "paragraph", "text": _norm(tag.get_text())})
    elif name == "pre":
        blocks.append(_code_block(tag, lang=None))
    elif name == "div" and CODE_DIV_CLASSES & set(classes):
        blocks.append(_code_block(tag.find("pre") or tag, lang=_highlight_lang(tag)))
    elif name == "div" and (level := _simulated_heading_level(classes)):
        # Vorlage, die wie eine Überschrift aussieht, technisch aber ein div ist
        blocks.append({"type": "heading", "level": level, "text": _norm(tag.get_text())})
        stats["erkannt: nachgebaute Überschrift"] += 1
    elif name == "div" and "collapsible" in classes:
        _handle_collapsible(tag, blocks, stats)
    elif name == "div" and _is_example_box(tag):
        _handle_example_box(tag, blocks, stats)
    elif name in ("ul", "ol"):
        blocks.append({"type": "list", "text": "\n".join(_list_lines(tag, depth=0))})
    elif name == "table":
        _handle_table(tag, blocks, stats)
    elif name == "dl":
        # MediaWiki nutzt <dl>/<dd> für Einrückungen (":" im Wikitext),
        # oft mit Code darin. Deshalb dd wie einen Container behandeln.
        for item in tag.find_all(["dt", "dd"], recursive=False):
            if item.name == "dt":
                blocks.append({"type": "paragraph", "text": _norm(item.get_text())})
            else:
                _walk(item, blocks, stats)
    elif name in ("hr", "br"):
        pass
    else:
        # div, section, blockquote, center, ...: einfach hineinsteigen
        _walk(tag, blocks, stats)


def _code_block(tag: Tag, lang: str | None) -> Block:
    lines = [line.rstrip() for line in tag.get_text().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return {"type": "code", "lang": lang, "text": "\n".join(lines)}


def _highlight_lang(div: Tag) -> str | None:
    for cls in div.get("class", []):
        if cls.startswith("mw-highlight-lang-"):
            return cls.removeprefix("mw-highlight-lang-")
    return None


def _simulated_heading_level(classes: list[str]) -> int | None:
    for cls in classes:
        if cls.startswith("Vorlage_Ueberschriftensimulation_"):
            return int(cls.rsplit("_", 1)[1])
    return None


def _handle_collapsible(box: Tag, blocks: list[Block], stats: Counter) -> None:
    """Aufklappbarer Kasten: Titel wird Überschrift, Inhalt normal verarbeitet."""
    title, body = box.select_one(".title"), box.select_one(".body")
    if title is not None:
        blocks.append({"type": "heading", "level": 4, "text": _norm(title.get_text())})
    _walk(body or box, blocks, stats)
    stats["erkannt: Aufklappkasten"] += 1


def _is_example_box(div: Tag) -> bool:
    """Beispielkasten des Buchs: Rahmen mit drei Bereichen (Titel, Code, Anmerkung)."""
    return "margin:6px 0px" in div.get("style", "") and len(_child_divs(div)) == 3


def _handle_example_box(box: Tag, blocks: list[Block], stats: Counter) -> None:
    head, body, foot = _child_divs(box)
    border = re.search(r"border:\s*(#[0-9A-Fa-f]{6})", box.get("style", ""))
    verdict = VERDICT_BY_BORDER.get(border.group(1).upper()) if border else None
    extra = {
        key: value
        for key, value in [
            ("title", _norm(head.get_text())),
            ("note", _norm(foot.get_text())),
            ("verdict", verdict),
        ]
        if value
    }

    inner: list[Block] = []
    _walk(body, inner, stats)
    next_is_output = False
    for block in inner:
        # Bereich "Ausgabe": der folgende Codeblock ist das Abfrageergebnis
        if block["type"] == "paragraph" and block["text"] == "Ausgabe":
            next_is_output = True
            continue
        if block["type"] == "code":
            if next_is_output:
                block = {"type": "output", "text": block["text"]}
                next_is_output = False
            else:
                block.update(extra)
        blocks.append(block)
    stats[f"erkannt: Beispielkasten {verdict or 'neutral'}"] += 1


def _child_divs(tag: Tag) -> list[Tag]:
    return tag.find_all("div", recursive=False)


def _list_lines(list_tag: Tag, depth: int) -> list[str]:
    ordered = list_tag.name == "ol"
    lines: list[str] = []
    for number, li in enumerate(list_tag.find_all("li", recursive=False), start=1):
        nested = li.find_all(["ul", "ol"], recursive=False)
        for sub in nested:
            sub.extract()
        text = _norm(li.get_text())
        marker = f"{number}." if ordered else "-"
        if text:
            lines.append(f"{'  ' * depth}{marker} {text}")
        for sub in nested:
            lines.extend(_list_lines(sub, depth + 1))
    return lines


def _handle_table(table: Tag, blocks: list[Block], stats: Counter) -> None:
    """Sonderformen erkennen, sonst Datentabelle als Zeilen oder Layout-Tabelle durchlaufen.

    Ältere Wiki-Seiten benutzen Tabellen auch nur zur Anordnung von Inhalten.
    Merkmale einer echten Datentabelle: Klasse 'wikitable', ein border-Attribut
    oder Kopfzellen (<th>).
    """
    if (head := _exercise_head(table)) is not None:
        blocks.append(head)
        stats[f"erkannt: Kopf {head['role']}"] += 1
        return
    if "limage" in table.get("class", []):
        blocks.append({"type": "paragraph", "text": _callout_text(table)})
        stats["erkannt: Hinweiskasten"] += 1
        return

    is_data = (
        "wikitable" in table.get("class", [])
        or table.get("border") not in (None, "0")
        or table.find("th") is not None
    )
    rows = table.find_all("tr")
    if is_data:
        lines = []
        for tr in rows:
            cells = [_norm(c.get_text()) for c in tr.find_all(["th", "td"], recursive=False)]
            if any(cells):
                lines.append(" | ".join(cells))
        blocks.append({"type": "table", "text": "\n".join(lines)})
    else:
        for tr in rows:
            for cell in tr.find_all(["th", "td"], recursive=False):
                _walk(cell, blocks, stats)


def _exercise_head(table: Tag) -> Block | None:
    """Kopfzeile 'Übung 3 | Titel | Zur Lösung' -> Überschrift mit Rolle."""
    rows = table.find_all("tr")
    if len(rows) != 1:
        return None
    cells = [_norm(c.get_text()) for c in rows[0].find_all("td", recursive=False)]
    if len(cells) != 3:
        return None
    for pattern, role, label in [
        (EXERCISE_HEAD, "exercise", "Übung"),
        (SOLUTION_HEAD, "solution", "Lösung zu Übung"),
    ]:
        if match := pattern.match(cells[0]):
            number = int(match.group(1))
            return {
                "type": "heading",
                "level": 4,
                "text": f"{label} {number}: {cells[1]}",
                "role": role,
                "exercise": number,
            }
    return None


def _callout_text(table: Tag) -> str:
    """Hinweiskasten: '<b>Hinweis</b><br/>Text' -> 'Hinweis: Text'."""
    cell = table.find("td") or table
    label = cell.find("b")
    if label is None:
        return _norm(cell.get_text())
    label_text = _norm(label.extract().get_text()).rstrip(":")
    return f"{label_text}: {_norm(cell.get_text())}"


# -- Nachbearbeitung der Blockliste -------------------------------------------


def _drop_boilerplate(blocks: list[Block], stats: Counter) -> list[Block]:
    result = []
    for block in blocks:
        if not block["text"]:
            continue
        if block["type"] == "paragraph" and _is_boilerplate(block["text"]):
            stats["entfernt: Navigationsabsatz"] += 1
            continue
        result.append(block)
    return result


def _drop_sections(blocks: list[Block], stats: Counter) -> list[Block]:
    """Entfernt Abschnitte wie 'Siehe auch' bis zur nächsten Überschrift gleicher Ebene."""
    result: list[Block] = []
    skip_level: int | None = None
    for block in blocks:
        if block["type"] == "heading":
            if skip_level is not None and block["level"] <= skip_level:
                skip_level = None
            if skip_level is None and block["text"] in DROPPED_SECTIONS:
                skip_level = block["level"]
                stats[f"entfernt: Abschnitt {block['text']}"] += 1
        if skip_level is None:
            result.append(block)
    return result


def _assign_roles(blocks: list[Block]) -> None:
    """Überträgt die Rolle eines Übungs-/Lösungskopfs auf die folgenden Blöcke.

    Eine Übung oder Lösung endet beim nächsten Kopf oder bei der nächsten Überschrift.
    """
    current: dict[str, Any] | None = None
    for block in blocks:
        if block["type"] == "heading":
            role = block.get("role")
            current = {"role": role, "exercise": block["exercise"]} if role else None
        elif current:
            block.update(current)


def _norm(text: str) -> str:
    """Whitespace zusammenfassen (inkl. geschützter Leerzeichen)."""
    return re.sub(r"\s+", " ", text).strip()


def _is_boilerplate(text: str) -> bool:
    return any(pattern.search(text) for pattern in BOILERPLATE_PATTERNS)


# -- Ausgabe zur Kontrolle ----------------------------------------------------

VERDICT_LABEL = {"negativ": "Negativbeispiel", "positiv": "Positivbeispiel"}


def blocks_to_markdown(blocks: list[Block]) -> str:
    """Blöcke als Markdown, damit man das Ergebnis mit eigenen Augen prüfen kann."""
    parts = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            parts.append(f"{'#' * block['level']} {block['text']}")
        elif kind == "code":
            label = VERDICT_LABEL.get(block.get("verdict", ""), "")
            title = block.get("title", "")
            if label or title:
                parts.append(f"**{label}:** {title}" if label else f"*{title}*")
            parts.append(f"```{block['lang'] or ''}\n{block['text']}\n```")
            if block.get("note"):
                parts.append(f"*{block['note']}*")
        elif kind == "output":
            parts.append(f"Ausgabe:\n```\n{block['text']}\n```")
        else:
            parts.append(block["text"])
    return "\n\n".join(parts) + "\n"
