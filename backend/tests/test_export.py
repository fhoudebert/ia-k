"""Markdown exports of answers, conversations and analyses."""
import threading

from iak import analyze, api, export, servers
from iak.library import Library

from .test_analyze import FakeChat
from .test_api import TOKEN, call

TURN = {"question": "Qui a réparé l'atlas ?", "status": "answered",
        "text": "Le relieur de la rue Saint-Jacques [S1], au printemps [S2].",
        "scope": "registre.pdf",
        "sources": [{"ref": "S1", "doc_name": "registre.pdf", "label": "p. 2",
                     "text": "Le bibliothécaire signale que l'atlas est arrivé endommagé "
                             "et qu'il a été confié à un relieur de la rue Saint-Jacques."},
                    {"ref": "S2", "doc_name": "notes_*.docx", "label": "sect. 1",
                     "text": "x " * 400},
                    {"ref": "S3", "doc_name": "inutile.pdf", "label": "p. 9", "text": "non cité"}]}


def test_answer_and_conversation():
    md = export.answer_md(TURN, "fr")
    assert md.startswith("## Qui a réparé l'atlas ?\n\n*Limité à : registre.pdf*")
    assert "Le relieur de la rue Saint-Jacques [S1], au printemps [S2]." in md
    assert "- **[S1]** *registre.pdf*, p. 2\n  > Le bibliothécaire signale" in md
    assert "*notes\\_\\*.docx*" in md                    # Markdown marks escaped
    assert md.count("x ") < 200 and " …" in md            # long passages cut
    assert "inutile.pdf" not in md                        # only the cited sources
    no = export.answer_md({"question": "Et la date ?", "status": "no_answer"}, "en")
    assert "The documents do not allow an answer" in no
    warn = export.answer_md({**TURN, "status": "unsourced", "text": "Sans citation."}, "fr")
    assert "> Réponse sans source vérifiée" in warn and "inutile.pdf" in warn
    conv = export.conversation_md([TURN, {"question": "Et quand ?", "status": "no_answer"}],
                                  "fr")
    assert conv.startswith("# Questions sur la bibliothèque\n\n*Exporté d'IA-K le ")
    assert conv.count("\n## ") == 2 and conv.endswith("\n")
    assert "  >" not in export.answer_md(TURN, "fr", quote=False)


def test_names_and_pages():
    assert export.safe_name('Résumé - a/b: "c"?.md') == "Résumé - a b c .md"
    assert export._pages_cited("Un [p. 3] deux [p. 3] [D1, p. 5] [pp. 7-8; chap. 2] [S1]") \
        == ["p. 3", "D1, p. 5", "pp. 7-8", "chap. 2"]


def test_analyses(tmp_path, native_pdf, docx_file):
    chat = FakeChat(reply=lambda req: "- L'atlas est envoyé chez le relieur [p. 2]\n"
                                      "- Les cartes sont rangées [p. 3]")
    try:
        with Library(tmp_path / "lib") as lib:
            d1 = lib.import_file(native_pdf).doc_id
            d2 = lib.import_file(docx_file).doc_id
            s = analyze.summarize(lib, chat.client(), d1, "fr")
            name, md = export.analysis_md(lib, s.id, "fr")
            assert name == "Résumé - registre.md"
            assert md.startswith("# Résumé — registre.pdf\n\n*Produit par IA-K le ")
            assert "Modèle : gemma-4-26b" in md
            assert "- Document : *registre.pdf*\n- Pages citées : p. 2, p. 3" in md
            c = analyze.compare(lib, chat.client(), [d1, d2], "les dates", "en")
            name, md = export.analysis_md(lib, c.id, "en")
            assert name == "comparison - registre - notes.md"
            assert "**Aspect**: les dates" in md
            assert "- **D1** *registre.pdf*\n- **D2** *notes.docx*" in md
            unit = lib.units(d1)[1]["id"]
            lib.con.execute("INSERT INTO analyses (doc_id, unit_id, task, text, score,"
                            " created_at) VALUES (?,?,?,?,?,0)",
                            (d1, unit, "correct", "Texte corrigé.", 0.913))
            lib.con.commit()
            rid = lib.con.execute("SELECT max(id) FROM analyses").fetchone()[0]
            name, md = export.analysis_md(lib, rid, "fr")
            assert name == "Texte OCR corrigé - registre - p. 2.md"
            assert "*concordance avec le texte de la page : 91 %*" in md and "- Page : p. 2" in md
    finally:
        chat.close()


def test_api_exports(tmp_path, native_pdf):
    chat = FakeChat(reply=lambda req: "Résumé [p. 1].")
    app = api.App(tmp_path / "lib", servers.Host("t", chat=chat.url), token=TOKEN)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        with Library(tmp_path / "lib") as lib:
            doc = lib.import_file(native_pdf).doc_id
            aid = analyze.summarize(lib, chat.client(), doc, "fr").id
        assert call(port, "POST", "/api/export/conversation", {"turns": []})[0] == 400
        s, r, _ = call(port, "POST", "/api/export/conversation",
                       {"turns": [TURN], "lang": "en", "single": True})
        assert s == 200 and r["markdown"].startswith("# Qui a réparé")
        assert r["name"].startswith("IA-K - Qui a réparé l'atlas") and r["name"].endswith(".md")
        out = tmp_path / "conv.md"
        s, r, _ = call(port, "POST", "/api/export/conversation",
                       {"turns": [TURN, TURN], "lang": "fr", "path": str(out)})
        assert s == 200 and out.read_text("utf-8").count("\n## Qui a") == 2
        s, r, _ = call(port, "GET", f"/api/analyses/{aid}/markdown?lang=en")
        assert s == 200 and r["markdown"].startswith("# Summary — registre.pdf")
        assert call(port, "GET", "/api/analyses/999/markdown")[0] == 404
        s, r, _ = call(port, "POST", f"/api/analyses/{aid}/export",
                       {"path": str(tmp_path / "r.md"), "lang": "fr"})
        assert s == 200 and "Pages citées : p. 1" in (tmp_path / "r.md").read_text("utf-8")
        assert call(port, "POST", f"/api/analyses/{aid}/export", {"path": ""})[0] == 400
        assert call(port, "POST", f"/api/analyses/{aid}/export",
                    {"path": str(tmp_path / "no" / "dir" / "r.md")})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()
        chat.close()
