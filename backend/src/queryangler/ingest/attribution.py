"""Erzeugt ATTRIBUTION.md aus dem Manifest.

CC BY-SA verlangt bei Weiterverwendung: Urheber nennen, Lizenz nennen und
verlinken, Änderungen kenntlich machen, abgeleitete Inhalte unter derselben
Lizenz weitergeben. Diese Datei deckt alle vier Punkte ab und wird bei jedem
Abruf neu erzeugt, damit sie nie veraltet.
"""

from typing import Any

from queryangler.config import BOOK_AUTHOR, BOOK_TITLE
from queryangler.ingest.wiki import page_url


def render_attribution(manifest: dict[str, Any]) -> str:
    license_info = manifest["license"]
    rows = "\n".join(
        f"| [{p['chapter']}]({p['url']}) "
        f"| [Revision {p['revid']}]({p['permalink']}) "
        f"| [Versionsgeschichte]({p['history_url']}) |"
        for p in manifest["pages"]
    )
    return f"""# Quellen und Lizenz der Wissensbasis

Die Wissensbasis dieses Projekts beruht auf dem Wikibook
[„{BOOK_TITLE}“]({page_url(BOOK_TITLE)}) aus den deutschsprachigen Wikibooks.
Autor laut Titelseite: {BOOK_AUTHOR}. Alle weiteren Autoren sind in den
Versionsgeschichten der einzelnen Kapitel aufgeführt (Links unten).

- **Lizenz:** [{license_info["text"]}]({license_info["url"]}) (laut API beim Abruf)
- **Abgerufen am:** {manifest["fetched_at"]}
- **Verwendete Fassungen:** je Kapitel über die Revisions-ID festgehalten (Permanentlinks unten)

## Änderungen gegenüber dem Original

Die Texte wurden für die Verwendung als Wissensbasis automatisch umgewandelt:
Navigationselemente, Bearbeitungslinks, Bilder und Einzelnachweise wurden entfernt,
die Inhalte in Textblöcke (Überschrift, Absatz, Liste, Tabelle, Code) überführt und
für die Suche in Abschnitte zerlegt. Der Wortlaut wurde nicht umgeschrieben.

Die abgeleiteten Daten im Ordner `data/` stehen unter derselben Lizenz wie das
Original. Der Programmcode dieses Projekts steht unter einer eigenen Lizenz
(siehe `LICENSE`).

## Kapitel und Fassungen

| Kapitel | Fassung | Autoren |
|---|---|---|
{rows}
"""
