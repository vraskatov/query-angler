# Architektur und Designentscheidungen

## Überblick

Query Angler besteht aus zwei Pipelines:

```
Ingest (einmalig)                       Anfrage (pro Frage)
Wikibook über MediaWiki-API             Frage → Embedding → ähnlichste Abschnitte
→ bereinigen (HTML → Textblöcke)        → Prompt mit Abschnitten und Frage
→ Chunking mit Metadaten                → LLM → Antwort mit Quellenangaben
→ Embeddings → Vektorindex
```

Dieses Dokument beschreibt die umgesetzten Bausteine und wird mit jedem weiteren ergänzt.

## Projektstruktur

- **`backend/` und `frontend/`** in einem Repository. Die gebaute Oberfläche liefert FastAPI mit
  aus; dadurch braucht es nur einen Container für die Anwendung, dazu Ollama.
- **`src`-Layout:** Der Code liegt unter `backend/src/queryangler/`. Python kann das Paket dadurch
  nur als installiertes Paket importieren, nicht versehentlich aus dem Arbeitsverzeichnis. Die
  Tests prüfen also dasselbe, was später im Container läuft.
- **uv mit Lockdatei:** `pyproject.toml` beschreibt die direkten Abhängigkeiten, `uv.lock` pinnt
  alle Pakete samt Prüfsummen. Entwicklungsrechner, Container und CI verwenden dieselben Versionen.
  Python ist über `.python-version` auf 3.12 festgelegt.

## Ingest

### Quelle: MediaWiki-API statt Scraping

Die Kapitel werden über die API von de.wikibooks.org abgerufen, nicht aus den Webseiten gelesen.

- Die API liefert nur den Artikelinhalt, ohne Menüs, Seitenleiste und Bedienelemente.
- Sie liefert die benötigten Metadaten mit: Seiten-ID, Revisions-ID, Lizenz.
- Ihr Format ist dokumentiert. Ein Scraper hängt am Seitenlayout und bricht still, wenn es sich ändert.
- Abgerufen wird gerendertes HTML (`action=parse`), nicht Wikitext, weil darin alle Vorlagen
  bereits aufgelöst sind.

Der Client hält die Regeln für automatisierte Zugriffe ein: aussagekräftiger User-Agent mit
Kontaktangabe, eine Sekunde Abstand zwischen Anfragen, Wiederholung mit exponentiellem Backoff
bei Überlast (429, 5xx) unter Beachtung von `Retry-After`.

### Reproduzierbarkeit und Lizenz

Für jedes Kapitel wird die **Revisions-ID** gespeichert. Damit ist festgehalten, auf welchem
Textstand der Tutor antwortet, und die Quellenangabe verweist per Permanentlink auf genau diese
Fassung. Die Lizenz wird beim Abruf von der API abgefragt. `ATTRIBUTION.md` wird bei jedem Abruf
automatisch aus diesen Daten erzeugt und kann dadurch nicht veralten.

### Rohdaten und abgeleitete Daten

| Ordner | Inhalt | Versioniert |
|---|---|---|
| `data/raw/` | API-Antworten, unverändert | ja |
| `data/clean/` | bereinigte Textblöcke | nein |

Abgerufen wird einmal, bereinigt beliebig oft, ohne die Quelle erneut zu belasten. Jeder Schritt
überschreibt sein Ergebnis vollständig, es entstehen keine Altlasten.

### Seitenauswahl

Das Retrieval liefert pro Frage nur wenige Abschnitte. Jeder Abschnitt im Index, der keine
SQL-Frage beantwortet, kann einem passenden Abschnitt den Platz wegnehmen. In den Index kommen
deshalb nur Seiten mit Lehrinhalt: **38 von 68** Seiten mit dem Buchpräfix.

| Ausgeschlossen | Anzahl | Grund |
|---|---|---|
| `Druckversion: …` | 5 | fassen andere Kapitel zusammen, Inhalte stünden doppelt im Index |
| `Downloads`, `Downloads: …` | 13 | SQL-Skripte für sechs Datenbanksysteme, Code ohne Erklärung |
| Meta-Seiten | 12 | Titelei, Inhalts- und Literaturverzeichnis, Lizenz, Weblinks, Zielgruppe, Arbeitsseite der Autoren, Navigationsvorlage u. a. |

Bewusst enthalten sind die Seiten zur Beispieldatenbank, weil sich viele Beispiele des Buchs
auf deren Tabellen beziehen.

### Bereinigung: HTML → Textblöcke

Das HTML wird nicht zu einem einzigen Text zusammengezogen, sondern in typisierte Blöcke
überführt: Überschrift (mit Ebene), Absatz, Liste, Tabelle, Code (mit Sprache). Das Chunking kann
dadurch an Überschriften schneiden und Codeblöcke zusammenhalten.

- Entfernt werden Bearbeitungslinks, Navigationselemente, Bilder, Einzelnachweise und
  Zeilennummern in Codeblöcken sowie reine Navigations- und Verweisabsätze.
- Inline-Code bleibt als `` `SELECT` `` erhalten, damit das Sprachmodell Schlüsselwörter und
  Fließtext unterscheiden kann.
- Datentabellen werden zu Zeilen der Form `a | b`, Layout-Tabellen werden aufgelöst.
- Abschnitte „Siehe auch“ werden entfernt, sie enthalten nur Querverweise.
- `ingest clean` gibt eine Statistik aus, was wie oft erkannt und entfernt wurde.

### Didaktische Struktur des Buchs erhalten

Das Buch kennzeichnet Inhalte durch Gestaltung, die bei reinem Text verloren ginge. Die
Bereinigung überführt sie in Felder der Blöcke:

