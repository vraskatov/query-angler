"""Tests der Bereinigung, komplett offline mit einer nachgebauten API-Antwort."""

from pathlib import Path

import pytest

from queryangler.ingest.clean import blocks_to_markdown, html_to_blocks

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "wikibooks_parse_sample.html"


@pytest.fixture(scope="module")
def blocks():
    result, _removed = html_to_blocks(FIXTURE.read_text(encoding="utf-8"))
    return result


def all_text(blocks) -> str:
    return "\n".join(b["text"] for b in blocks)


def test_navigation_und_querverweise_sind_entfernt(blocks):
    text = all_text(blocks)
    for unwanted in [
        "weiter zu",
        "Seitentitel",
        "(Einführung in SQL:",
        "Artikel zum Thema",
        "Bearbeiten",
        "Einzelnachweis",
        "Bild",
        "[1]",
    ]:
        assert unwanted not in text


def test_ueberschriften_mit_ebene(blocks):
    headings = [(b["level"], b["text"]) for b in blocks if b["type"] == "heading"]
    assert headings == [(2, "Grundform"), (3, "Spalten auswählen")]


def test_syntaxhighlight_wird_codeblock_mit_zeilenumbruechen(blocks):
    code = [b for b in blocks if b["type"] == "code"]
    assert code[0] == {"type": "code", "lang": "sql", "text": "SELECT *\nFROM Mitarbeiter;"}


def test_code_in_eingerueckter_definitionsliste(blocks):
    code = [b for b in blocks if b["type"] == "code"]
    assert code[1]["text"] == "SELECT Name AS N\n  FROM Mitarbeiter AS m;"
    assert code[1]["lang"] is None


def test_inline_code_als_backticks(blocks):
    assert "Befehl `SELECT` und seine wichtigsten Klauseln." in all_text(blocks)


def test_verschachtelte_und_nummerierte_listen(blocks):
    lists = [b["text"] for b in blocks if b["type"] == "list"]
    assert lists[0] == (
        "- Spalten werden durch Kommas getrennt.\n"
        "  - Das letzte Element hat kein Komma.\n"
        "- Aliasnamen vergibt man mit `AS`."
    )
    assert lists[1] == "1. Erst FROM\n2. dann SELECT"


def test_datentabelle_als_zeilen(blocks):
    tables = [b["text"] for b in blocks if b["type"] == "table"]
    assert tables == ["Spalte | Typ\nID | INTEGER\nName | VARCHAR(30)"]


def test_reihenfolge_bleibt_erhalten(blocks):
    types = [b["type"] for b in blocks]
    assert types == [
        "paragraph",
        "heading",
        "paragraph",
        "code",
        "heading",
        "paragraph",
        "list",
        "list",
        "code",
        "table",
        "paragraph",
    ]


def test_statistik_zaehlt_entfernungen():
    _blocks, removed = html_to_blocks(FIXTURE.read_text(encoding="utf-8"))
    assert removed["entfernt: Navigationsabsatz"] == 3
    assert removed["entfernt: .noprint"] == 1


def test_markdown_ausgabe():
    md = blocks_to_markdown(
        [
            {"type": "heading", "level": 2, "text": "Grundform"},
            {"type": "code", "lang": "sql", "text": "SELECT 1;"},
        ]
    )
    assert md == "## Grundform\n\n```sql\nSELECT 1;\n```\n"


# -- Sonderformen des Buchs (Ausschnitte aus echtem HTML) ----------------------


@pytest.fixture(scope="module")
def box_blocks():
    html = (FIXTURES / "wikibooks_boxes_sample.html").read_text(encoding="utf-8")
    result, _stats = html_to_blocks(html)
    return result


def find(blocks, **fields):
    return [b for b in blocks if all(b.get(k) == v for k, v in fields.items())]


def test_beispielkasten_ohne_bewertung_mit_ausgabe(box_blocks):
    code = find(box_blocks, type="code", lang="sql")[0]
    assert code["text"] == "select Name\n  from Mitarbeiter;"
    assert "verdict" not in code
    assert find(box_blocks, type="output") == [{"type": "output", "text": "NAME\n------\nMüller"}]
    assert not find(box_blocks, text="Ausgabe")


def test_negativ_und_positivbeispiel(box_blocks):
    negativ = find(box_blocks, verdict="negativ")
    positiv = find(box_blocks, verdict="positiv")
    assert len(negativ) == 1 and negativ[0]["title"] == "Zulässig, aber nicht so schön"
    assert len(positiv) == 1 and positiv[0]["title"] == "Besseres Vorgehen"
    assert positiv[0]["note"] == "Die Auswahlbedingung steht im WHERE"


def test_nachgebaute_ueberschrift(box_blocks):
    assert find(box_blocks, type="heading", level=3, text="Verletzung der 1. Normalform")


def test_hinweiskasten_wird_absatz(box_blocks):
    hint = "Hinweis: Alle Beispiele beziehen sich auf diese Datenstruktur."
    assert find(box_blocks, type="paragraph", text=hint)
    assert not find(box_blocks, type="table")


def test_aufklappkasten(box_blocks):
    assert find(box_blocks, type="heading", level=4, text="Möglichkeiten bei Firebird")
    assert find(box_blocks, type="paragraph", text="Firebird bietet FIRST und SKIP.")


def test_leerer_inline_code_verschwindet(box_blocks):
    assert find(box_blocks, text="Leerer Inline-Code: soll verschwinden.")


def test_uebungen_und_loesungen_tragen_rolle(box_blocks):
    heads = [(b["text"], b.get("role")) for b in box_blocks if b["type"] == "heading"]
    assert ("Übung 1: Definition von JOINs", "exercise") in heads
    assert ("Lösung zu Übung 1: Definition von JOINs", "solution") in heads

    question = find(box_blocks, text="Welche Aussagen sind wahr?")[0]
    assert (question["role"], question["exercise"]) == ("exercise", 1)
    wrong_code = find(box_blocks, type="code", lang=None)[0]
    assert (wrong_code["role"], wrong_code["exercise"]) == ("exercise", 2)
    answer = find(box_blocks, text="Die Aussagen 2 und 3 sind wahr.")[0]
    assert (answer["role"], answer["exercise"]) == ("solution", 1)

    assert "Zur Lösung" not in all_text(box_blocks)
    assert "Zur Übung" not in all_text(box_blocks)


def test_ueberschrift_beendet_rolle(box_blocks):
    zwischen = find(box_blocks, type="heading", text="Lösungen")[0]
    assert "role" not in zwischen
    assert "role" not in find(box_blocks, text="Dieser Abschnitt bleibt erhalten.")[0]


def test_abschnitt_siehe_auch_entfernt(box_blocks):
    assert not find(box_blocks, text="Siehe auch")
    assert "Unterabfragen" not in all_text(box_blocks)
    assert find(box_blocks, type="heading", text="Danach")


def test_code_im_listenpunkt_bleibt_codeblock(box_blocks):
    i = next(
        i for i, b in enumerate(box_blocks) if b["text"] == "1. Folgende Gruppierung ist zulässig."
    )
    assert box_blocks[i]["type"] == "list"
    assert box_blocks[i + 1] == {
        "type": "code",
        "lang": "sql",
        "text": "select PLZ, COUNT(*)\n  from Versicherungsnehmer vn",
    }
    assert box_blocks[i + 2] == {"type": "list", "text": "2. Zweiter Punkt"}
