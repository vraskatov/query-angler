"""Tests für eigenes Material: Zerlegen, Hochladen, Entfernen, Neubau."""

import pytest
from fakes import FakeEmbedder, FakeLLM
from fastapi.testclient import TestClient

from queryangler.api.app import create_app
from queryangler.index.store import IndexUnavailableError, build_index, open_index
from queryangler.retrieval import Retriever
from queryangler.sources import MAX_CHARS, SourceError, SourceManager, chunk_document
from queryangler.tutor import Tutor

MARKDOWN = """# Fensterfunktionen

Einleitung zu Fensterfunktionen.

## ROW_NUMBER

ROW_NUMBER nummeriert Zeilen innerhalb einer Partition.

```sql
# Das ist keine Überschrift, sondern ein Kommentar im Code
SELECT ROW_NUMBER() OVER (PARTITION BY Ort ORDER BY Name) FROM Abteilung;
```

## LAG

LAG liefert den Wert der vorherigen Zeile.
"""


def test_markdown_is_split_at_headings_with_path():
    chunks = chunk_document(MARKDOWN, "Skript Woche 3", ".md", "skript-abc123", "CC BY 4.0")
    sections = [c["metadata"]["section"] for c in chunks]
    assert sections == [
        "Fensterfunktionen",
        "Fensterfunktionen > ROW_NUMBER",
        "Fensterfunktionen > LAG",
    ]
    row_number = chunks[1]
    assert row_number["text"].startswith(
        "Kapitel: Skript Woche 3\nAbschnitt: Fensterfunktionen > ROW_NUMBER"
    )
    assert "# Das ist keine Überschrift" in row_number["text"]  # Code bleibt ganz
    assert row_number["metadata"]["source"] == "upload"
    assert row_number["metadata"]["source_id"] == "skript-abc123"
    assert row_number["metadata"]["license"] == "CC BY 4.0"
    assert row_number["id"] == "upload-skript-abc123#002"


def test_long_text_is_split_at_blank_lines_never_inside_code():
    paragraphs = "\n\n".join(f"Absatz {i}: " + "x" * 400 for i in range(6))
    code = "```sql\n" + "\n\n".join(f"SELECT {i};" for i in range(50)) + "\n```"
    chunks = chunk_document(paragraphs + "\n\n" + code, "T", ".txt", "t-1", "eigene")
    assert len(chunks) > 1
    assert all(c["metadata"]["section"] == "Text" for c in chunks)
    code_chunks = [c for c in chunks if "```sql" in c["text"]]
    assert len(code_chunks) == 1 and code_chunks[0]["text"].rstrip().endswith("```")
    assert all(c["metadata"]["chars"] <= MAX_CHARS for c in chunks if "```" not in c["text"])


@pytest.fixture
def manager(tmp_path):
    embedder = FakeEmbedder()
    collection = build_index([], embedder, tmp_path / "index")
    return SourceManager(collection, embedder, tmp_path / "uploads")


def test_add_list_remove(manager):
    source = manager.add("woche3.md", MARKDOWN.encode(), "Skript Woche 3", "CC BY 4.0")
    assert source.chunks == 3
    assert [s.id for s in manager.entries()] == [source.id]
    assert manager.collection.count() == 3
    assert (manager.upload_dir / source.filename).exists()

    manager.remove(source.id)
    assert manager.entries() == []
    assert manager.collection.count() == 0
    assert not (manager.upload_dir / source.filename).exists()


@pytest.mark.parametrize(
    ("filename", "data", "title", "message"),
    [
        ("folien.pdf", b"%PDF", "T", "nur Markdown"),
        ("a.md", b"x" * 1_000_001, "T", "größer als 1 MB"),
        ("a.md", "Grüße".encode("latin-1"), "T", "UTF-8"),
        ("a.md", b"   \n\n  ", "T", "keinen Text"),
        ("a.md", b"Text", "   ", "Pflicht"),
    ],
)
def test_invalid_uploads_are_rejected(manager, filename, data, title, message):
    with pytest.raises(SourceError, match=message):
        manager.add(filename, data, title, "CC BY 4.0")
    assert manager.entries() == []


def test_reindex_after_rebuild(manager, tmp_path):
    manager.add("woche3.md", MARKDOWN.encode(), "Skript Woche 3", "CC BY 4.0")
    # Neubau des Index wie bei `index build`: danach ist das Material zunächst weg
    collection = build_index([], manager.embedder, tmp_path / "index")
    assert collection.count() == 0
    rebuilt = SourceManager(collection, manager.embedder, manager.upload_dir)
    assert rebuilt.reindex_all() == 3
    assert collection.count() == 3


# --- über die API ------------------------------------------------------------------

BOOK = [
    {
        "id": "gruppierungen#001",
        "text": "Kapitel: Gruppierungen\nAbschnitt: HAVING\n\nHAVING filtert Gruppen",
        "metadata": {"chapter": "Gruppierungen", "section": "HAVING", "role": "content"},
    }
]


@pytest.fixture
def client(tmp_path):
    embedder = FakeEmbedder()
    build_index(BOOK, embedder, tmp_path / "index")

    def build_tutor():
        retriever = Retriever(open_index(tmp_path / "index", embedder.name), embedder, hybrid=True)
        return Tutor(retriever, FakeLLM(text="LAG liefert den Vorgänger [1]."), context_tokens=8192)

    app = create_app(build_tutor, upload_dir=tmp_path / "uploads", frontend_dir=tmp_path / "nichts")
    with TestClient(app) as client:
        yield client


MARKDOWN_BYTES = MARKDOWN.encode()


def upload(client, name="woche3.md", data=MARKDOWN_BYTES, title="Skript Woche 3"):
    return client.post(
        "/sources",
        files={"file": (name, data, "text/markdown")},
        data={"title": title, "license": "CC BY 4.0"},
    )


def test_uploaded_material_is_used_as_source(client):
    response = upload(client)
    assert response.status_code == 201
    source_id = response.json()["id"]

    answer = client.post("/ask", json={"question": "Was liefert LAG für die vorherige Zeile?"})
    first = answer.json()["sources"][0]
    assert first["chapter"] == "Skript Woche 3"
    assert first["license"] == "CC BY 4.0"
    assert first["url"] == ""

    info = client.get("/info").json()
    assert (info["book_chunks"], info["uploads"], info["upload_chunks"]) == (1, 1, 3)
    assert info["book_license"] == "CC BY-SA 4.0"

    assert client.delete(f"/sources/{source_id}").status_code == 204
    assert client.get("/sources").json() == []
    assert client.get("/info").json()["upload_chunks"] == 0


def test_upload_errors(client):
    assert upload(client, name="folien.pdf").status_code == 400
    assert "nur Markdown" in upload(client, name="folien.pdf").json()["detail"]
    assert upload(client, title="").status_code == 422  # Pflichtfeld leer: Prüfung durch FastAPI
    assert client.delete("/sources/gibt-es-nicht").status_code == 404


def test_book_sources_carry_the_book_license(client):
    first = client.post("/ask", json={"question": "Was macht HAVING?"}).json()["sources"][0]
    assert first["license"] == "CC BY-SA 4.0"


def test_frontend_is_served_when_built(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Query Angler</h1>", encoding="utf-8")

    def no_index():
        raise IndexUnavailableError("Kein Index.")  # Server startet trotzdem

    with TestClient(create_app(no_index, tmp_path / "uploads", dist)) as client:
        assert "Query Angler" in client.get("/").text
        assert client.get("/health").json()["tutor_ready"] is False  # API hat Vorrang
