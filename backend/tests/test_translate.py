"""Translation against a fake TranslateGemma server and a fake chat server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from iak import api, db, servers, translate
from iak.library import Library

from .test_analyze import FakeChat
from .test_api import TOKEN, call, wait


class FakeTranslateGemma:
    """/props with TranslateGemma's template, /completion answering with the
    text after the prompt, in capitals (the "translation")."""

    def __init__(self, n_ctx=8192, reply=None):
        self.prompts = []
        self.reply = reply or (lambda text: text.upper())
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/props":
                    self._send({"chat_template": "{{ item['source_lang_code'] }}",
                                "default_generation_settings": {"n_ctx": n_ctx}})
                else:
                    self._send({"data": [{"id": "translategemma"}]})

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.path == "/completion"
                outer.prompts.append(req)
                text = req["prompt"].split(":\n\n\n", 1)[1].split("<end_of_turn>")[0]
                self._send({"content": outer.reply(text) + "<end_of_turn>"})

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def test_detect_language():
    assert translate.detect_language("Le registre de la bibliothèque mentionne l'achat "
                                     "de trois volumes et il est dans le cabinet.") == "fr"
    assert translate.detect_language("The marine charts were stored in the east cabinet "
                                     "and the atlas was sent to the binder.") == "en"
    assert translate.detect_language("abc def") is None
    # "de", "la", "un" are Spanish too: the elisions and "et" tell French
    assert translate.detect_language("Le registre de la bibliothèque mentionne l'achat de "
                                     "trois volumes reliés, d'un atlas et de plusieurs cartes "
                                     "marines au cours de l'année 1712.") == "fr"
    assert translate.detect_language("El registro de la biblioteca menciona la compra de tres "
                                     "volúmenes, un atlas y varias cartas en el año 1712.") == "es"


def test_split_text_keeps_paragraphs():
    text = "\n\n".join(f"Paragraphe {i}. " + "mot " * 40 for i in range(10))
    pieces = translate.split_text(text, 600)
    assert all(len(p) <= 600 for p in pieces)
    assert "\n\n".join(pieces).split() == text.split()
    # a paragraph longer than the limit is cut on sentences, then words
    long = " ".join(f"Phrase {i} assez longue pour remplir." for i in range(100)) + " " + "x" * 900
    pieces = translate.split_text(long, 500)
    assert all(len(p) <= 500 for p in pieces) and "".join(pieces).replace(" ", "") \
        == long.replace(" ", "")


def test_translategemma_prompt_and_pieces():
    tg = FakeTranslateGemma(n_ctx=2048)
    try:
        tr = translate.Translator.connect(servers.Client(tg.url))
        assert tr.format == "translategemma" and tr.piece_chars == 2867
        text = "\n\n".join(f"Paragraphe {i} : " + "texte " * 99 + "fin." for i in range(8))
        out = tr.translate(text, "fr", "en")
        assert out == text.upper()
        assert len(tg.prompts) > 1
        p = tg.prompts[0]
        assert p["prompt"].startswith("<start_of_turn>user\nYou are a professional French (fr)"
                                      " to English (en) translator.")
        assert "Please translate the following French text into English:\n\n\nParagraphe 0" \
            in p["prompt"] and p["prompt"].endswith("<end_of_turn>\n<start_of_turn>model\n")
        assert p["stop"] == ["<end_of_turn>", "<start_of_turn>"]
    finally:
        tg.close()


def test_short_output_is_retried():
    answers = iter(["", "COMPLETE TRANSLATION OF THE TEXT"])
    tg = FakeTranslateGemma(reply=lambda text: next(answers))
    try:
        tr = translate.Translator.connect(servers.Client(tg.url))
        assert tr.translate("Une phrase assez longue pour être traduite.", "fr", "en") \
            == "COMPLETE TRANSLATION OF THE TEXT"
        assert [p["temperature"] for p in tg.prompts] == [0.1, 0.4]
    finally:
        tg.close()


def test_chat_fallback():
    chat = FakeChat(reply=lambda req: "Translation: The atlas was damaged.")
    try:
        host = servers.Host("t", chat=chat.url, translate="http://127.0.0.1:9")
        tr = translate.translator_for(host)
        assert tr.format == "chat"
        assert tr.translate("L'atlas était endommagé.", "fr", "en") == "The atlas was damaged."
        msg = chat.requests[0]["messages"][0]["content"]
        assert "Translate the following French text into English" in msg
    finally:
        chat.close()
    with pytest.raises(translate.TranslateError):
        translate.translator_for(servers.Host("t", chat="http://127.0.0.1:9"))


def test_document_translation_cache_and_export(tmp_path, native_pdf):
    tg = FakeTranslateGemma()
    try:
        tr = translate.Translator.connect(servers.Client(tg.url))
        with Library(tmp_path / "lib") as lib:
            doc = lib.import_file(native_pdf).doc_id
            assert translate.document_language(lib, doc) == "fr"
            r = translate.translate_document(lib, tr, doc, None, "en")
            assert r == {"src": "fr", "tgt": "en", "units": 3, "translated": 3, "cached": 0}
            assert translate.translate_document(lib, tr, doc, "fr", "en")["cached"] == 3
            rows = translate.translations(lib, doc, "en")
            assert rows[0]["text"] == rows[0]["source"].upper() and not rows[0]["stale"]
            # a corrected page is stale, then translated again
            unit = rows[1]["unit_id"]
            lib.set_unit_text(unit, "Texte corrigé de la page deux.")
            assert translate.translations(lib, doc, "en")[1]["stale"]
            r = translate.translate_document(lib, tr, doc, "fr", "en")
            assert r["translated"] == 1 and r["cached"] == 2
            assert translate.translations(lib, doc, "en")[1]["text"] == \
                "TEXTE CORRIGÉ DE LA PAGE DEUX."
            out = translate.export(lib, doc, "en", tmp_path / "t.md", bilingual=True)
            md = out.read_text(encoding="utf-8")
            assert md.startswith("# ") and "## p. 1" in md and "TEXTE CORRIGÉ" in md \
                and "Texte corrigé" in md
            with pytest.raises(translate.TranslateError):
                translate.export(lib, doc, "de", tmp_path / "t.txt", "txt")
            lib.delete(doc)
            assert lib.con.execute("SELECT count(*) FROM translations").fetchone()[0] == 0
    finally:
        tg.close()


def test_schema_3_is_upgraded(tmp_path):
    import sqlite3
    path = tmp_path / "old" / "iak.sqlite"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA[: db.SCHEMA.index("-- schema 4")])
    old.execute("PRAGMA user_version = 3")
    old.commit()
    old.close()
    con = db.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "score" in [r[1] for r in con.execute("PRAGMA table_info(analyses)")]
    assert con.execute("SELECT count(*) FROM translations").fetchone()[0] == 0
    con.close()


def test_api_translation(tmp_path, native_pdf):
    tg = FakeTranslateGemma()
    chat = FakeChat()
    host = servers.Host("test", chat=chat.url, translate=tg.url)
    app = api.App(tmp_path / "lib", host, token=TOKEN)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        s, langs, _ = call(port, "GET", "/api/languages")
        assert s == 200 and {"code": "la", "name": "Latina"} in langs
        assert call(port, "POST", "/api/translate", {"text": "x", "tgt": ""})[0] == 400
        assert call(port, "POST", "/api/translate", {"text": " ", "tgt": "en"})[0] == 400
        _, r, _ = call(port, "POST", "/api/translate",
                       {"text": "Bonjour.", "src": "fr", "tgt": "en"})
        j = wait(port, r["job"])
        assert j["status"] == "done" and j["result"]["text"] == "BONJOUR."
        assert j["result"]["format"] == "translategemma"
        # undetectable language: an error that says to choose it
        _, r, _ = call(port, "POST", "/api/translate", {"text": "Bonjour.", "tgt": "en"})
        assert "choose" in wait(port, r["job"])["error"]

        with Library(tmp_path / "lib") as lib:
            doc = lib.import_file(native_pdf).doc_id
            unit = lib.units(doc)[0]["id"]
        _, r, _ = call(port, "POST", "/api/translate/document", {"unit_id": unit, "tgt": "en"})
        j = wait(port, r["job"])
        assert j["status"] == "done" and j["result"]["src"] == "fr" and not j["result"]["cached"]
        _, r, _ = call(port, "POST", "/api/translate/document", {"doc_id": doc, "tgt": "en"})
        j = wait(port, r["job"])
        assert j["result"]["translated"] == 2 and j["result"]["cached"] == 1
        s, t, _ = call(port, "GET", f"/api/documents/{doc}/translations?tgt=en")
        assert s == 200 and t["languages"] == ["en"] and all(u["text"] for u in t["units"])
        assert call(port, "POST", "/api/translate/document", {"doc_id": 999, "tgt": "en"})[0] \
            == 404
        out = tmp_path / "doc.en.txt"
        s, e, _ = call(port, "POST", f"/api/documents/{doc}/translations/export",
                       {"tgt": "en", "path": str(out), "format": "txt"})
        assert s == 200 and out.read_text(encoding="utf-8").count("[p. ") == 3
        assert call(port, "POST", f"/api/documents/{doc}/translations/export",
                    {"tgt": "de", "path": str(out)})[0] == 409
        _, info, _ = call(port, "GET", "/api/servers")
        assert info["translate"]["ok"] and info["translate"]["props"]["translategemma"]
    finally:
        srv.shutdown()
        srv.server_close()
        tg.close()
        chat.close()