| Im Buch | Im Block | Zweck |
|---|---|---|
| roter Beispielkasten (20×) | Code mit `verdict: "negativ"` | Der Tutor darf solchen Code nicht ohne Vorbehalt als Lösung anbieten. |
| grüner Beispielkasten (12×) | Code mit `verdict: "positiv"` | Die bessere Variante eines Vergleichs ist als solche erkennbar |
| Kastentitel, z. B. „Firebird-Version“ | `title` am Codeblock | Dialekt und Zweck eines Beispiels bleiben erhalten |
| Bereich „Ausgabe“ im Kasten | eigener Blocktyp `output` | Abfrage und Ergebnis werden nicht verwechselt |
| Übungs- und Lösungskästen (je 151×) | `role: "exercise"` bzw. `"solution"` mit Nummer | Lösungen lassen sich ausblenden, wenn der Tutor nur Hinweise geben soll. Übungscode, der absichtlich Fehler enthält, ist als solcher erkennbar. |
| nachgebaute Überschriften (69×) | echte Überschriften | Das Chunking kann an ihnen schneiden. |
| Hinweiskästen | Absatz „Hinweis: …“ | statt einer einzeiligen Tabelle |

## Chunking

Ein Chunk ist zugleich die Einheit der Suche, der Kontext für das Sprachmodell und die Einheit
der Quellenangabe. Er muss groß genug sein, um seinen Zusammenhang mitzubringen, und klein genug
für ein trennscharfes Embedding und einen knappen Prompt.

### Regeln

Geschnitten wird an der Gliederung des Buchs, nicht nach fester Zeichenzahl:

1. **Abschnitte:** An jeder Überschrift beginnt ein Abschnitt. Er kennt seinen Pfad, etwa
   `Arbeiten mit JOIN > WHERE-Klausel bei JOINs`.
2. **Zusammenfassen:** Kleine Unterabschnitte (unter 400 Zeichen) werden mit dem vorigen
   zusammengefasst, solange sie im selben Hauptabschnitt liegen und das Ergebnis höchstens 1500
   Zeichen hat. Ihre Überschriften bleiben im Text sichtbar.
3. **Teilen:** Größere Abschnitte werden an Blockgrenzen geteilt. Dabei bleiben zusammen:
   ein Codeblock mit dem Absatz davor und seiner Ausgabe, sowie ein Absatz, der mit einem
   Doppelpunkt endet, mit dem, was er ankündigt. Ein Codeblock wird nie zerschnitten.
4. **Übungen und Lösungen** bilden je eigene Chunks und tragen `role` und `exercise` in den
   Metadaten. Lösungen lassen sich so ausblenden, wenn der Tutor nur Hinweise geben soll.
5. **Kopf:** Jeder Chunk beginnt mit Kapitel und Abschnittspfad. Ein Abschnitt wie „Beispiel“
   ist für sich genommen nicht einzuordnen, mit Pfad schon. Das hilft dem Embedding und dem
   Sprachmodell.
6. **Rot und grün markierte Kästen** stehen im Chunk-Text als „Rot markiert im Buch:“ bzw.
   „Grün markiert im Buch:“, direkt gefolgt von der Einstufung des Buchs selbst (dem Titel des
   Kastens). Das Modell sieht nur Text, die Farbe muss deshalb ausgeschrieben werden.

   Erste Fassung: „Negativbeispiel (fehlerhaft oder nicht empfohlen)“. Die Bewertung der
   Antworten zeigte, dass das zu grob war: Das Modell nannte eine zulässige, nur ungünstige
   Abfrage („Möglich, aber nicht günstig“) „fehlerhaft“. Eine Durchsicht aller 20 roten Kästen
   ergab: 8 heißen „Fehler“, 3 „möglich, aber ungünstig“ oder ähnlich, 5 nur „Oracle-Version“
   oder „Firebird-Version“, 4 Sonstiges. Rot bedeutet im Buch also „mit Vorbehalt“, nicht
   „falsch“. Seitdem ist das Etikett neutral, und Regel 3 im Prompt verlangt, die Einstufung des
   Buchs unverändert wiederzugeben.

Auf Überlappung zwischen Chunks wird verzichtet: Es wird nur an Blockgrenzen geschnitten, und die
Regeln unter 3. halten zusammen, was sich aufeinander bezieht.

Allgemeine Text-Splitter aus Frameworks wie LangChain wurden nicht verwendet. Sie kennen weder
Übungsrollen noch Negativbeispiele und können mitten in einem Codeblock schneiden.

### Ergebnis

896 Chunks aus 38 Kapiteln: 591 Lehrtext, 153 Übungen, 152 Lösungen. Median 689 Zeichen.
19 Chunks enthalten ein Negativbeispiel.

Gespeichert werden sie als JSON Lines (`data/chunks/chunks.jsonl`), ein Chunk pro Zeile mit `id`,
`text` und `metadata` (Kapitel, Abschnitt, Rolle, Übungsnummer, Negativbeispiel ja/nein, URL,
Permanentlink, Revision, Länge).

## Embeddings und Index

### Embedding-Modell: BGE-M3

| Kriterium | BGE-M3 |
|---|---|
| Deutsch | stärkstes Modell dieser Größenklasse im deutschen Retrieval (MIRACL) |
| Eingabelänge | 8.192 Token, deckt auch die größten Chunks vollständig ab |
| Lizenz | MIT |
| Größe | 568 Mio. Parameter, läuft auf der CPU |

