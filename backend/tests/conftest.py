"""Test documents are generated, so the repository holds no binary samples."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")

HAS_TESSERACT = shutil.which("tesseract") is not None
needs_tesseract = pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")

PAGES = [
    "Le registre de la bibliothèque mentionne l'achat de trois volumes reliés, "
    "d'un atlas et de plusieurs cartes marines au cours de l'année 1712.",
    "Le bibliothécaire signale que l'atlas est arrivé endommagé par l'humidité "
    "et qu'il a été confié à un relieur de la rue Saint-Jacques.",
    "Inventory note: the marine charts were stored in the east cabinet.",
]


def _pdf_page(doc, text: str, fontsize: float = 12):
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(60, 60, 535, 780), text, fontsize=fontsize)
    return page


@pytest.fixture
def native_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "registre.pdf"
    doc = pymupdf.open()
    for t in PAGES:
        _pdf_page(doc, t)
    doc.set_metadata({"title": "Registre"})
    doc.save(path)
    return path


def _text_png(text: str, zoom: float = 3.0) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=160)
    page.insert_textbox(pymupdf.Rect(10, 10, 410, 150), text, fontsize=16)
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    """A PDF whose page is only an image of text, as from a scanner."""
    path = tmp_path / "scan.pdf"
    png = _text_png("Facture 4417 reliure atlas montant douze livres")
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=160)
    page.insert_image(page.rect, stream=png)
    doc.save(path)
    return path


@pytest.fixture
def screenshot(tmp_path: Path) -> Path:
    path = tmp_path / "capture.png"
    path.write_bytes(_text_png("Erreur 404 fichier introuvable serveur", zoom=1.5))
    return path


@pytest.fixture
def docx_file(tmp_path: Path) -> Path:
    docx = pytest.importorskip("docx")
    path = tmp_path / "notes.docx"
    d = docx.Document()
    d.add_paragraph("Introduction sans titre.")
    d.add_heading("Chapitre premier", level=1)
    d.add_paragraph("Les cartes marines sont rangées dans l'armoire est.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "objet", "prix"
    t.cell(1, 0).text, t.cell(1, 1).text = "atlas", "12 livres"
    d.add_heading("Chapitre second", level=1)
    d.add_paragraph("Le relieur rend l'atlas en mars.")
    d.save(path)
    return path


@pytest.fixture
def txt_file(tmp_path: Path) -> Path:
    path = tmp_path / "journal.txt"
    path.write_text("Première ligne.\n\nDeuxième paragraphe sur la reliure.\n", encoding="utf-8")
    return path


@pytest.fixture
def md_file(tmp_path: Path) -> Path:
    path = tmp_path / "guide.md"
    path.write_text("# Installation\n\nCopier la clé.\n\n## Usage\n\nLancer le serveur.\n",
                    encoding="utf-8")
    return path


@pytest.fixture
def html_file(tmp_path: Path) -> Path:
    path = tmp_path / "page.html"
    path.write_text("<html><head><title>Page test</title><style>p{}</style></head><body>"
                    "<h1>Atlas</h1><p>Un atlas de 1712.</p><script>var x=1;</script>"
                    "<h2>Cartes</h2><p>Cartes marines&nbsp;: six.</p></body></html>",
                    encoding="utf-8")
    return path


@pytest.fixture
def epub_file(tmp_path: Path) -> Path:
    path = tmp_path / "livre.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:title>Observations</dc:title><dc:creator>Pierre Belon</dc:creator>'
                   '</metadata><manifest>'
                   '<item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine>'
                   '</package>')
        z.writestr("OEBPS/text/ch1.xhtml",
                   "<html><body><h1>Du crocodile</h1><p>Le crocodile du Nil.</p></body></html>")
        z.writestr("OEBPS/text/ch2.xhtml",
                   "<html><body><h1>De l'hippopotame</h1><p>Cheval de rivière.</p></body></html>")
    return path
