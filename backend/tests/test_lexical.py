"""Tests für die Stichwortsuche (BM25)."""

from queryangler.index.lexical import LexicalIndex, tokenize


def meta(role="content"):
    return {"role": role}


def test_tokenize_stems_and_drops_stopwords():
    assert tokenize("Wie zähle ich die Abteilungen?") == tokenize("zählen Abteilung")
    assert "die" not in tokenize("die Abteilung")


def test_rare_words_count_more_than_common_ones():
    index = LexicalIndex(
        ["a", "b", "c"],
        ["Sortieren absteigend mit Tabelle", "Tabelle anlegen", "Tabelle löschen"],
        [meta(), meta(), meta()],
    )
    # "Tabelle" steht überall, "absteigend" nur in a: a gewinnt deutlich
    hits = index.search("Tabelle absteigend sortieren", k=3, roles=["content"])
    assert hits[0].id == "a"
    assert hits[0].score > 2 * hits[1].score


def test_roles_are_filtered_and_unknown_words_ignored():
    index = LexicalIndex(
        ["a", "b"], ["LIKE sucht Muster", "Übung: LIKE verwenden"], [meta(), meta("exercise")]
    )
    assert [h.id for h in index.search("LIKE", k=5, roles=["content"])] == ["a"]
    assert index.search("Apfelkuchen", k=5, roles=["content"]) == []


def test_long_documents_are_damped():
    short = "JOIN verbindet Tabellen"
    long = short + " " + " ".join(f"füllwort{i}" for i in range(200))
    index = LexicalIndex(["kurz", "lang"], [short, long], [meta(), meta()])
    hits = index.search("JOIN", k=2, roles=["content"])
    assert [h.id for h in hits] == ["kurz", "lang"]
