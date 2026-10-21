"""Documents in full text: pictures of text replaced by text, illustrations kept."""
import threading
import zipfile

import numpy as np
import pytest

from iak import api, bookformats, fulltext, servers
from iak.library import Library

from .conftest import needs_tesseract
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
        assert counts == {"pages": 2, "illustrations": 1, "ocr_pages": 0}
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
