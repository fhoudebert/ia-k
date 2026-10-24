"""Documents in full text: pictures of text replaced by text, illustrations kept."""
import threading
import zipfile

import numpy as np
import pytest

from iak import api, bookformats, fulltext, servers
from iak.library import Library

from .conftest import needs_tesseract
from .test_analyze import FakeChat
from .test_api import TOKEN, call, wait

pymupdf = pytest.importorskip("pymupdf")


def picture_png(w=300, h=200) -> bytes:
    """A "photograph": a grey gradient with noise."""
    rng = np.random.default_rng(1)
    a = np.tile(np.linspace(40, 200, w), (h, 1)) + rng.normal(0, 18, (h, w))
    data = np.clip(a, 0, 255).astype(np.uint8).tobytes()
    pix = pymupdf.Pixmap(pymupdf.csGRAY, w, h, data, 0)
    return pix.tobytes("png")


TEXT = ("Le registre de la bibliothèque mentionne l'achat de trois volumes reliés, d'un "
        "atlas et de plusieurs cartes marines au cours de l'année 1712.")


def test_find_illustrations():
    page = np.full((600, 400), 235, np.uint8)
    page[100:110, 40:360] = 30                       # a line of text
    page[200:400, 80:320] = np.linspace(40, 200, 240)  # a picture
    page[500:503, 40:360] = 30                       # a thin rule: not a picture
    rng = np.random.default_rng(0)
    for y, x in rng.integers(0, 400, (40, 2)):      # specks
        page[y, x] = 0
    found = fulltext.find_illustrations(page, [(40, 100, 360, 110)])
    assert len(found) == 1
    x0, y0, x1, y1 = found[0]
    assert x0 <= 80 and y0 <= 200 and x1 >= 316 and y1 >= 396
    assert y1 - y0 < 260
    # a line of text is ink too: masked by its words, it is not a picture
    assert fulltext.find_illustrations(page[:150], []) != []
    assert fulltext.find_illustrations(page[:150], [(40, 100, 360, 110)]) == []
    # a junk "word" over a picture is not used as a mask
    boxes = [[40, 100, 120, 110, "Le", 96], [130, 100, 200, 110, "registre", 95],
             [80, 200, 320, 400, "~~~", 12]]
    assert fulltext.text_rects(boxes) == [(40, 100, 120, 110), (130, 100, 200, 110)]


def test_assemble():
    I = fulltext.Item
    pic = bookformats.Picture(b"x", "image/jpeg", 10, 10)
    pages = []
    titles = ["L'atlas", "Le relieur", "Les cartes", "Le printemps", "Le retour"]
    for n in range(5):
        pages.append([I("text", "Histoire du registre", 9), I("text", titles[n], 18),
                      I("text", f"Le relieur a reçu {titles[n]} et il le rend sans", 11),
                      I("img", pic=pic),
                      I("text", f"retard, page {titles[n]}.", 11), I("text", str(n + 1), 9)])
    blocks = fulltext.assemble(pages)
    kinds = [k for k, _ in blocks]
    assert kinds[:3] == ["h1", "p", "img"]
    assert "Histoire du registre" not in [t for _, t in blocks]       # running head
    assert not any(t == "3" for _, t in blocks)                       # page number
    # joined over the picture, which comes after the paragraph
    assert blocks[1] == ("p", "Le relieur a reçu L'atlas et il le rend sans retard, page L'atlas.")
    assert blocks[2][0] == "img"


def native_pdf_with_picture(path):
    doc = pymupdf.open()
    for n in range(2):
        page = doc.new_page(width=420, height=595)
        page.insert_text((40, 60), f"Chapitre {n + 1}", fontsize=20)
        page.insert_textbox(pymupdf.Rect(40, 80, 380, 200), TEXT, fontsize=11)
        if n == 0:
            page.insert_image(pymupdf.Rect(80, 220, 340, 390), stream=picture_png())
        page.insert_textbox(pymupdf.Rect(40, 420, 380, 560), TEXT.upper(), fontsize=11)
    doc.save(path)
    return path


