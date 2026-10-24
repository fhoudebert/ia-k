"""Finishing a full text book-wide: French typography, long s, cut and glued words."""
from iak import bookformats, fulltext, layout, textfix

NB = " "


def test_typography():
    assert textfix.typo("Il dit : « bonjour » ; puis... il part ! Voir (p. 12 ) , ici.") == \
        f"Il dit{NB}: «{NB}bonjour{NB}»{NB}; puis… il part{NB}! Voir (p. 12), ici."
    assert textfix.typo("- Bonjour, dit-il.") == "— Bonjour, dit-il."
    assert textfix.typo("à 10:30, soit 1,5 h") == "à 10:30, soit 1,5 h"     # numbers stay
    # a note call is a boundary the rules do not see
    t = textfix.typo("le mythe" + layout.call(3) + " , ici")
    assert t == "le mythe" + layout.call(3) + ", ici"


def test_long_s():
    texts = ["Il eft auffi vrai que les chofes font faites.",
             "Il est ainsi, et c'est aussi la chose.", "Les choses sont dites, il est vrai."]
    out, log = textfix.long_s(texts, frozenset({"est", "aussi", "choses"}))
    assert out[0] == "Il est aussi vrai que les choses font faites."    # font: a word
    assert {r["word"]: r["new"] for r in log if r["kept"]} == {
        "eft": "est", "auffi": "aussi", "chofes": "choses"}


def test_cut_and_glued_words():
    texts = (["Le mar eschal vint.", "Le mareschal partit.", "Le mareschal et le roi."] * 3
             + ["Il entra dans la chambredu roi."] + ["la chambre du roi"] * 6
             + ["de rue en rue"] * 3)
    out, log = textfix.words(texts)
    assert out[0] == "Le mareschal vint." and out[9] == "Il entra dans la chambre du roi."
    assert out[-1] == "de rue en rue"                                  # two words stay
    kept = {(r["word"], r["new"]) for r in log if r["kept"]}
    assert ("mar eschal", "mareschal") in kept and ("chambredu", "chambre du") in kept


def test_finish_blocks_and_log(tmp_path):
    blocks = [("h1", "Chapitre"), ("p", "Il eft dit : « oui » ..."),
              ("note", (1, "Voir ( p. 2 ) .")), ("img", bookformats.Picture(b"x"))]
    out, log = fulltext.finish_blocks(blocks, "fr", fulltext.Finish(old_print=True))
    assert out[1] == ("p", f"Il eft dit{NB}: «{NB}oui{NB}»…")    # no lexicon in the tests
    assert out[2] == ("note", (1, "Voir (p. 2)."))
    assert out[3] == blocks[3]
    # not French: no typography
    out, _ = fulltext.finish_blocks(blocks, "en", fulltext.Finish())
    assert out[1] == blocks[1]


LEX = frozenset("""était lui même savait allait allait bontés roi enfants charmants moments
allèrent assemblèrent différents différent diffèrent avez envoyez envoyés croit doit partir
teste tête des dès il ils vous les""".split())


def test_modern_spelling():
    from iak.modernise import modernise
    texts = ["Il estoit tard, & luy-mesme ne sçavoit pas où il alloit.",
             "Vous avez les bontez du Roy. Ils allerent voir les charmans enfans.",
             "Estoit-il là ? Aussi tost dit. ILS AVOIENT des bontez, vous les envoyez.",
             "Il croit qu'il doit partir. Il teste sa force. Les momens s'assemblerent."]
    out, log = modernise(texts, LEX)
    assert out[0] == "Il était tard, & lui-même ne savait pas où il allait."
    assert out[1] == "Vous avez les bontés du Roi. Ils allèrent voir les charmants enfants."
    assert out[2] == "Était-il là ? Aussitôt dit. ILS AVAIENT des bontés, vous les envoyez."
    assert out[3] == "Il croit qu'il doit partir. Il teste sa force. Les moments s'assemblèrent."
    left = {r["word"] for r in log if not r["kept"]}
    assert "teste" in left and "croit" not in left             # a doubtful case, listed
    assert len({r["word"] for r in log}) == len(log)              # one line per form
    # without a lexicon: the lists only
    out, _ = modernise(["Il estoit là, luy aussi ; il alloit."])
    assert out == ["Il était là, lui aussi ; il alloit."]


def test_finish_modern():
    out, log = textfix.finish(["Il estoit là ..."], typography=True, modern=True)
    assert out == ["Il était là…"]
    assert any(r["kind"] == "modern spelling" for r in log)
