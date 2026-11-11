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
- [x] Chunking: an der Gliederung des Buchs, Übungen und Lösungen getrennt, Negativbeispiele markiert
- [x] Embeddings (BGE-M3, lokal auf der CPU) und Vektorindex (Chroma)
- [x] Retrieval mit Filter für Musterlösungen
- [x] Generierung: API mit FastAPI, lokales Sprachmodell über Ollama, Antwort mit Quellen
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
uv run python -m queryangler.ingest chunk    # Chunks nach data/chunks/
uv run python -m queryangler.ingest chunks arbeiten-mit-join
```

## Index bauen und suchen

```bash
uv run python -m queryangler.index build     # lädt beim ersten Mal das Modell (ca. 2,3 GB)
uv run python -m queryangler.index search "Was unterscheidet WHERE von HAVING?"
uv run python -m queryangler.index search "Wie funktioniert ein LEFT JOIN?" --ohne-loesungen
```

Das Embedding-Modell ist auf eine Revision festgelegt (`QUERYANGLER_EMBED_REVISION`). Nur `build`
lädt es herunter, die Suche und der Server arbeiten offline. Für den Download ist kein
Hugging-Face-Konto nötig. Wer trotzdem ein Token verwendet, setzt es als Umgebungsvariable
(`export HF_TOKEN=...`), nicht als Argument eines Befehls, damit es nicht in der Shell-Historie landet.

## Tutor starten

Voraussetzung: [Ollama](https://ollama.com) läuft, der Index ist gebaut.

```bash
ollama pull ministral-3:8b
ollama create query-angler -f ollama/Modelfile     # im Hauptordner: Kontextfenster 8192 Token

cd backend
uv run uvicorn queryangler.api.app:app --reload
```

Unter <http://localhost:8000/docs> lässt sich die API direkt im Browser ausprobieren.

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "Was unterscheidet WHERE von HAVING?", "mode": "erklaeren"}'
curl -s localhost:8000/health
```

| Feld | Bedeutung |
|---|---|
| `mode` | `erklaeren` (vollständige Erklärung) oder `sokratisch` (nur Hinweise, Musterlösungen ausgeblendet) |
| `k` | Anzahl der Auszüge aus dem Buch, 1 bis 10 (Standard 5) |

Die Antwort enthält den Text, die Quellen mit Link und Ähnlichkeitswert (`cited`: in der Antwort
genannt), die Laufzeiten und die Zahl der Prompt-Token.

Einstellungen über Umgebungsvariablen, unter anderem `QUERYANGLER_LLM_BASE_URL`
(Standard `http://localhost:11434/v1`), `QUERYANGLER_LLM_MODEL` (Standard `query-angler`) und
`QUERYANGLER_LLM_CONTEXT_TOKENS` (Standard 8192, muss zum Modelfile passen). Jeder Dienst mit
OpenAI-kompatibler Schnittstelle funktioniert, ein Schlüssel kommt bei Bedarf in
`QUERYANGLER_LLM_API_KEY`.

## Entwicklung

```bash
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