def test_native_pdf(tmp_path):
    with Library(tmp_path / "lib") as lib:
        doc = lib.import_file(native_pdf_with_picture(tmp_path / "registre.pdf")).doc_id
        title, blocks, counts = fulltext.library_document(lib, doc)
        assert counts == {"pages": 2, "illustrations": 1, "ocr_pages": 0, "notes": 0}
        kinds = [k for k, _ in blocks]
        assert kinds[0] == "h1" and kinds.count("img") == 1 and kinds.count("h1") == 2
        assert kinds.index("img") < kinds.index("h1", 1)                # on page 1
        r = fulltext.convert(lib, doc, "epub")
        out = fulltext.output_dir(lib, doc) / r["file"]
        assert r["file"] == "registre.texte.epub" and r["illustrations"] == 1
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            assert "OEBPS/images/i1.jpg" in names and "OEBPS/c2.xhtml" in names
            assert "<dc:language>fr</dc:language>" in z.read("OEBPS/content.opf").decode()
        for fmt in ("pdf", "html", "docx", "rtf", "md"):
            r = fulltext.convert(lib, doc, fmt)
            assert (fulltext.output_dir(lib, doc) / r["file"]).stat().st_size > 500
        assert (fulltext.output_dir(lib, doc) / "registre.texte_images" / "i1.jpg").exists()
        html = (fulltext.output_dir(lib, doc) / "registre.texte.html").read_text("utf-8")
        assert html.count("data:image/jpeg;base64,") == 1
        assert len(fulltext.outputs(lib, doc)) == 6
        # text only
        title, blocks, counts = fulltext.library_document(lib, doc, illustrations=False)
        assert "img" not in [k for k, _ in blocks]
        # deleting the document takes its versions away
        lib.delete(doc, backup=False)
        assert not fulltext.output_dir(lib, doc).exists()


@needs_tesseract
def test_scanned_pdf_and_epub(tmp_path):
    # a scanned page: text and a picture, as one image
    src = native_pdf_with_picture(tmp_path / "net.pdf")
    with pymupdf.open(src) as net:
        scans = [p.get_pixmap(dpi=200).tobytes("png") for p in net]
    scan = pymupdf.open()
    for png in scans:
        p = scan.new_page(width=420, height=595)
        p.insert_image(p.rect, stream=png)
    scan.save(tmp_path / "scan.pdf")
    with Library(tmp_path / "lib", ocr_langs="fra") as lib:
        doc = lib.import_file(tmp_path / "scan.pdf").doc_id
        assert lib.units(doc)[0]["source"] == "ocr"
        title, blocks, counts = fulltext.library_document(lib, doc)
        assert counts["ocr_pages"] == 2 and counts["illustrations"] == 1
        text = " ".join(t for k, t in blocks if k != "img")
        assert "atlas" in text and "1712" in text
    # an EPUB of scanned pages, with a cover and a real illustration
    epub = tmp_path / "scans.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(f'<item id="p{i}" href="p{i}.png" media-type="image/png"/>'
                        for i in (1, 2))
        z.writestr("OEBPS/content.opf", '<?xml version="1.0" encoding="utf-8"?><package '
                   'xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier '
                   'id="id">x</dc:identifier><dc:title>R</dc:title><dc:language>fr</dc:language>'
                   '</metadata><manifest><item id="cover" href="cover.png" media-type="image/png" '
                   'properties="cover-image"/><item id="ill" href="ill.png" '
                   f'media-type="image/png"/>{items}<item id="c1" href="c1.xhtml" '
                   'media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/>'
                   '</spine></package>')
        z.writestr("OEBPS/c1.xhtml", '<?xml version="1.0" encoding="utf-8"?><html xmlns='
                   '"http://www.w3.org/1999/xhtml"><head><title>c</title></head><body>'
                   '<p><img src="cover.png"/></p><div><img src="p1.png"/></div>'
                   '<p>Avant</p><p><img src="ill.png"/></p><p><img src="p2.png"/></p>'
                   '</body></html>')
        z.writestr("OEBPS/cover.png", scans[1])
        z.writestr("OEBPS/ill.png", picture_png())
        z.writestr("OEBPS/p1.png", scans[0])
        z.writestr("OEBPS/p2.png", scans[1])
    out = tmp_path / "scans.texte.epub"
    c = fulltext.epub_document(epub, out, "fra")
    assert (c["pictures"], c["text_pictures"], c["kept"]) == (3, 2, 1)
    assert c["illustrations"] == 1 and c["size_after"] < c["size_before"]
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        body = z.read("OEBPS/c1.xhtml").decode()
        opf = z.read("OEBPS/content.opf").decode()
    assert {"OEBPS/cover.png", "OEBPS/ill.png", "OEBPS/iak-ft-1.jpg"} <= names
    assert "OEBPS/p1.png" not in names and "p1.png" not in opf and "iak-ft-1.jpg" in opf
    assert '<img src="cover.png"/>' in body and "Avant" in body and "1712" in body
    assert "<h2>Chapitre 1</h2>" in body