Verglichen wurden Qwen3-Embedding-0.6B (etwas schwächer im Deutschen) und EmbeddingGemma-300m
(Gemma-Lizenz, keine anerkannte Open-Source-Lizenz). Modelle mit 512 Token Eingabelänge, etwa
multilingual-e5, hätten die größten Chunks abgeschnitten. Die größeren Qwen3-Modelle bräuchten den
Grafikspeicher, den das Sprachmodell belegt. Ob die Wahl für dieses Material die beste ist, prüfen
die Evals.

Das Modell läuft auf der CPU, damit die GPU dem Sprachmodell bleibt. PyTorch wird deshalb als
reine CPU-Version installiert (`[tool.uv.sources]` in `pyproject.toml`), was mehrere Gigabyte an
CUDA-Bibliotheken spart. Die Vektoren werden auf Länge 1 normiert.

Das Modell steckt hinter dem Protokoll `Embedder`. In den Tests ersetzt es ein einfaches
Wortzählmodell, sodass nichts heruntergeladen werden muss.

### Index: Chroma

- dauerhaft in `data/index/`, Ähnlichkeit über Kosinus, bei jedem `build` komplett neu aufgebaut
- **Embeddings werden selbst berechnet und übergeben** (`embedding_function=None`). Chroma bringt
  ein eigenes Standardmodell mit, ein kleines englisches Modell. Für deutschen Text würde es still
  schlechtere Ergebnisse liefern.
- **Modellname und Revision werden im Index gespeichert** und beim Öffnen geprüft. Wer mit einem
  anderen Modell sucht als dem, mit dem der Index gebaut wurde, bekäme sinnlose Treffer ohne
  Fehlermeldung.
- **Das Modell ist auf eine Revision festgelegt** (Commit auf Hugging Face). Ändert jemand das
  Modell im Repository, bleibt der Index trotzdem gültig. Suche und Server laden das Modell nur aus
  dem lokalen Zwischenspeicher (`local_files_only`), ohne Netzzugriff.
- **Telemetrie abgeschaltet:** Chroma sendet standardmäßig anonyme Nutzungsstatistiken.

Für einen produktiven Betrieb wäre PostgreSQL mit pgvector eine Alternative: ein Dienst weniger,
wenn ohnehin eine Datenbank läuft, und Ähnlichkeitssuche kombinierbar mit normalem SQL.

### Retrieval

`Retriever.search(frage, k=5, include_solutions=True)` liefert die k ähnlichsten Chunks über die
Vektorsuche (Kosinus-Ähnlichkeit). Übungsstellungen werden nie gefunden, nur Lehrtext und, beim
Erklären, Musterlösungen. Mit `include_solutions=False` fallen auch die Musterlösungen weg, die
Grundlage für den sokratischen Modus.

**Gemessen** mit 28 Fragen, deren passender Abschnitt bekannt ist (`evals/questions.toml`):

| Variante | Trefferquote@1 | @3 | @5 | @8 | MRR | Zeit pro Frage |
|---|---|---|---|---|---|---|
| Vektorsuche mit Übungsstellungen | 29 % | 46 % | 57 % | 71 % | 0,42 | < 0,1 s |
| **Vektorsuche ohne Übungsstellungen (Standard)** | 25 % | 50 % | **61 %** | 71 % | 0,42 | < 0,1 s |
| + Reranker (`bge-reranker-v2-m3`) | 39 % | 57 % | 64 % | 71 % | 0,51 | etwa 8 s |
| Hybrid mit BM25, Reciprocal Rank Fusion | 39 % | 46 % | 50 % | 68 % | 0,48 | < 0,1 s |
| Hybrid, gewichtete Summe 0,7 / 0,3 | 36 % | 43 % | 50 % | 75 % | 0,45 | < 0,1 s |
| Hybrid, gewichtete Summe 0,5 / 0,5 | 39 % | 46 % | 57 % | 71 % | 0,48 | < 0,1 s |
| Hybrid, Reißverschluss | 25 % | 54 % | 61 % | 64 % | 0,41 | < 0,1 s |

Ins Prompt kommen fünf Auszüge, deshalb zählt die Trefferquote@5.

- **Übungsstellungen ausgeschlossen:** Sie ähneln Fragen von Lernenden stark („Geben Sie alle
  Abteilungen aus …“), enthalten aber kein Wissen. Ohne sie +4 Prozentpunkte, ohne Kosten.
- **Reranker:** Ein Cross-Encoder, der Frage und Chunk gemeinsam liest. Er verbessert vor allem
  Platz 1, bei @5 aber nur um 3 Prozentpunkte, für 8 Sekunden pro Frage auf der CPU. Eingebaut,
  standardmäßig aus (`QUERYANGLER_RERANK=1`).
- **Hybride Suche:** Die Stichwortsuche (BM25, selbst implementiert, mit deutschem Wortstamm)
  ist allein etwa so gut wie die Vektorsuche, scheitert aber an anderen Fragen. Bei „Wie sortiere
  ich das Ergebnis absteigend?“ findet sie den Abschnitt auf Platz 1, die Vektorsuche auf
  Platz 19. Keines der drei Fusionsverfahren schlägt trotzdem die Vektorsuche bei @5.
  Reciprocal Rank Fusion schadet sogar: Sie bevorzugt Chunks, die in beiden Listen im Mittelfeld
  stehen, und das sind hier oft allgemeine Abschnitte. Eingebaut, standardmäßig aus
  (`QUERYANGLER_HYBRID=1`, Verfahren über `QUERYANGLER_FUSION`).
- **Grenze der Messung:** Bei 28 Fragen ist eine Frage 3,6 Prozentpunkte. Unterschiede von ein
  oder zwei Fragen sind Rauschen. Ohne klaren Gewinn bleibt das einfachere System.
