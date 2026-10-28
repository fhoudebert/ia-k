"""The chapters of a book in full text: its table of contents, its titles."""
from iak import structure


def test_toc_titles():
    texts = ["SOMMAIRE", "Pages Préface historique 7 Les Antilles avant 1789 . . . . 15",
             "La Guadeloupe de 1789 à Victor Hughes ... 39 Victor Hughes reprend la",
             "Guadeloupe aux Anglais 111 ILLUSTRATIONS Plan des forts 112"]
    assert structure.is_toc_page(texts)
    assert structure.toc_titles(texts, 400) == [
        "Préface historique", "Les Antilles avant 1789", "La Guadeloupe de 1789 à Victor Hughes",
        "Victor Hughes reprend la Guadeloupe aux Anglais"]


def test_chapters_from_the_toc():
    blocks = [("h1", "VICTOR HUGHES"), ("p", "PRÉFACE"), ("p", "HISTORIQUE"),
              ("p", "Le texte de la préface, assez long pour être un paragraphe."),
              ("p", "LES"), ("p", "ANTILLES"), ("p", "AVANT"),
              ("p", "Les traités de paix de Paris et de Versailles."),
              ("p", "LES ANTILLES AVANT 1789"),                    # a running head
              ("p", "La suite du chapitre, sur la page suivante."),
              ("p", "HUGHES REPREND LA GUADELOUPE AUX ANGLAIS"),   # « Victor » lost
              ("p", "Le chapitre suivant."), ("h2", "HNOINILYUVW 37S11 SNVO")]
    toc = ["Préface historique", "Les Antilles avant 1789",
           "Victor Hughes reprend la Guadeloupe aux Anglais"]
    out, n = structure.structure(blocks, toc)
    assert n == 3
    assert [b for b in out if b[0] == "h1"] == [
        ("h1", "Préface historique"), ("h1", "Les Antilles avant 1789"),
        ("h1", "Victor Hughes reprend la Guadeloupe aux Anglais")]
    assert ("p", "LES ANTILLES AVANT 1789") not in out                   # the head went
    assert ("p", "HNOINILYUVW 37S11 SNVO") in out                        # no heading
    assert len(out) == 9                       # the title lines and the head gone


def test_toc_words_from_the_text():
    blocks = [("p", "LES CORSAIRES DE LA GUADELOUPE"), ("p", "Un texte."),
              ("p", "LA FIN"), ("p", "Un autre texte.")]
    out, n = structure.structure(blocks, ["Los Corsaires de la Guadeloupe", "La fin"],
                                 frozenset({"les", "corsaires", "de", "la", "fin"}))
    assert out[0] == ("h1", "Les Corsaires de la Guadeloupe") and n == 2


def test_chapter_patterns():
    blocks = [("p", "LIVRE PREMIER"), ("p", "CHAPITRE PREMIER"), ("p", "Texte."),
              ("p", "CHAPITRE II."), ("p", "Texte."), ("p", "Le chapitre II de ce livre dit")]
    out, n = structure.structure(blocks, [])
    assert [k for k, _ in out] == ["h1", "h2", "p", "h2", "p", "p"] and n == 3


def test_epub_contents_list_the_chapters(tmp_path):
    import re
    import zipfile

    from iak import bookformats
    blocks = [("p", "Titre")] + [("h1", "Premier")] + [("p", f"Texte {n}.") for n in range(400)] \
        + [("h1", "Second"), ("p", "Fin.")]
    out = tmp_path / "b.epub"
    bookformats.write(blocks, "epub", "Livre", "fr", out)
    with zipfile.ZipFile(out) as z:
        nav = z.read("OEBPS/nav.xhtml").decode()
        files = [n for n in z.namelist() if re.fullmatch(r"OEBPS/c\d+\.xhtml", n)]
    assert re.findall(r"<a [^>]*>(.*?)</a>", nav) == ["Livre", "Premier", "Second"]
    assert len(files) > 3                       # the long chapter in several files