def test_api(tmp_path):
    app = api.App(tmp_path / "lib", servers.Host("t"), token=TOKEN)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        with Library(tmp_path / "lib") as lib:
            doc = lib.import_file(native_pdf_with_picture(tmp_path / "registre.pdf")).doc_id
            txt = tmp_path / "t.txt"
            txt.write_text("du texte\n", encoding="utf-8")
            tdoc = lib.import_file(txt).doc_id
        assert call(port, "POST", f"/api/documents/{doc}/fulltext", {"format": "xls"})[0] == 400
        assert call(port, "POST", f"/api/documents/{tdoc}/fulltext", {})[0] == 409
        s, r, _ = call(port, "POST", f"/api/documents/{doc}/fulltext", {"format": "pdf"})
        j = wait(port, r["job"])
        assert j["status"] == "done" and j["result"]["file"] == "registre.texte.pdf"
        _, files, _ = call(port, "GET", f"/api/documents/{doc}/fulltext")
        assert [f["name"] for f in files] == ["registre.texte.pdf"]
        dest = tmp_path / "copie.pdf"
        s, _, _ = call(port, "POST", f"/api/documents/{doc}/fulltext/save",
                       {"name": "registre.texte.pdf", "path": str(dest)})
        assert s == 200 and dest.read_bytes()[:4] == b"%PDF"
        assert call(port, "POST", f"/api/documents/{doc}/fulltext/save",
                    {"name": "../iak.sqlite", "path": str(dest)})[0] == 400
        s, r, _ = call(port, "POST", f"/api/documents/{doc}/fulltext/import",
                       {"name": "registre.texte.pdf"})
        assert wait(port, r["job"])["result"]["files"][0]["status"] == "imported"
        assert call(port, "DELETE",
                    f"/api/documents/{doc}/fulltext?name=registre.texte.pdf")[0] == 200
        assert call(port, "GET", f"/api/documents/{doc}/fulltext")[1] == []
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- layout and notes

from iak import layout  # noqa: E402


def line(text, x0, y0, x1, h=10):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y0 + h, "h": h}


def test_paragraphs_from_the_lines():
    L = [line("Le registre de la bibliothèque mentionne", 20, 0, 300),   # indented
         line("l'achat de trois volumes et d'un atlas, de", 0, 14, 300),
         line("cartes marines.", 0, 28, 90),                            # short, ends
         line("Le relieur reçut l'atlas au printemps et le", 0, 42, 300),
         line("rendit à l'été.", 0, 56, 80),
         line("Il le rendit relié", 0, 90, 300),                         # extra space
         line("CHAPITRE II", 110, 104, 190),                             # centred
         line("La suite du registre parle du relieur et", 20, 118, 300)]
    paras = [t for t, _, _ in layout.paragraphs(L)]
    assert paras == ["Le registre de la bibliothèque mentionne l'achat de trois volumes et "
                     "d'un atlas, de cartes marines.",
                     "Le relieur reçut l'atlas au printemps et le rendit à l'été.",
                     "Il le rendit relié", "CHAPITRE II",
                     "La suite du registre parle du relieur et"]
    # without geometry: Tesseract's paragraphs, joined where one plainly goes on
    assert layout.text_paragraphs("Le relieur reçut\nl'atlas de\n\nParis, au printemps.\n\n"
                                  "Il le rendit.") == ["Le relieur reçut l'atlas de Paris, au "
                                                       "printemps.", "Il le rendit."]
    assert layout.continues("pour le", "Parlement") and not layout.continues("fin.", "Il")


def test_note_calls():
    assert layout.note_mark("1 Voir l'inventaire.") == ("1", "Voir l'inventaire.")
    assert layout.note_mark("(a) Voir.") == ("a", "Voir.") and layout.note_mark("* Voir.")
    assert layout.note_mark("Le relieur") is None
    for body in ("l'atlas1 au", "l'atlas¹ au", "l'atlas (1) au", "l'atlas! au"):
        texts = ["Rien ici.", body]
        assert layout.link_call(texts, "1", 7) and texts[1] == f"l'atlas{layout.call(7)} au", body
    texts = ["en 1712 et 1"]
    assert not layout.link_call(texts, "1", 1)               # not after a word
    assert layout.split_calls(f"a{layout.call(3)} b") == ["a", 3, " b"]


