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

- **`backend/` und später `frontend/`** in einem Repository, zusammengeführt über Docker Compose.
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
| roter Beispielkasten (20×) | Code mit `verdict: "negativ"` | Der Tutor darf fehlerhaften oder nicht empfohlenen Code nicht als Lösung anbieten. |
| grüner Beispielkasten (12×) | Code mit `verdict: "positiv"` | Empfohlene Variante als solche erkennbar |
| Kastentitel, z. B. „Firebird-Version“ | `title` am Codeblock | Dialekt und Zweck eines Beispiels bleiben erhalten |
| Bereich „Ausgabe“ im Kasten | eigener Blocktyp `output` | Abfrage und Ergebnis werden nicht verwechselt |
| Übungs- und Lösungskästen (je 151×) | `role: "exercise"` bzw. `"solution"` mit Nummer | Lösungen lassen sich ausblenden, wenn der Tutor nur Hinweise geben soll. Übungscode, der absichtlich Fehler enthält, ist als solcher erkennbar. |
| nachgebaute Überschriften (69×) | echte Überschriften | Das Chunking kann an ihnen schneiden. |
| Hinweiskästen | Absatz „Hinweis: …“ | statt einer einzeiligen Tabelle |

## Testbarkeit

Logik und Ein-/Ausgabe sind getrennt. Bereinigung und Attribution sind reine Funktionen und werden
mit festen Beispieldaten getestet. Der API-Client erhält seine Netzwerkverbindung von außen
(Parameter `transport`). In den Tests ersetzt `httpx.MockTransport` die echte API. Alle Tests
laufen dadurch ohne Netzwerk, auch in der CI.

## Bekannte Grenzen

- Die Wissensbasis entspricht inhaltlich dem Stand von SQL:2008 und älteren Datenbankversionen.
  Neuere Funktionen (z. B. JSON-Funktionen) deckt sie nicht ab. Der Tutor soll in solchen Fällen
  sagen, dass die Wissensbasis dazu nichts enthält.
- In drei Tabellen des Kapitels „DDL – Einzelheiten“ steht Code in den Zellen. Er verliert bei
  der Umwandlung in Tabellenzeilen seine Zeilenumbrüche.
