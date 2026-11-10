"""Tests des Chunkings mit kleinen, von Hand gebauten Blocklisten."""

from queryangler.ingest import chunk as chunk_module
from queryangler.ingest.chunk import CONTENT, chunk_page


def page(blocks):
    return {
        "chapter": "Testkapitel",
        "slug": "testkapitel",
        "url": "https://example.org/test",
        "permalink": "https://example.org/test?oldid=1",
        "revid": 1,
        "blocks": blocks,
    }


def h(level, text, **extra):
    return {"type": "heading", "level": level, "text": text, **extra}


def p(text, **extra):
    return {"type": "paragraph", "text": text, **extra}


def code(text, **extra):
    return {"type": "code", "lang": "sql", "text": text, **extra}


LONG = "Lorem ipsum dolor sit amet. " * 20  # gut 500 Zeichen


def test_kopf_mit_kapitel_und_pfad():
    chunks = chunk_page(page([p("Einleitung."), h(2, "Grundlagen"), p(LONG)]))
    assert chunks[0]["text"].startswith("Kapitel: Testkapitel\nAbschnitt: Kapitelanfang\n\n")
    assert chunks[1]["text"].startswith("Kapitel: Testkapitel\nAbschnitt: Grundlagen\n\n")
    assert [c["id"] for c in chunks] == ["testkapitel#001", "testkapitel#002"]


def test_kleine_unterabschnitte_werden_zusammengefasst():
    blocks = [h(2, "JOIN"), p("Kurz."), h(3, "INNER"), p("Auch kurz."), h(3, "OUTER"), p("Kurz.")]
    chunks = chunk_page(page(blocks))
    assert len(chunks) == 1
    assert chunks[0]["metadata"]["section"] == "JOIN"
    assert "### INNER" in chunks[0]["text"] and "### OUTER" in chunks[0]["text"]


def test_keine_zusammenfassung_ueber_hauptabschnitte():
    chunks = chunk_page(page([h(2, "Eins"), p("Kurz."), h(2, "Zwei"), p("Kurz.")]))
    assert [c["metadata"]["section"] for c in chunks] == ["Eins", "Zwei"]


def test_uebungen_und_loesungen_einzeln_mit_rolle():
    blocks = [
        h(2, "Übungen"),
        h(4, "Übung 1: A", role="exercise", exercise=1),
        p("Frage 1?", role="exercise", exercise=1),
        h(4, "Übung 2: B", role="exercise", exercise=2),
        p("Frage 2?", role="exercise", exercise=2),
        h(3, "Lösungen"),
        h(4, "Lösung zu Übung 1: A", role="solution", exercise=1),
        p("Antwort 1.", role="solution", exercise=1),
    ]
    meta = [c["metadata"] for c in chunk_page(page(blocks))]
    assert [(m["role"], m["exercise"]) for m in meta] == [
        ("exercise", 1),
        ("exercise", 2),
        ("solution", 1),
    ]
    assert meta[2]["section"] == "Übungen > Lösungen > Lösung zu Übung 1: A"


def test_grosser_abschnitt_wird_an_blockgrenzen_geteilt():
    blocks = [h(2, "Lang")] + [p(f"Absatz {i}. {LONG}") for i in range(6)]
    chunks = chunk_page(page(blocks))
    assert len(chunks) > 1
    assert all(c["metadata"]["section"] == "Lang" for c in chunks)
    assert all(c["metadata"]["chars"] <= chunk_module.MAX_CHARS + 100 for c in chunks)


def test_code_bleibt_beim_einleitenden_satz():
    blocks = [h(2, "Lang"), p(LONG), p(LONG), p("Leider ohne Erfolg:"), code("Get Name From X")]
    chunks = chunk_page(page(blocks))
    with_code = [c for c in chunks if "Get Name From X" in c["text"]]
    assert len(with_code) == 1
    assert "Leider ohne Erfolg:" in with_code[0]["text"]


def test_doppelpunkt_verbindet_mit_folgendem_absatz():
    blocks = [
        h(2, "Lang"),
        p(LONG),
        p(LONG),
        p("Natürlich geht auch das:"),
        p("Gesucht werden alle mit M."),
        code("SELECT 1;"),
    ]
    with_code = [c for c in chunk_page(page(blocks)) if "SELECT 1;" in c["text"]]
    assert "Natürlich geht auch das:" in with_code[0]["text"]


def test_zu_grosser_einzelblock_wird_nicht_zerschnitten():
    big = "\n".join(f"SELECT {i} FROM t;" for i in range(200))
    chunks = chunk_page(page([h(2, "Code"), code(big)]))
    assert len(chunks) == 1
    assert big in chunks[0]["text"]


def test_negativbeispiel_markiert_und_in_metadaten():
    blocks = [h(2, "JOIN"), code("SELECT …", title="So nicht", verdict="negativ")]
    [chunk] = chunk_page(page(blocks))
    assert "Negativbeispiel (fehlerhaft oder nicht empfohlen):** So nicht" in chunk["text"]
    assert chunk["metadata"]["has_negative_example"] is True


def test_metadaten_enthalten_kein_none():
    # Chroma akzeptiert in Metadaten nur str, int, float und bool
    chunks = chunk_page(page([p("Text."), h(2, "A"), p("Mehr.")]))
    for chunk in chunks:
        assert None not in chunk["metadata"].values()
        assert chunk["metadata"]["role"] == CONTENT


def test_leere_abschnitte_erzeugen_keine_chunks():
    chunks = chunk_page(page([h(2, "Nur Überschrift"), h(2, "Mit Inhalt"), p("Text.")]))
    assert [c["metadata"]["section"] for c in chunks] == ["Mit Inhalt"]