- **Ungelöst:** Umgangssprachliche Fragen ohne die Begriffe des Buchs („keinen Wert“ statt NULL,
  „beginnt mit“ statt LIKE) findet keine Variante. Ein möglicher nächster Schritt wäre, die Frage
  vor der Suche vom Sprachmodell in Fachbegriffe umschreiben zu lassen (Query Rewriting), auf
  Kosten einer zusätzlichen Anfrage an das Modell.

Ähnlichkeit ist nicht dasselbe wie Beantwortbarkeit. Die Frage nach `JSON_VALUE`, die das Buch
nicht behandelt, erreichte 0,609, mehr als die passende Frage nach WHERE und HAVING (0,556). Eine
Schwelle kann deshalb nur klar themenfremde Fragen abfangen (Apfelkuchen: 0,392). Lücken innerhalb
des Themas muss der Prompt abfangen.

Die Eval bestätigt das: Der beste Vektortreffer lag bei Fragen aus dem Buch zwischen 0,492 und
0,705, bei themenfremden Fragen bis 0,510, bei Fragen „nah dran“ bis 0,632. Die Gruppen überlappen,
eine Schwelle auf der Vektorähnlichkeit ist damit ausgeschlossen. Die Werte des Rerankers trennen
deutlich besser (nah dran höchstens 0,065, themenfremd höchstens 0,001, Buchfragen im Median
0,64), aber auch einige echte Fragen lagen unter 0,07. Auch dort wäre eine Schwelle unsicher.

## Generierung

### Sprachmodell: lokal über Ollama

Standard ist `ministral-3:8b` (Apache 2.0). In Vergleichstests mit denselben Fragen war es
inhaltlich genauer als `qwen3:8b` und formulierte besseres Deutsch, bei gleicher Geschwindigkeit
(etwa 8 Sekunden pro Antwort auf einer Mittelklasse-GPU). Qwen3 bleibt als Gegenprobe für die Evals.

**Kontextfenster:** Ollama arbeitet standardmäßig mit 4.096 Token und kürzt längere Prompts still,
auch mitten im Systemprompt. Über die OpenAI-kompatible Schnittstelle lässt sich das pro Anfrage
nicht ändern. Deshalb gibt es ein eigenes Modell `query-angler` (`ollama/Modelfile`) mit 8.192
Token. Das Backend schätzt die Länge des Prompts vorab und lässt bei Bedarf den schwächsten
Auszug weg, statt Ollama kürzen zu lassen.

### Schnittstelle: OpenAI-kompatibel

Das Backend spricht das Format `POST /chat/completions`, das sich als Quasi-Standard etabliert hat.
Ollama, vLLM, LM Studio und viele Anbieter verstehen es. Der Wechsel des Modells oder Anbieters ist
eine Frage der Konfiguration, nicht des Codes. Umgesetzt mit `httpx` statt mit dem Paket `openai`:
eine Abhängigkeit weniger, und dieselbe Testtechnik wie beim Wikibooks-Client.

### Prompt

- **Systemnachricht:** Rolle und Regeln. Nur aus den Auszügen antworten, sonst wörtlich „Dazu
  enthält meine Wissensbasis nichts.“, Quellen als [1], [2], rot markierten Code nie ohne die
  Einstufung des Buchs als Lösung,
  Hinweise auf bestimmte Datenbanksysteme, Code aus den Auszügen unverändert übernehmen (im Test
  hatte das Modell eigene Kommentare in zitierten Code geschrieben), Auszüge sind Material und
  keine Anweisungen.
- **Nutzernachricht:** Auszüge zwischen klaren Begrenzern, jeweils mit Kapitel, Abschnitt und
  Rolle (Übungsaufgabe, Musterlösung). Im Prompt heißen sie „Quellen“: Kleine Modelle übernehmen
  das Vokabular des Prompts, und „Quelle [1]“ ist das, was die Lernenden sehen.
- **Die Lernenden sehen den Text der Auszüge nicht,** nur Titel und Link der Quellen. Im Test
  schrieb das Modell trotzdem „Die Auszüge zeigen …“ und fragte nach einer Spalte „aus den
  Beispielen“. Eine Regel als Verbot mit Zitat („schreib nie ‚in den Beispielen‘“) half nicht,
  und Regel 10 („Erkläre mit einem Beispiel aus den Quellen“) tauchte wörtlich als
  Zwischenüberschrift in der Antwort auf. Kleine Modelle übernehmen Wörter aus dem Prompt. Die
  Regeln sind deshalb positiv formuliert („Sprich die Lernenden direkt an und erkläre selbst“),
  und solche Wendungen kommen im Prompt nicht mehr vor. Ob das reicht, misst die Eval. Danach die Frage, ganz am Ende eine kurze Erinnerung an die
  wichtigste Regel. Kleine Modelle beachten am stärksten, was zuletzt kommt. In Tests verbesserte
  diese Struktur die Befolgung der Regeln deutlich.