def book_with_notes(path):
    css = ("body{font-family:serif;font-size:11pt} p{text-indent:1.5em;margin:0;"
           "text-align:justify} .n{font-size:8.5pt;text-indent:0} h1{text-align:center}")
    P = ("Le registre de la bibliothèque mentionne l'achat de trois volumes reliés, d'un atlas "
         "et de plusieurs cartes marines au cours de l'année mil sept cent douze.")
    pages = [f"<h1>Chapitre premier</h1><p>{P} Le relieur reçut l'atlas<sup>1</sup> au "
             f"printemps, et il le rendit avec une reliure neuve.</p><p>{P} Le bibliothécaire "
             "dressa la liste complète des cartes réparées pour le</p>"
             "<p class='n'>&nbsp;</p><p class='n'>1 Voir l'inventaire de 1712, folio douze.</p>",
             "<p style='text-indent:0'>Parlement de Paris, qui en ordonna la restauration.</p>"
             f"<p>{P} Le prix de chaque carte<sup>2</sup> est noté.</p>"
             "<p class='n'>&nbsp;</p><p class='n'>2 Le prix est donné en livres tournois.</p>"]
    doc = pymupdf.open()
    for h in pages:
        doc.new_page(width=420, height=595).insert_htmlbox(pymupdf.Rect(45, 50, 375, 560), h,
                                                           css=css)
    doc.save(path)
    return path


def check_notes(md):
    assert "l'atlas[^1] au printemps" in md and "carte[^2] est noté" in md
    assert "réparées pour le Parlement de Paris" in md               # over the page break
    assert md.count("Le registre de la bibliothèque") == 3            # 3 paragraphs, not cut
    assert "[^1]: Voir l'inventaire de 1712, folio douze." in md
    assert "[^2]: Le prix est donné en livres tournois." in md


def test_notes_native_pdf(tmp_path):
    with Library(tmp_path / "lib") as lib:
        doc = lib.import_file(book_with_notes(tmp_path / "notes.pdf")).doc_id
        r = fulltext.convert(lib, doc, "md")
        check_notes((fulltext.output_dir(lib, doc) / r["file"]).read_text("utf-8"))
        r = fulltext.convert(lib, doc, "epub")
        with zipfile.ZipFile(fulltext.output_dir(lib, doc) / r["file"]) as z:
            body = z.read("OEBPS/c1.xhtml").decode()
        assert 'epub:type="noteref" href="#n1"' in body and 'epub:type="footnote" id="n1"' in body
        assert 'xmlns:epub="http://www.idpf.org/2007/ops"' in body


@needs_tesseract
def test_notes_scanned_pdf(tmp_path):
    src = book_with_notes(tmp_path / "net.pdf")
    scan = pymupdf.open()
    with pymupdf.open(src) as net:
        for p in net:
            q = scan.new_page(width=420, height=595)
            q.insert_image(q.rect, stream=p.get_pixmap(dpi=200).tobytes("png"))
    scan.save(tmp_path / "scan.pdf")
    with Library(tmp_path / "lib", ocr_langs="fra") as lib:
        doc = lib.import_file(tmp_path / "scan.pdf").doc_id
        r = fulltext.convert(lib, doc, "md")
        check_notes((fulltext.output_dir(lib, doc) / r["file"]).read_text("utf-8"))


def test_note_formats(tmp_path):
    blocks = [("h1", "Chapitre"), ("p", f"Le relieur{layout.call(1)} rend l'atlas."),
              ("note", (1, "Voir l'inventaire.")), ("h1", "Suite"), ("p", "Fin.")]
    for fmt in ("html", "docx", "rtf", "md", "txt", "pdf"):
        out = tmp_path / f"b.{fmt}"
        bookformats.write(blocks, fmt, "Livre", "fr", out)
        assert out.stat().st_size > 50
    html = (tmp_path / "b.html").read_text("utf-8")
    assert 'href="#n1" id="r1"><sup>1</sup>' in html and '<p class="note" id="n1">' in html
    assert html.index('id="n1"') < html.index("Suite")               # at the end of its chapter
    assert "\\super 1" in (tmp_path / "b.rtf").read_text("ascii")
    assert "relieur[1] rend" in (tmp_path / "b.txt").read_text("utf-8")
    assert "[^1]: Voir" in (tmp_path / "b.md").read_text("utf-8")
    assert layout.CALL_OPEN not in (tmp_path / "b.md").read_text("utf-8")


