"""Doubtful OCR words: confidence, the library's vocabulary, odd letters; the
pages worth reading again; their correction by the text model."""
import json
import re
import threading

import pytest

from iak import api, correction, doubt, servers, transcription
from iak.analyze import AnalysisError
from iak.library import Library

from .test_analyze import FakeChat
from .test_api import TOKEN, call, wait

GOOD = ("Le relieur a reçu l'atlas des cartes marines et il le rend au printemps "
        "avec une reliure neuve en veau fauve.")
OCR = "Le reliéur a reçu l'at1as des cartes rnarines et il le rend au printemps avec une Reliure."


def boxes_for(text, low=()):
    """Word boxes as Tesseract gives them, confidence 85 or 30 for `low`."""
    out, x = [], 10
    for w in text.split():
        out.append([x, 10, x + 8 * len(w), 30, w, 30.0 if w in low else 85.0])
        x += 8 * len(w) + 6
    return out


@pytest.fixture
def lib(tmp_path, native_pdf):
    """The native PDF is the trusted text; one more document passes for OCR."""
    lib = Library(tmp_path / "lib")
    lib.import_file(native_pdf)
    txt = tmp_path / "ocr.txt"
    txt.write_text("page scannée\n", encoding="utf-8")       # replaced below
    good = tmp_path / "good.txt"
    good.write_text(GOOD + "\n", encoding="utf-8")
    lib.import_file(good)                           # the vocabulary: native text
    doc = lib.import_file(txt).doc_id
    unit = lib.units(doc)[0]["id"]
    lib.con.execute("UPDATE units SET text=?, source='ocr', boxes=?, width=900, height=40"
                    " WHERE id=?", (OCR, json.dumps(boxes_for(OCR, low=("reliéur",))), unit))
    lib.con.commit()
    yield lib, doc, unit
    lib.close()


def test_odd_chars():
    assert doubt.odd_chars("at1as") and doubt.odd_chars("aTlas") and doubt.odd_chars("l|vre")
    assert doubt.odd_chars("eeee")
    assert not any(doubt.odd_chars(w) for w in ("atlas", "1er", "2e", "15th", "McDonald", "XVIe"))


def test_page_and_document(lib):
    lib, doc, unit = lib
    d = doubt.unit(lib, unit)
    found = {s["word"]: s["reasons"] for s in d["suspects"]}
    assert found["reliéur"] == ["conf", "unknown"]       # low confidence, not a known word
    assert found["at1as"] == ["chars"]
    assert found["rnarines"] == ["unknown"]
    assert "Reliure" not in found                         # a capital: maybe a name
    assert "printemps" not in found and "cartes" not in found
    s = next(x for x in d["suspects"] if x["word"] == "at1as")
    assert OCR[s["start"]:s["end"]] == "at1as" and s["box"] and s["conf"] == 85.0
    assert d["ratio"] == round(3 / d["words"], 3)
    pages = doubt.document(lib, doc)
    assert pages[0]["doubt"] and pages[0]["doubtful"] == 3
    # (select_pages "doubt" is tried on a PDF in test_api)
    # corrected by hand: trusted, nothing doubtful
    lib.set_unit_text(unit, GOOD)
    assert doubt.unit(lib, unit)["doubtful"] == 0
    assert doubt.marked("a bc d", [{"start": 2, "end": 4}]) == "a ⟦bc⟧ d"


def test_word_lists(lib, tmp_path, monkeypatch):
    lib, doc, unit = lib
    words = tmp_path / "words"
    words.mkdir()
    (words / "marine.txt").write_text("# vocabulaire de la marine\nrnarines\n", encoding="utf-8")
    monkeypatch.setattr(doubt, "WORDS_DIR", words)
    assert "rnarines" not in {s["word"] for s in doubt.unit(lib, unit)["suspects"]}


FIXES = {"reliéur": "relieur", "at1as": "atlas", "rnarines": "marines"}


def fix_reply(req):
    """The fake text model: one line per numbered doubtful word."""
    prompt = req["messages"][-1]["content"]
    assert isinstance(prompt, str)                        # text only
    out = []
    for m in re.finditer(r"^(\d+)\. .*?⟦(.+?)⟧", prompt, re.M):
        out.append(f"{m.group(1)}: {FIXES.get(m.group(2), m.group(2))}")
    return "\n".join(out)


def test_fix_with_the_text_model(lib):
    lib, doc, unit = lib
    chat = FakeChat(vision=False, reply=fix_reply)
    try:
        r = transcription.read_page(lib, chat.client(), unit, "fix")
        assert r.method == "fix" and r.text == OCR.replace("reliéur", "relieur").replace(
            "at1as", "atlas").replace("rnarines", "marines")
        assert 0.7 < r.score < 1
        src = json.loads(lib.con.execute("SELECT sources FROM analyses WHERE id=?",
                                         (r.id,)).fetchone()[0])
        assert src["kept"] == 3 and src["refused"] == 0
        assert {c["word"]: c["new"] for c in src["changes"]} == FIXES
        transcription.apply(lib, r.id)
        assert lib.units(doc)[0]["text"] == r.text
        assert transcription.page_reader(lib, unit)["task"] == "fix"
        # once corrected, nothing doubtful is left
        with pytest.raises(AnalysisError, match="no doubtful word"):
            transcription.read_page(lib, chat.client(), unit, "fix")
    finally:
        chat.close()


