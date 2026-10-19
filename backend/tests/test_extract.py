from iak import chunk, extract, ocr

from .conftest import needs_tesseract


def test_native_pdf(native_pdf):
    ex = extract.extract(native_pdf)
    assert ex.meta["pages"] == 3 and ex.meta["title"] == "Registre"
    assert [u.label for u in ex.units] == ["p. 1", "p. 2", "p. 3"]
    assert ex.units[1].locator == {"page": 2}
    assert "relieur" in ex.units[1].text
    assert all(u.source == "native" for u in ex.units)
    # word boxes in points, inside the page
    x0, y0, x1, y1, word, _ = ex.units[0].boxes[0]
    assert 0 <= x0 < x1 <= ex.units[0].width and word


@needs_tesseract
def test_scanned_pdf_goes_through_ocr(scanned_pdf):
    ex = extract.extract(scanned_pdf)
    u = ex.units[0]
    assert u.source == "ocr" and u.ocr_conf and u.ocr_conf > 50
    assert "4417" in u.text and "reliure" in u.text.lower()
    # boxes converted back to page points
    assert all(b[2] <= u.width + 1 and b[3] <= u.height + 1 for b in u.boxes)


@needs_tesseract
def test_screenshot(screenshot):
    ex = extract.extract(screenshot)
    assert ex.units[0].label == "image" and ex.units[0].source == "ocr"
    assert "404" in ex.units[0].text


def test_no_ocr_option_keeps_empty_scanned_page(scanned_pdf):
    ex = extract.extract(scanned_pdf, extract.Options(ocr=False))
    assert ex.units[0].text == "" and ex.units[0].source == "native"


def test_docx_sections_and_tables(docx_file):
    ex = extract.extract(docx_file)
    labels = [u.label for u in ex.units]
    assert labels == ["§ 1", "Chapitre premier", "Chapitre second"]
    assert "atlas | 12 livres" in ex.units[1].text


def test_txt_md_html(txt_file, md_file, html_file):
    t = extract.extract(txt_file)
    assert t.units[0].label == "l. 1" and "reliure" in t.units[0].text
    m = extract.extract(md_file)
    assert [u.label for u in m.units] == ["Installation", "Usage"]
    h = extract.extract(html_file)
    assert h.meta["title"] == "Page test"
    assert [u.label for u in h.units] == ["Atlas", "Cartes"]
    assert "var x" not in h.units[0].text and "six" in h.units[1].text


def test_epub(epub_file):
    ex = extract.extract(epub_file)
    assert ex.meta == {"title": "Observations", "author": "Pierre Belon"}
    assert [u.label for u in ex.units] == ["Du crocodile", "De l'hippopotame"]
    assert ex.units[1].locator["section"] == 2


def test_unsupported(tmp_path):
    p = tmp_path / "x.xyz"
    p.write_text("x")
    try:
        extract.extract(p)
    except extract.ExtractError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("no error")


def test_tsv_parsing_rebuilds_lines_and_paragraphs():
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    rows = [
        (1, 1, 1, 1, "Bonjour", 90), (1, 1, 1, 2, "monde", 80),
        (1, 1, 2, 1, "suite", 70),
        (1, 2, 1, 1, "nouveau", 60),
    ]
    tsv = head + "".join(f"5\t1\t{b}\t{p}\t{ln}\t{w}\t10\t20\t30\t40\t{c}\t{t}\n"
                         for b, p, ln, w, t, c in rows)
    r = ocr.parse_tsv(tsv)
    assert r.text == "Bonjour monde\nsuite\n\nnouveau"
    assert r.conf == 75.0 and r.boxes[0] == [10, 20, 40, 60, "Bonjour", 90.0]


def test_chunk_overlap_and_offsets():
    text = " ".join(f"Phrase numéro {i} sur la reliure des livres." for i in range(120))
    pieces = chunk.split(text, target=400, overlap=80)
    assert len(pieces) > 5
    for a, b in zip(pieces, pieces[1:]):
        assert b.start < a.end            # overlap
        assert text[a.start:a.end].strip() == a.text
        assert len(a.text) <= 400
    assert pieces[-1].end == len(text)
    assert chunk.split("  court  ")[0].text == "court"
    assert chunk.split("   ") == []