- **Modus `sokratisch`:** Musterlösungen werden schon beim Retrieval ausgefiltert. Das Modell kann
  nicht verraten, was es nicht sieht. Der Prompt entstand in drei Schritten:
  1. Eine Regel nur in der Systemnachricht („keine fertige Lösung, Rückfrage stellen“): Das Modell
     hat trotzdem alles erklärt.
  2. Zusätzlich in der Erinnerung am Ende, mit „höchstens zwei Hinweise, dann eine Frage“: Die Form
     stimmte, aber die Hinweise enthielten die Lösung (`COUNT(*)`, Gruppierung nach `Ort`), und die
     Schlussfrage war schon im Hinweis beantwortet. Die Regel begrenzte die Anzahl, nicht den Inhalt.
  3. Verbotene Inhalte ausdrücklich benannt (Code, Schlüsselwörter, Spaltennamen der Lösung,
     Quellenverweise), feste Form `Hinweis:` / `Frage:`, ein schlechtes und ein gutes Beispiel zu
     einem anderen Thema. Kleine Modelle folgen festen Formen und Beispielen zuverlässiger als
     abstrakten Regeln. Ergebnis: Form eingehalten, keine Quellenverweise, Absage bei
     `JSON_VALUE` funktioniert weiterhin. Einzelne Schlüsselwörter (`COUNT`) rutschen noch durch.

  Weitere Runden am Prompt bringen bei einem 8B-Modell wenig. Die Antwort-Eval bestätigte das:
  Nur 6 von 10 sokratischen Antworten waren ohne SQL-Begriffe, nur 4 von 10 ohne Verweis auf
  „Quellen“ oder „Beispiele“. Deshalb prüft seitdem der Code die Ausgabe (nächster Abschnitt).
  Bei Begriffsfragen („Was unterscheidet …?“) ist jeder Hinweis schon die halbe Antwort; der
  Modus passt eher zu Aufgaben („Wie schreibe ich …?“).

  Nachtrag aus dem Ausprobieren: Auf „Was ist das Leben?“ kam im sokratischen Modus ein Hinweis
  auf Trigger statt einer Absage. Die feste Form „Hinweis/Frage“ drückte gegen die Absage, und
  die Rückmeldung für den zweiten Versuch bot gar keine Absage an. Die Evals hatten Absagen nur im
  Modus Erklären geprüft. Seitdem: ein Muster für die Absage im sokratischen Prompt, ein Ausweg in
  der Rückmeldung, eine Absage im zweiten Versuch gilt, und vier Testfragen ohne Grundlage laufen
  auch im sokratischen Modus.
- **Modus `erklaeren`:** Auf „Hallo Welt?“ schrieb das Modell einen Kurzlehrgang. Es deutete die
  Frage um („ich nehme an, du meinst …“), erfand eine Tabelle, hängte „nächste Schritte“ an und
  bot eine Fortsetzung an, obwohl die API keinen Verlauf kennt (38 s). Abgesagt hat es nicht,
  weil „Hallo“ im Buch tatsächlich etwas findet: die Funktion `Hello`. Die alte Regel „Zeige ein
  passendes SQL-Beispiel“ zwang es außerdem zum Erfinden, wenn die Quellen keins enthielten.
  Seitdem: zuerst ein Satz mit der direkten Antwort, bei einem Gruß nur dieser Satz; alles
  Weitere aus den Quellen, ohne eigene Tipps oder nächste Schritte; Beispiele aus den Quellen,
  eigene als „Eigenes Beispiel:“ gekennzeichnet; keine Fortsetzung, keine Emojis. Eine feste
  Längengrenze wurde verworfen: Sie träfe auch gute Antworten, und kleine Modelle halten
  Wortgrenzen ungenau ein. Die Länge soll sich aus der Bindung an die Quellen ergeben.
- **Temperatur 0,2:** sachlich statt kreativ.

Die Antwort nennt, welche Quellen das Modell tatsächlich zitiert hat (`cited`). Daran lässt sich
in den Evals messen, ob Antworten belegt sind.

### Ausgabe prüfen

Regeln im Prompt reichen bei einem 8B-Modell nicht. `guard.py` prüft deshalb die Antwort im Code,
mit denselben Funktionen, die auch die Evals verwenden:

- **Absage:** In der Eval begann das Modell bei allen 12 Fragen ohne Grundlage richtig mit
  „Dazu enthält meine Wissensbasis nichts.“, hängte aber in 11 Fällen etwas an, teils mit eigenem
  Wissen (die Namen der Isolationsstufen, in welchen Systemen es `STRING_AGG` gibt). Beginnt eine
  Antwort mit dem Absagesatz, liefert der Tutor deshalb nur diesen Satz aus. Der hilfreiche
  Hinweis „das Buch behandelt reguläre Views“ fällt damit auch weg; eine kurze, verlässliche
  Absage ist für Lernende wichtiger.
- **Sokratischer Modus:** Enthält ein Hinweis SQL-Begriffe, die nicht schon in der Frage stehen,
  SQL-Code oder Verweise auf „Quellen“ und „Beispiele“, bekommt das Modell genau einen zweiten
  Versuch, mit der konkreten Begründung („sie nennt die SQL-Begriffe COUNT, GROUP BY“).
  Ausgeliefert wird die Antwort mit weniger Problemen. Bleiben welche, steht das im Feld `checks`
  der Antwort. Die Form allein (etwa ein Satz nach der Frage) löst keinen zweiten Versuch aus.
- **Kosten:** Ein zweiter Versuch dauert einige Sekunden. Wie oft er nötig ist, misst die Eval
  („Zweiter Versuch nötig“).

### API

- **FastAPI**, Modell und Index werden einmal beim Start geladen (`lifespan`), nicht pro Anfrage.
- **Asynchron:** Die Anfrage an das Sprachmodell wartet, ohne den Server zu blockieren. Die Suche
  rechnet auf der CPU und läuft deshalb in einem eigenen Thread (`asyncio.to_thread`).
- **Fehler mit passenden Statuscodes:** ungültige Eingabe 422, Sprachmodell nicht erreichbar oder
  Index fehlt 503, Zeitüberschreitung 504, Fehler des Sprachmodells 502.