def test_api(tmp_path, native_pdf):
    chat = FakeChat(vision=False, reply=fix_reply)
    app = api.App(tmp_path / "lib", servers.Host("t", chat=chat.url), token=TOKEN)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        with Library(tmp_path / "lib") as l:
            doc = l.import_file(native_pdf).doc_id
            unit = l.units(doc)[0]["id"]
            l.con.execute("UPDATE units SET text=?, source='ocr', boxes=? WHERE id=?",
                          (OCR, json.dumps(boxes_for(OCR, low=("reliéur",))), unit))
            l.con.commit()
        s, d, _ = call(port, "GET", f"/api/units/{unit}/doubts")
        assert s == 200 and d["doubtful"] >= 2 and d["suspects"][0]["box"]
        s, pages, _ = call(port, "GET", f"/api/documents/{doc}/doubts")
        assert s == 200 and pages[0]["doubt"] and not pages[1]["doubtful"]
        assert call(port, "GET", "/api/units/999/doubts")[0] == 404
        assert call(port, "GET", "/api/documents/999/doubts")[0] == 404
        s, r, _ = call(port, "POST", "/api/read", {"doc_id": doc, "pages": "doubt",
                                                   "method": "fix"})
        j = wait(port, r["job"])
        assert j["status"] == "done" and j["result"]["read"] == 1, j
        # every page: the pages without doubtful word are not sent to the model
        n = len(chat.requests)
        s, r, _ = call(port, "POST", "/api/read", {"doc_id": doc, "pages": "all",
                                                   "method": "fix", "refresh": True})
        j = wait(port, r["job"])
        assert (j["result"]["read"], j["result"]["skipped"]) == (1, 2)
        assert len(chat.requests) == n + 1
        assert call(port, "POST", "/api/read", {"unit_id": unit, "method": "x"})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()
        chat.close()


def test_judging_the_proposals():
    vocab = {"atlas", "relieur", "mot", "joint", "arrêtera"}
    assert correction.judge("at1as", "atlas", vocab) == "known"
    assert correction.judge("francais", "français", set()) == "close"     # one letter
    assert correction.judge("arréle", "arrêtèle", vocab) == "too different"
    assert correction.judge("arréle", "arrêtera", vocab) == "known"
    assert correction.judge("motjoint", "mot joint", vocab) == "known"
    assert correction.judge("atlas", "atlas", vocab) is None
    assert correction.judge("rnot", "m0t", vocab) == "odd"
    # the vision model has seen the letters: more may change
    assert correction.judge("cbarte", "charte", set(), loose=True) == "close"
    text = "Le reliéur arréle ici."
    sus = [{"word": "reliéur", "start": 3, "end": 10}, {"word": "arréle", "start": 11, "end": 17}]
    new, changes = correction.apply_changes(text, sus, {0: "relieur", 1: "arrêtèle"}, vocab)
    assert new == "Le relieur arréle ici."
    assert [(c["new"], c["kept"], c["why"]) for c in changes] == [
        ("relieur", True, "known"), ("arrêtèle", False, "too different")]


def test_answers_and_context():
    text = "Le relieur reçut l'at1as, au printemps."
    s = {"word": "at1as", "start": 19, "end": 24}
    assert text[19:24] == "at1as"
    assert correction.context(text, s) == "Le relieur reçut l'⟦at1as⟧, au printemps."
    raw = "1: atlas\n2. « relieur »\n- 3) marines (corrigé)\n9: x\nvoici"
    assert correction.parse_answer(raw, range(1, 4)) == {1: "atlas", 2: "relieur", 3: "marines"}
    # a line read again by the vision model, aligned with its OCR
    line = "Le reliéur a reçu l'at1as des cartes"
    sus = [(0, {"word": "reliéur", "start": 3}), (1, {"word": "at1as", "start": 20})]
    assert correction.proposals_from_line(line, "Le relieur a reçu l'atlas des Cartes", sus) \
        == {0: "relieur", 1: "atlas"}




def test_focus_reads_the_doubtful_lines(tmp_path, native_pdf):
    """Targeted vision: the line holding doubtful words, cut out of the page."""
    seen = []

    def reply(req):
        content = req["messages"][-1]["content"]
        img = content[1]["image_url"]["url"]
        assert img.startswith("data:image/png;base64,")
        seen.append(len(img))
        return "Le relieur a reçu l'atlas des cartes marines et il le rend au printemps avec une Reliure."
    chat = FakeChat(vision=True, reply=reply)
    try:
        with Library(tmp_path / "lib") as lib:
            doc = lib.import_file(native_pdf).doc_id
            good = tmp_path / "good.txt"                     # the vocabulary
            good.write_text(GOOD + "\n", encoding="utf-8")
            lib.import_file(good)
            unit = lib.units(doc)[0]["id"]
            lib.con.execute("UPDATE units SET text=?, source='ocr', boxes=?, width=900,"
                            " height=1270 WHERE id=?",
                            (OCR, json.dumps(boxes_for(OCR, low=("reliéur",))), unit))
            lib.con.commit()
            r = transcription.read_page(lib, chat.client(), unit, "focus")
            assert r.method == "focus" and "relieur a reçu l'atlas des cartes marines" in r.text
            assert r.text.endswith("avec une Reliure.") and len(seen) == 1       # one line
            src = json.loads(lib.con.execute("SELECT sources FROM analyses WHERE id=?",
                                             (r.id,)).fetchone()[0])
            assert src["lines"] == 1 and src["kept"] == 3
            # no boxes: no place to look at
            lib.con.execute("UPDATE units SET boxes=NULL WHERE id=?", (unit,))
            lib.con.commit()
            with pytest.raises(AnalysisError, match="no place|no doubtful"):
                transcription.read_page(lib, chat.client(), unit, "focus")
    finally:
        chat.close()
