# Query Angler

**Zieht Wissen an Land.**

Ein quelloffener KI-Tutor für SQL, der Fragen ausschließlich auf Grundlage einer offenen
Wissensbasis beantwortet und seine Quellen nennt (Retrieval-Augmented Generation).
Ausgelegt für ein lokal laufendes Sprachmodell (Ollama), sodass keine Daten den Rechner verlassen.

Wissensbasis ist das Wikibook „Einführung in SQL“ (CC BY-SA), siehe [ATTRIBUTION.md](ATTRIBUTION.md).
Designentscheidungen und ihre Begründung: [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md).

> Projekt im Aufbau.

## Stand

- [x] Ingest: Kapitel über die MediaWiki-API laden, Revisionen festhalten, HTML bereinigen
- [ ] Chunking
- [ ] Embeddings und Vektorindex
- [ ] Retrieval
- [ ] Generierung (FastAPI, lokales LLM über Ollama)
- [ ] Self-Service-Oberfläche für Lehrende
- [ ] Evals, Docker Compose, CI

## Ingest ausführen

Voraussetzung: [uv](https://docs.astral.sh/uv/). Python 3.12 lädt uv bei Bedarf selbst.

```bash
cd backend
uv sync
export QUERYANGLER_CONTACT="https://github.com/<name>"   # Kontakt für den User-Agent

uv run python -m queryangler.ingest list     # Seiten des Buchs mit Auswahl anzeigen
uv run python -m queryangler.ingest fetch    # Rohdaten nach data/raw/ laden
uv run python -m queryangler.ingest clean    # bereinigte Textblöcke nach data/clean/
uv run python -m queryangler.ingest show einleitung
```

## Entwicklung

```bash
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
