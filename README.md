<img src="frontend/public/logo.png" width="96" alt="Logo von Query Angler: Angelhaken unter einer Wasserlinie">

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
- [x] Chunking: an der Gliederung des Buchs, Übungen und Lösungen getrennt, rot und grün markierte Kästen mit der Einstufung des Buchs
- [x] Embeddings (BGE-M3, lokal auf der CPU) und Vektorindex (Chroma)
- [x] Retrieval mit Filter für Musterlösungen
- [x] Generierung: API mit FastAPI, lokales Sprachmodell über Ollama, Antwort mit Quellen
- [x] Oberfläche: Fragen mit Schalter Erklären/Sokratisch, Wissensbasis mit Upload für Lehrende
- [x] Evals: Retrieval (Trefferquote, MRR) und Antworten (Absagen, sokratischer Modus, eigene Bewertung)
- [ ] Docker Compose, CI

Wie sich das für den Betrieb an einer Hochschule erweitern ließe (vLLM, mehrere Kopien, gemeinsamer Index, Moodle über LTI), steht in [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md#ausblick-betrieb-an-einer-hochschule).

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
uv run python -m queryangler.index models    # einmalig: Embedding-Modell und Reranker laden (ca. 4,5 GB)
uv run python -m queryangler.index build     # Chunks einbetten, Index aufbauen
uv run python -m queryangler.index search "Was unterscheidet WHERE von HAVING?"
uv run python -m queryangler.index search "Wie funktioniert ein LEFT JOIN?" --ohne-loesungen
uv run python -m queryangler.index search "Wie sortiere ich absteigend?" --mit-reranker
```

Standard ist die reine Vektorsuche. Zwei Erweiterungen sind eingebaut und gemessen, aber
standardmäßig aus, weil sie in der Eval nichts oder zu wenig brachten (siehe
[docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md#retrieval)): eine hybride Suche mit BM25
(`QUERYANGLER_HYBRID=1`, Fusion über `QUERYANGLER_FUSION`) und ein Reranker
(`QUERYANGLER_RERANK=1`, `BAAI/bge-reranker-v2-m3`, auf der CPU langsam).

Das Embedding-Modell ist auf eine Revision festgelegt (`QUERYANGLER_EMBED_REVISION`). Nur `models`
und `build` laden aus dem Netz, die Suche und der Server arbeiten offline (`HF_HUB_OFFLINE`). Für den Download ist kein
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
genannt), die Laufzeiten und die Zahl der Prompt-Token. Der Tutor prüft die Antwort des Modells,
bevor er sie ausliefert: Nach einer Absage wird nichts angehängt, und ein sokratischer Hinweis, der
die Lösung verrät, bekommt einen zweiten Versuch (`attempts`, `checks` in der Antwort).

Einstellungen über Umgebungsvariablen, unter anderem `QUERYANGLER_LLM_BASE_URL`
(Standard `http://localhost:11434/v1`), `QUERYANGLER_LLM_MODEL` (Standard `query-angler`) und
`QUERYANGLER_LLM_CONTEXT_TOKENS` (Standard 8192, muss zum Modelfile passen). Jeder Dienst mit
OpenAI-kompatibler Schnittstelle funktioniert, ein Schlüssel kommt bei Bedarf in
`QUERYANGLER_LLM_API_KEY`.

## Oberfläche

Die Oberfläche (Vue 3, `frontend/`) hat zwei Reiter:

- **Fragen:** Frage stellen, Schalter **Erklären | Sokratisch**, Antwort mit Formatierung und
  Quellen. Im sokratischen Modus heißen die Quellen „Zum Nachlesen“.
- **Wissensbasis** für Lehrende: was der Tutor weiß (Buch, Umfang, Modelle, ob Ollama läuft) und
  **eigenes Material hochladen**, als Markdown (.md) oder Text (.txt), mit Titel und Lizenz. Der
  Tutor nutzt es sofort als Quelle. Hochgeladenes lässt sich wieder entfernen und überlebt einen
  Neubau des Index.

Einmal bauen, dann liefert FastAPI die Oberfläche unter <http://localhost:8000/> mit aus:

```bash
cd frontend
npm install
npm run build        # schreibt nach frontend/dist/
```

Zum Entwickeln an der Oberfläche: `npm run dev` startet sie auf <http://localhost:5173> mit
automatischem Neuladen und leitet die API-Aufrufe an das Backend auf Port 8000 weiter.

Anmeldung und Rollen gibt es bewusst nicht. Im Betrieb käme die Rolle (Lernende oder Lehrende)
aus Moodle über LTI 1.3.

## Evals

Testfragen in [`evals/questions.toml`](evals/questions.toml), in vier Gruppen: im Buch, nah am
Thema aber nicht im Buch, themenfremd, sokratische Aufgaben. Ergebnisse in `evals/results/`.

```bash
cd backend
uv run python -m queryangler.evals retrieval                   # ohne Sprachmodell, Sekunden
uv run python -m queryangler.evals answers --label ministral   # mit Ollama, einige Minuten
QUERYANGLER_LLM_MODEL=query-angler-qwen3 uv run python -m queryangler.evals answers --label qwen3
uv run python -m queryangler.evals rate                        # neuesten Lauf bewerten (0–2)
uv run python -m queryangler.evals report                      # Vergleich aller Läufe
```

Ergebnis mit Ministral 3 8B (Einzelheiten und Bewertungsskala in
[docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md#evals)):

| | |
|---|---|
| Trefferquote@5 im Retrieval | 61 % |
| Richtigkeit der Antworten | 1,78 von 2 |
| Treue zur Quelle | 1,28 von 2 |
| Absage bei Fragen ohne Grundlage im Buch | 12 von 12 |
| Sokratischer Modus ohne verratene SQL-Begriffe | 10 von 10 |

## Entwicklung

```bash
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Herkunft

Das Logo wurde mit ChatGPT nach Vorgaben des Autors erzeugt.