- **Aufwärmen beim Start:** Ollama lädt ein Modell erst bei der ersten Anfrage in den
  Grafikspeicher und entlädt es nach einigen Minuten ohne Anfrage wieder. Die erste Frage dauerte
  dadurch spürbar länger. Der Server schickt deshalb beim Start im Hintergrund eine winzige Anfrage
  (`warm_up`), ohne den Start zu blockieren; die Oberfläche weist bei längerer Wartezeit darauf
  hin. Gegen das Entladen hilft `OLLAMA_KEEP_ALIVE` beim Ollama-Dienst. Im Betrieb an einer
  Hochschule liefe das Modell ohnehin dauerhaft auf einem Inferenzserver; dank des
  OpenAI-kompatiblen Formats ändert sich dafür nur die Adresse.
- **Start auch ohne Index:** `/health` nennt dann den Grund, `/ask` antwortet mit 503. Das hilft
  später in Docker Compose, wo Dienste in beliebiger Reihenfolge starten.
- **Dependency Injection:** `create_app(build_tutor)` nimmt die Funktion entgegen, die den Tutor
  baut. Die Tests übergeben einen Tutor mit Ersatzteilen.

## Oberfläche und eigenes Material

### Oberfläche

- **Vue 3 mit Vite, schlichtes JavaScript,** ohne Router und ohne UI-Bibliothek. Zwei Ansichten,
  umgeschaltet über Reiter; `KeepAlive` behält den Zustand beim Wechseln.
- **Schalter Erklären | Sokratisch** mit beiden Wörtern sichtbar. Ein An-aus-Schalter wäre
  missverständlich, weil man nicht sieht, was „aus“ bedeutet.
- **Hell und dunkel:** Standard ist die Einstellung des Betriebssystems (`prefers-color-scheme`),
  ein Knopf in der Kopfzeile überschreibt sie und merkt sich die Wahl im Browser. Ein kleines
  Skript in `index.html` setzt sie vor dem ersten Zeichnen, damit die Seite nicht aufblitzt.
  Farben: hell mit Pastellgelb und Anthrazit, dunkel umgekehrt, Akzente in beiden Orange.
- **Antworten als Markdown** (`marked`), bereinigt mit `DOMPurify`. Das Sprachmodell erzeugt Text,
  der als HTML in die Seite kommt; ohne Bereinigung könnte eine Antwort Skripte einschleusen.
- **Ehrlich zu den Grenzen:** Unter dem Eingabefeld steht, dass jede Frage einzeln beantwortet
  wird. Hat der Tutor abgesagt oder einen zweiten Versuch gebraucht, steht das unter der Antwort.
- **Auslieferung durch FastAPI** (`StaticFiles`), eingehängt nach allen API-Routen, damit `/ask`
  oder `/docs` Vorrang haben. Ein eigener Container mit nginx wäre in großen Projekten üblich, hier
  aber ein Dienst mehr ohne Nutzen. Beim Entwickeln leitet Vite die API-Aufrufe weiter, so braucht
  es kein CORS.

### Eigenes Material (Self-Service für Lehrende)

- **Formate:** Markdown und reiner Text, UTF-8, höchstens 1 MB. Markdown wird wie das Buch an den
  Überschriften zerlegt (Überschriften in Codeblöcken zählen nicht), reiner Text an Leerzeilen;
  zu lange Abschnitte an Leerzeilen geteilt, nie innerhalb eines Codeblocks. PDF und Word sind
  bewusst nicht dabei: Text aus PDFs ist fehleranfällig (Spalten, Kopfzeilen, Silbentrennung).
- **Pflichtangaben:** Titel und Lizenz. Der Tutor nennt das Material als Quelle mit genau diesen
  Angaben, so wie das Buch mit CC BY-SA 4.0.
- **Speicherung:** Die Chunks kommen in denselben Chroma-Index, mit `source="upload"` und einer
  `source_id` in den Metadaten. Darüber lassen sie sich gezielt löschen. Originaldatei und Liste
  der Quellen liegen in `data/uploads/` (nicht im Repo). `index build` nimmt sie nach dem Neubau
  wieder auf, sonst ginge das Material verloren.
- **API:** `GET /info`, `GET /sources`, `POST /sources` (Formular mit Datei, Titel, Lizenz),
  `DELETE /sources/{id}`. Das Einbetten läuft in einem eigenen Thread, wie die Suche.
- **Bewusst nicht:** Anmeldung und Rollen. Im Betrieb käme das über Moodle (LTI 1.3), ebenso
  getrennte Wissensbasen je Kurs. Das hochgeladene Material landet bisher in derselben
  Wissensbasis wie das Buch.

## Evals

Tests prüfen, ob der Code tut, was er soll. Evals messen, wie gut das System antwortet. Das
Sprachmodell ist nicht deterministisch, deshalb laufen die Evals getrennt von den Tests und mit
dem echten Modell.

**Testfragen** (`evals/questions.toml`), 44 Stück in vier Gruppen:

| Gruppe | Anzahl | Erwartung |
|---|---|---|
| `im_buch` | 18 | richtige Antwort mit Quelle; darunter Umformulierungen ohne die Fachbegriffe des Buchs |
| `nah_dran` | 10 | Absage; SQL-Themen, die das Buch nicht erklärt (geprüft gegen alle Chunks; MERGE und Isolationsstufen stehen nur in der Liste der Schlüsselwörter) |
| `fremd` | 6 | Absage |
| `sokratisch` | 10 | Hinweis und Frage ohne Lösung |

