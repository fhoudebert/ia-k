"""Reading pages again with the vision model, against a fake vision server."""
import threading

import pytest

from iak import api, servers, transcription
from iak.analyze import AnalysisError
from iak.library import Library

from .test_analyze import FakeChat
from .test_api import TOKEN, call, wait


def reply(req):
    """The fake model: "corrects" atlas -> ATLAS in the OCR text it is given,
    or reads the page as a fixed text."""
    content = req["messages"][-1]["content"]
    prompt = content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    if "<ocr>" in prompt:
        return prompt.split("<ocr>\n", 1)[1].split("\n</ocr>")[0].replace("atlas", "ATLAS relié")
    return "```\nPage lue par le modèle de vision.\n```"


@pytest.fixture
def scanned(tmp_path, native_pdf):
    """A library whose PDF pages pass for OCR pages: 1 good, 2 and 3 poor."""
    lib = Library(tmp_path / "lib")
    doc = lib.import_file(native_pdf).doc_id
    units = lib.units(doc)
    for u, conf in zip(units, (95, 60, 40)):
        lib.con.execute("UPDATE units SET source='ocr', ocr_conf=? WHERE id=?", (conf, u["id"]))
    lib.con.commit()
    yield lib, doc, [u["id"] for u in units]
    lib.close()


def test_agreement_and_diff():
    assert transcription.agreement("", "x") is None
    assert transcription.agreement("Le relieur répare", "le relieur répare") == 1.0
    assert 0.5 < transcription.agreement("le relieur a réparé l'atlas",
                                         "le relieur a réparé la carte") < 1
    ops = transcription.diff("le relieur  de la rue", "le relieur de la grand rue")
    assert "".join(t for op, t in ops if op != "-") == "le relieur de la grand rue"
    assert "".join(t for op, t in ops if op != "+") == "le relieur  de la rue"
    assert ["+", "grand "] in ops


def test_loop_removed():
    assert transcription._drop_loop("Titre\nligne\nfin\nfin\nfin\nfin\nfin") == \
        "Titre\nligne\nfin\nfin"
    assert transcription._drop_loop("a\n\n\n\nb") == "a\n\n\n\nb"


def test_read_apply_restore(scanned):
    lib, doc, (u1, u2, u3) = scanned
    chat = FakeChat(reply=reply)
    try:
        r = transcription.read_page(lib, chat.client(), u2, "correct")
        assert "ATLAS" in r.text and 0.8 < r.score < 1 and not r.truncated
        assert lib.con.execute("SELECT text FROM units WHERE id=?", (u2,)).fetchone()[0] \
            != r.text                                         # not applied yet
        # correcting: capped from the OCR text's length; repetition discouraged
        assert chat.requests[0]["max_tokens"] == 600
        assert chat.requests[0]["presence_penalty"] == 1.5
        t = transcription.read_page(lib, chat.client(), u3, "transcribe")
        assert t.text == "Page lue par le modèle de vision." and t.score < 0.2
        assert "<ocr>" not in chat.requests[1]["messages"][-1]["content"][0]["text"]
        assert chat.requests[1]["max_tokens"] == transcription.MAX_TOKENS

        transcription.apply(lib, r.id)
        unit = lib.con.execute("SELECT * FROM units WHERE id=?", (u2,)).fetchone()
        assert unit["text"] == r.text and unit["edited"] == 1
        assert transcription.readers(lib, doc) == {u2: {"id": r.id, "model": "gemma-4-26b",
                                                         "task": "correct"}}
        assert [h.unit_id for h in lib.find("relié", mode="kw")[0]] == [u2]
        # the text corrected by hand afterwards is no longer the model's
        lib.set_unit_text(u2, unit["text"] + " Corrigé.")
        assert transcription.page_reader(lib, u2) is None
        transcription.restore(lib, u2)
        unit = lib.con.execute("SELECT * FROM units WHERE id=?", (u2,)).fetchone()
        assert unit["text"] == unit["text_extracted"] and unit["edited"] == 0
        with pytest.raises(AnalysisError):
            transcription.apply(lib, 9999)
    finally:
        chat.close()