def test_call_read_as_a_letter():
    known = layout.words(["Le relieur reçut l'atlasi", "l'atlas des cartes, et lui aussi"])
    texts = ["Il vint avec lui hier.", "Le relieur reçut l'atlasi au printemps."]
    assert layout.link_call(texts, "1", 4, known)
    assert texts == ["Il vint avec lui hier.", f"Le relieur reçut l'atlas{layout.call(4)} au printemps."]


def epub_of_pictures(path, pngs):
    """An EPUB 2 whose pages are pictures, one per document."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(f'<item id="p{i}" href="images/p{i}.png" media-type="image/png"/>'
                        f'<item id="c{i}" href="c{i}.html" media-type="application/xhtml+xml"/>'
                        for i in range(len(pngs)))
        spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(pngs)))
        z.writestr("content.opf", '<?xml version="1.0" encoding="utf-8"?><package '
                   'xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">'
                   'x</dc:identifier><dc:title>L</dc:title><dc:language>fr</dc:language>'
                   f'</metadata><manifest>{items}</manifest><spine>{spine}</spine></package>')
        for i, png in enumerate(pngs):
            z.writestr(f"images/p{i}.png", png)
            z.writestr(f"c{i}.html", '<?xml version="1.0" encoding="utf-8"?><html xmlns='
                       '"http://www.w3.org/1999/xhtml"><head><title>c</title></head><body>'
                       f'<p class="c"><img src="images/p{i}.png"/></p></body></html>')
    return path


@needs_tesseract
def test_epub_focus_elisions_and_notes(tmp_path, monkeypatch):
    """Tesseract reads the pictures; its doubtful words go to the vision
    model line by line; an l' read as a letter is put right (with the
    lexicon); the notes at the foot of a page are linked to their calls."""
    from iak import doubt
    lex = tmp_path / "words"
    lex.mkdir()
    (lex / "fr.txt").write_text("anxiété\ninfini\nauteur\nlucrèce\nmythe\n", encoding="utf-8")
    monkeypatch.setattr(doubt, "LEXICON_DIR", lex)
    css = ("body{font-family:serif;font-size:12pt} p{margin:0 0 .4em;text-align:justify} "
           ".n{font-size:9pt}")
    pngs = []
    for k, (word, note) in enumerate([("Lanxiété", "Voir le docteur Logre, Paris, 1946."),
                                      ("Pinfini", "Le prix est donné en livres.")]):
        filler = ("Le registre de la bibliothèque mentionne l'achat de trois volumes reliés, "
                  "d'un atlas et de plusieurs cartes marines au cours de l'année. ") * 4 + \
            "L'anxiété du poète touche à l'infini. "            # written right elsewhere
        html = (f"<p>{filler}</p><p>Page {k + 1}. Le poète chante, et {word} de Lucrèce, dit "
                "l'auteur, est un "
                "mythe<sup>1</sup>, que le relieur reçut au printemps avec les cartes.</p>"
                f"<p class='n'>&nbsp;</p><p class='n'>1 {note}</p>")
        doc = pymupdf.open()
        page = doc.new_page(width=420, height=595)
        page.insert_htmlbox(pymupdf.Rect(40, 50, 380, 560), html, css=css)
        pngs.append(page.get_pixmap(dpi=200).tobytes("png"))
    src = epub_of_pictures(tmp_path / "livre.epub", pngs)
    lines = []

    def reply(req):                       # the vision model reads a line: nothing new
        assert req["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image")
        lines.append(req)
        return ""
    chat = FakeChat(reply=reply)
    try:
        out = tmp_path / "livre.texte.epub"
        c = fulltext.epub_document(src, out, "fra", focus_client=chat.client())
    finally:
        chat.close()
    assert c["text_pictures"] == 2 and c["notes"] == 2 and len(lines) >= 2
    with zipfile.ZipFile(out) as z:
        p0, p1 = z.read("c0.html").decode(), z.read("c1.html").decode()
    assert "et L'anxiété de Lucrèce" in p0 and "et l'infini de Lucrèce" in p1
    assert "avec les cartes.</p>" in p0                    # a one-word last line stays
    assert 'href="#iak-n1" id="iak-r1"' in p0 and 'id="iak-n2"' in p1
    assert "Voir le docteur Logre" in p0.split('id="iak-n1"')[1]
    log = fulltext.corrections_path(out).read_text("utf-8")
    assert "Lanxiété\tL'anxiété\tyes" in log and "Pinfini\tl'infini\tyes" in log