Vier der Absage-Fragen (zwei aus `nah_dran`, zwei aus `fremd`) laufen mit `mode = "sokratisch"`,
damit die Absage auch in diesem Modus geprüft wird.

Das erwartete Retrieval-Ziel ist ein Abschnitt („Kapitel > Abschnitt“), keine Chunk-ID. So bleiben
die Fragen gültig, wenn sich das Chunking ändert.

**Messgrößen**

- Retrieval: Trefferquote@k (Anteil der Fragen mit einem passenden Abschnitt unter den ersten k
  Treffern) und MRR (Mittelwert von 1/Rang). Dazu der höchste Ähnlichkeitswert je Gruppe als
  Grundlage für eine mögliche Schwelle.
- Antworten, automatisch: Quelle zitiert, Pflichtbegriffe vorhanden, Absage (Antwort beginnt mit
  dem Absagesatz) und ob das Modell etwas angehängt hat, im sokratischen Modus Form, Leck
  (SQL-Begriffe in Großbuchstaben, die nicht schon in der Frage stehen, oder ein Codeblock),
  Verweise auf Quellen, die die Lernenden nicht sehen, und wie oft ein zweiter Versuch nötig war.
  Der Bericht berechnet diese Prüfungen aus dem Antworttext neu, sodass alte und neue Läufe nach
  denselben Regeln verglichen werden.
- Erste Fassung der Absage-Prüfung: Sie verlangte den Absagesatz allein und zählte deshalb 11 von
  12 richtigen Absagen als Fehler. Ein Fehler der Messung, nicht des Tutors; erst der Blick in die
  Antworten zeigte das.
- Antworten, bewertet: Richtigkeit und Treue zur Quelle, je 0 bis 2, im Terminal mit den
  Quelltexten daneben (`evals rate`). Die Quelltexte speichert der Lauf mit, denn Chunk-Nummern
  können sich beim Neubau des Index verschieben. Das ist einmal passiert: Eine Bewertung zeigte zu
  einer alten Antwort plötzlich einen anderen Abschnitt.

**Bewertungsskala**

| | Richtigkeit (fachlich, egal was im Buch steht) | Treue (steht es in den Quellen?) |
|---|---|---|
| 2 | richtig und beantwortet die Frage | Kernaussage steht in den Quellen, alle Verweise stimmen; eigene Beispiele sind als solche erkennbar |
| 1 | im Kern richtig, aber mit Fehler, schiefer Verallgemeinerung oder wichtiger Lücke | Kernaussage gedeckt, aber Details oder Verweise nicht; oder Kernaussage aus eigenem Wissen, aber nicht als Buchinhalt ausgegeben |
| 0 | falsch, irreführend oder an der Frage vorbei | Kernaussage aus eigenem Wissen und dem Buch zugeschrieben, oder Quelle verfälscht |

Die beiden Werte sind unabhängig: „Richtig, aber nicht treu“ heißt, das Modell wusste es selbst,
das Buch sagt es nicht. „Treu, aber nicht richtig“ hieße, das Buch ist an der Stelle ungenau.

**Wer bewertet hat:** 11 der 18 Antworten bewertete der Autor (SQL-Dozent), im Gespräch mit Claude
abgestimmt. Dabei entstand die Skala für die Treue. In den 8 Fällen, in denen der Autor zuerst
bewertete, stimmte die Richtigkeit 7-mal mit Claudes Vorschlag überein, die Treue nur 3-mal: Der
Autor hatte „ehrlich gekennzeichnetes eigenes Wissen“ zunächst als treu gewertet. Nach Festlegung
der Skala wurde das einheitlich zu 1. Die übrigen 7 Antworten bewertete Claude nach derselben
Skala. Ein 8B-Modell als Richter über ein 8B-Modell wäre nicht glaubwürdig, ein deutlich größeres
Modell mit fester Skala und Stichproben durch einen Menschen ist eine übliche Methode
(LLM-as-a-Judge). Der Bericht weist für jeden Wert aus, wie viele Bewertungen von wem stammen.

**Ergebnis** (Ministral 3 8B, Vektorsuche, Prüfung der Ausgabe im Code, Lauf vom 12.11.2026):

| Kennzahl | Wert |
|---|---|
| Richtigkeit, Fragen im Buch | 1,78 von 2 |
| Treue zur Quelle | 1,28 von 2 |
| … wenn der passende Abschnitt unter den Quellen war (13 Fragen) | 1,38 |
| … wenn nicht (5 Fragen) | 1,00 |
| Absage bei Fragen ohne Grundlage | 12 von 12 (Lauf vom 13.11. mit 16 Fragen: 16 von 16, im sokratischen Modus 4 von 4) |
| Sokratisch ohne SQL-Begriffe | 10 von 10 (vor der Prüfung im Code: 6 von 10) |
| Sokratisch ohne Verweis auf Quellen | 3 von 10 (unverändert) |
| Median Antwortzeit LLM: Erklären / Sokratisch / Absage | 13,1 / 7,8 / 5,4 s |

Lesart:
- Das Modell antwortet fachlich fast immer richtig, oft aber mit eigenem Wissen statt aus dem Buch.
  Fand das Retrieval den passenden Abschnitt nicht, sinkt die Treue spürbar. Die Schwäche liegt
  damit vor allem beim Retrieval, nicht beim Sprachmodell.
- Der zweite Versuch im sokratischen Modus beseitigt verratene SQL-Begriffe zuverlässig, aber
  nicht die Verweise auf „die Beispiele“. Das Modell beschreibt seine Quellen offenbar so
  hartnäckig, dass eine Begründung allein nicht reicht. Er kostet im Median gut 3 Sekunden.