def test_read_document(scanned):
    lib, doc, (u1, u2, u3) = scanned
    assert [u["id"] for u in transcription.select_pages(lib, doc, "low")] == [u2, u3]
    assert len(transcription.select_pages(lib, doc, "low", 50)) == 1
    assert len(transcription.select_pages(lib, doc, "ocr")) == 3
    chat = FakeChat(reply=reply)
    try:
        lib.set_unit_text(u3, "Corrigé à la main.")
        r = transcription.read_document(lib, chat.client(), doc, replace=True)
        assert (r["pages"], r["read"], r["replaced"], r["kept_edited"]) == (2, 2, 1, 1)
        assert lib.con.execute("SELECT text FROM units WHERE id=?", (u3,)).fetchone()[0] \
            == "Corrigé à la main."
        # again: nothing to read, the readings are reused
        r = transcription.read_document(lib, chat.client(), doc, replace=True)
        assert r["read"] == 0 and r["skipped"] == 2 and len(chat.requests) == 2
        # cancelled before the first page
        r = transcription.read_document(lib, chat.client(), doc, pages="all", refresh=True,
                                        cancelled=lambda: True)
        assert r["cancelled"] and r["read"] == 0
    finally:
        chat.close()


def test_not_a_page_document(tmp_path, txt_file):
    with Library(tmp_path / "lib") as lib:
        doc = lib.import_file(txt_file).doc_id
        with pytest.raises(AnalysisError):
            transcription.select_pages(lib, doc)


def test_api_readings(tmp_path, native_pdf):
    chat = FakeChat(reply=reply)
    host = servers.Host("test", chat=chat.url, vision="http://127.0.0.1:9/v1")
    app = api.App(tmp_path / "lib", host, token=TOKEN)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        with Library(tmp_path / "lib") as lib:
            doc = lib.import_file(native_pdf).doc_id
            units = [u["id"] for u in lib.units(doc)]
            lib.con.execute("UPDATE units SET source='ocr', ocr_conf=50")
            lib.con.commit()
        assert call(port, "POST", "/api/read", {"unit_id": 999})[0] == 404
        assert call(port, "POST", "/api/read", {"doc_id": doc, "method": "x"})[0] == 400
        assert call(port, "POST", "/api/read", {"doc_id": doc, "threshold": 10})[0] == 409
        # one page, with the vision server down: the chat server reads it
        _, r, _ = call(port, "POST", "/api/read", {"unit_id": units[1]})
        j = wait(port, r["job"])
        assert j["status"] == "done" and "ATLAS" in j["result"]["text"]
        aid = j["result"]["analysis"]
        s, d, _ = call(port, "GET", f"/api/analyses/{aid}/diff")
        assert s == 200 and ["+", "l'ATLAS relié"] in d["ops"] and not d["same"]
        s, r, _ = call(port, "POST", f"/api/analyses/{aid}/apply")
        assert s == 200 and wait(port, r["job"])["status"] == "done"
        _, us, _ = call(port, "GET", f"/api/documents/{doc}/units")
        assert us[1]["reader"]["id"] == aid and us[0]["reader"] is None
        assert call(port, "GET", f"/api/analyses/{aid}/diff")[1]["same"]
        s, r, _ = call(port, "POST", f"/api/units/{units[1]}/restore")
        _, us, _ = call(port, "GET", f"/api/documents/{doc}/units")
        assert us[1]["reader"] is None and not us[1]["edited"]
        # the whole document, replacing
        _, r, _ = call(port, "POST", "/api/read", {"doc_id": doc, "pages": "ocr",
                                                   "replace": True})
        j = wait(port, r["job"])
        assert j["result"]["replaced"] == 3 and j["result"]["skipped"] == 1
        _, an, _ = call(port, "GET", f"/api/analyses?doc={doc}")
        assert all(a["score"] is not None for a in an if a["task"] == "correct")
        # a queued job can be cancelled
        assert call(port, "POST", "/api/jobs/999/cancel")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
        chat.close()


def test_cancel_queued_job(tmp_path):
    app = api.App(tmp_path / "lib", servers.Host("t"), token=TOKEN)
    started = threading.Event()
    release = threading.Event()

    def slow(app_, lib, job):
        started.set()
        release.wait(5)
        return {"cancelled": job.cancel}
    api.JOBS["slow"] = slow
    try:
        first = app.submit("slow", {})
        started.wait(5)
        second = app.submit("slow", {})
        second.cancel = first.cancel = True
        release.set()
        for _ in range(100):
            if second.status == "cancelled":
                break
            threading.Event().wait(0.05)
        assert first.status == "cancelled" and first.result == {"cancelled": True}
        assert second.status == "cancelled" and second.result is None
    finally:
        del api.JOBS["slow"]