**Grenzen:** 44 Fragen ergeben Tendenzen, keine statistisch belastbaren Unterschiede; die Treue-
Werte nach Retrieval-Treffer beruhen auf 13 und 5 Antworten. Die Leck-Prüfung erkennt
SQL-Begriffe, aber keine verratenen Tabellen- oder Spaltennamen.

## Testbarkeit

Logik und Ein-/Ausgabe sind getrennt. Bereinigung und Attribution sind reine Funktionen und werden
mit festen Beispieldaten getestet. Der API-Client erhält seine Netzwerkverbindung von außen
(Parameter `transport`). In den Tests ersetzt `httpx.MockTransport` die echte API. Alle Tests
laufen dadurch ohne Netzwerk, auch in der CI.

Für Index, Tutor und API ersetzen zwei Ersatzteile die großen Modelle (`tests/fakes.py`): ein
Embedder, der Wörter zählt, und ein Sprachmodell mit fester Antwort, das sich den erhaltenen Prompt
merkt. Der Chroma-Index dagegen ist echt. So prüfen die Tests den ganzen Weg von der Anfrage bis
zur Antwort, ohne Modell und ohne Ollama.

## Bekannte Grenzen

- Die Wissensbasis entspricht inhaltlich dem Stand von SQL:2008 und älteren Datenbankversionen.
  Neuere Funktionen (z. B. JSON-Funktionen) deckt sie nicht ab. Der Tutor soll in solchen Fällen
  sagen, dass die Wissensbasis dazu nichts enthält.
- In drei Tabellen des Kapitels „DDL – Einzelheiten“ steht Code in den Zellen. Er verliert bei
  der Umwandlung in Tabellenzeilen seine Zeilenumbrüche.
- 11 Chunks sind deutlich länger als 1500 Zeichen, weil sie aus einem einzelnen großen Block
  bestehen (lange Prozeduren, eine Tabelle aller Schlüsselwörter). Sie werden nicht zerschnitten.
  BGE-M3 verarbeitet sie vollständig. Der Chunk mit der Schlüsselwortliste taucht allerdings bei
  vielen fremden Fragen unter den Treffern auf. Die Evals zeigen, ob er ausgeschlossen werden sollte.
- Die Token-Schätzung für den Prompt ist grob (Zeichen geteilt durch 3). Gemessen sind es etwa
  3,9 Zeichen pro Token, die Schätzung liegt also rund 30 % zu hoch. Das ist gewollt: Lieber fällt
  ein Auszug zu viel weg, als dass Ollama den Prompt still kürzt.
- Der sokratische Modus ist ein Hinweismodus: Die API kennt keinen Gesprächsverlauf, die Frage
  am Ende des Hinweises läuft deshalb ins Leere. Echtes sokratisches Lernen braucht den Dialog
  (Tutor fragt, Lernende antworten, Tutor reagiert). Der nächste Schritt wäre ein Feld `history`
  mit den bisherigen Nachrichten.
- Typische Schwächen des 8B-Modells, die die Bewertung zeigte und die bewusst nicht weiter
  bearbeitet wurden: eigene Beispiele werden als „aus der Quelle“ ausgegeben; Code aus dem Buch
  wird umbenannt oder mit Kommentaren versehen, obwohl Regel 4 das verbietet; Plaudern („Gute
  Frage!“); „Frag gerne nach!“, obwohl die API keinen Gesprächsverlauf kennt. Einmal stand ein
  erfundener Halbsatz in einem wörtlichen Zitat.
- Bei einem Hinweiskasten des Buchs ging die Überschrift verloren („: Dies ist ein
  Paradebeispiel dafür, wie Unterabfragen nicht benutzt werden sollen.“). Das Modell nutzte genau
  dieses abschreckende Beispiel, um ORDER BY zu erklären.
- Mit fünf Auszügen passen nicht alle Varianten eines Themas ins Prompt. Bei „erste zehn Zeilen“
  fehlte `TOP` für Microsoft SQL, obwohl das Buch es behandelt; das Modell sagte, die
  Wissensbasis enthalte zu anderen Datenbanken nichts.
- Eine genannte Quelle beweist nicht, dass die Aussage stimmt. Im Test stand hinter „HAVING kann
  nur mit Aggregatfunktionen kombiniert werden“ eine passende Quelle, die Aussage ist trotzdem zu
  stark verallgemeinert. Die Evals müssen prüfen, ob der Auszug die Aussage stützt.

## Ausblick: Betrieb an einer Hochschule

Lokal läuft das Sprachmodell über Ollama. Ollama ist auf einfache lokale Nutzung ausgelegt; für
viele gleichzeitige Nutzer wäre ein Inferenzserver wie vLLM die bessere Wahl, weil er gleichzeitige
Anfragen laufend bündelt (Continuous Batching) und den Speicher dafür effizient verwaltet. Da die
App das OpenAI-kompatible Format spricht, ändert sich dafür nur die Adresse in
`QUERYANGLER_LLM_BASE_URL`.

- **Mehr Last:** mehrere Kopien des Inferenzservers hinter einem Load Balancer. Weil jede Frage
  einzeln beantwortet wird, kann jede Kopie jede Anfrage übernehmen.
- **Gemeinsamer Index:** Der Index liegt derzeit als Datei beim Container. Mit mehreren
  App-Kopien müssten alle denselben Stand sehen, sobald Lehrende Material hochladen; dafür
  bräuchte es Chroma im Servermodus oder eine gemeinsame Vektordatenbank.
- **Moodle:** Anbindung über LTI, damit der Tutor im Kurs erscheint und Lehrende ihr Material
  dort pflegen.
