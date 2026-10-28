"""Documents in full text: a document made of images of text becomes a light
document of real text that an e-reader can reflow, its illustrations kept.

- A PDF, or an image, of the library: its text is the library's (native,
  OCR, corrected, or read again by the vision model). Illustrations are
  found on each page: the images a native PDF holds, and, on a scanned page,
  the regions of ink or grey that no recognised word covers (photographs,
  engravings, maps, diagrams), cut out of the page. Headings come from the
  font sizes, running heads and page numbers go, paragraphs are joined across
  pages. Written as EPUB (default), PDF, HTML, DOCX, RTF or Markdown.
- An EPUB holding pictures of text (scanned pages): each picture is read
  (Tesseract, or the vision model); a picture of text is replaced by its
  paragraphs (and the illustrations cut out of it), a picture that is not
  text stays. The cover is never touched.
"""
from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import bookformats, layout, ocr
from .book import BookError, join_lines

GRID = 60                     # cells across a page for the illustration search
MIN_AREA = 0.012              # an illustration covers at least this share of the page
RENDER_DPI = 100              # page rendered to look for illustrations
CROP_DPI = 150                # resolution of the illustrations kept
JPEG_QUALITY = 82
SCAN_COVER = 0.7              # a picture covering this much of a page is the scan itself


class FullTextError(RuntimeError):
    pass


# ---------------------------------------------------------------- illustrations

def gray_array(pix) -> np.ndarray:
    """A grey PyMuPDF pixmap as a (height, width) array."""
    a = np.frombuffer(pix.samples, dtype=np.uint8)
    return a.reshape(pix.height, pix.stride)[:, :pix.width]


def find_illustrations(gray: np.ndarray, text_rects, min_area: float = MIN_AREA) -> list:
    """Regions (x0, y0, x1, y1, in pixels) of ink or grey that are not text.

    The page is cut into a grid; a cell is "graphic" when enough of its
    pixels are darker than the paper once the words' rectangles are blanked
    out; neighbouring graphic cells make a region; small, thin, sparse
    regions (specks, rules, a scan's dark border) are dropped."""
    h, w = gray.shape
    if h < 20 or w < 20:
        return []
    paper = float(np.percentile(gray, 90))
    ink = gray < (paper - 40)
    for x0, y0, x1, y1 in text_rects:
        pad = max(2, int((y1 - y0) * 0.6))
        ink[max(0, int(y0) - pad):min(h, int(y1) + pad),
            max(0, int(x0) - pad):min(w, int(x1) + pad)] = False
    cell = max(6, w // GRID)
    gh, gw = (h + cell - 1) // cell, (w + cell - 1) // cell
    pad_h, pad_w = gh * cell - h, gw * cell - w
    grid = np.pad(ink, ((0, pad_h), (0, pad_w))).reshape(gh, cell, gw, cell).mean(axis=(1, 3))
    graphic = grid > 0.06
    seen = np.zeros_like(graphic)
    regions = []
    for r in range(gh):
        for c in range(gw):
            if not graphic[r, c] or seen[r, c]:
                continue
            stack, cells = [(r, c)], []
            seen[r, c] = True
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                for dy in (-1, 0, 1):          # 8 neighbours, one cell gap allowed below
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < gh and 0 <= nx < gw and graphic[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            ys = [y for y, _ in cells]
            xs = [x for _, x in cells]
            r0, r1, c0, c1 = min(ys), max(ys) + 1, min(xs), max(xs) + 1
            density = len(cells) / ((r1 - r0) * (c1 - c0))
            regions.append([c0 * cell, r0 * cell, min(w, c1 * cell), min(h, r1 * cell), density])
    out = []
    for x0, y0, x1, y1, density in _merge(regions, cell * 2):
        rw, rh = x1 - x0, y1 - y0
        if rw * rh < min_area * w * h or rw < 0.08 * w or rh < 0.04 * h:
            continue
        if density < 0.3:
            continue
        edge = x0 <= cell or y0 <= cell or x1 >= w - cell or y1 >= h - cell
        if edge and (rw < 0.1 * w or rh < 0.1 * h):
            continue                          # a scan's dark border or the book's gutter
        if rw > 0.97 * w and rh > 0.97 * h:
            continue                          # the whole page: a dark scan, not a picture
        # a margin of one cell: the pale edges of a picture (a sky) are kept
        out.append((max(0, x0 - cell), max(0, y0 - cell), min(w, x1 + cell), min(h, y1 + cell)))
    return sorted(out, key=lambda b: (b[1], b[0]))


def _merge(regions, gap):
    """Merge regions that touch or nearly do (a picture with a pale band)."""
    regions = [list(r) for r in regions]
    changed = True
    while changed:
        changed = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, b = regions[i], regions[j]
                if (a[0] - gap < b[2] and b[0] - gap < a[2]
                        and a[1] - gap < b[3] and b[1] - gap < a[3]):
                    area_a = (a[2] - a[0]) * (a[3] - a[1])
                    area_b = (b[2] - b[0]) * (b[3] - b[1])
                    merged = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    area = (merged[2] - merged[0]) * (merged[3] - merged[1])
                    merged.append((a[4] * area_a + b[4] * area_b) / max(area, 1))
                    regions[i] = merged
                    del regions[j]
                    changed = True
                    break
            if changed:
                break
    return regions


def text_rects(boxes) -> list:
    """The rectangles of the words that are words: Tesseract sometimes reads
    an engraving's hatching as a "word" of low confidence and great size,
    which would hide the illustration."""
    good = [b for b in boxes if len(b) < 6 or b[5] is None or b[5] >= 40]
    heights = sorted(b[3] - b[1] for b in good)
    if not heights:
        return []
    limit = heights[len(heights) // 2] * 3
    return [tuple(b[:4]) for b in good if b[3] - b[1] <= limit]


# ---------------------------------------------------------------- PDF pages

@dataclass
class Item:
    kind: str                 # text, img
    text: str = ""
    size: float | None = None
    pic: bookformats.Picture | None = None
    ocr: bool = False          # its size from OCR word heights (rough)


def _crop(page, rect_pt) -> bookformats.Picture:
    import pymupdf
    pix = page.get_pixmap(clip=pymupdf.Rect(rect_pt), dpi=CROP_DPI)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    return bookformats.Picture(pix.tobytes("jpg", jpg_quality=JPEG_QUALITY), "image/jpeg",
                               pix.width, pix.height, CROP_DPI)


def page_pictures(page, word_rects_pt) -> list[tuple[float, bookformats.Picture]]:
    """(top in points, picture) of the illustrations of a page."""
    import pymupdf
    area = page.rect.width * page.rect.height
    out = []
    scan = False
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if r.is_empty:
            continue
        if r.width * r.height >= SCAN_COVER * area:
            scan = True                       # the scan of the page: look inside it
            continue
        if r.width * r.height >= MIN_AREA * area:
            out.append((r.y0, _crop(page, r)))
    if scan or not out:
        zoom = RENDER_DPI / 72
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
        rects = [(x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom) for x0, y0, x1, y1 in word_rects_pt]
        known = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()
                 if pymupdf.Rect(info["bbox"]).get_area() < SCAN_COVER * area]
        for x0, y0, x1, y1 in find_illustrations(gray_array(pix), rects):
            r = pymupdf.Rect(x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom) & page.rect
            # an embedded picture already kept is not cut out a second time
            if not any((r & k).get_area() > 0.5 * r.get_area() for k in known):
                out.append((r.y0, _crop(page, r)))
    return sorted(out, key=lambda t: t[0])


def _native_items(page, illustrations: bool) -> list[Item]:
    """Paragraphs with their type size (layout: indents, spacing, short
    lines), and pictures, in reading order."""
    entries = [(top, Item("text", t, round(size, 1)))
               for t, size, top in layout.paragraphs(layout.native_lines(page))]
    if illustrations:
        words = text_rects([list(w[:4]) + [w[4], None] for w in page.get_text("words")])
        for top, pic in page_pictures(page, words):
            entries.append((top, Item("img", pic=pic)))
    entries.sort(key=lambda e: e[0])
    return [it for _, it in entries]


def _ocr_items(page, text: str, boxes, illustrations: bool) -> list[Item]:
    """The library's text of a page (OCR, corrected...) as paragraphs, and its
    illustrations placed where they are on the page."""
    paras = [Item("text", t, size, ocr=True)
             for t, size in layout.ocr_paragraphs(text, boxes or [])]
    if not illustrations:
        return paras
    rects = text_rects(boxes or [])
    pics = page_pictures(page, rects)
    if not pics:
        return paras
    if rects:
        top, bottom = min(r[1] for r in rects), max(r[3] for r in rects)
    else:
        top, bottom = 0.0, page.rect.height
    total = sum(len(p.text) for p in paras) or 1
    cum, cuts = 0, []
    for p in paras:
        cum += len(p.text)
        cuts.append(cum / total)
    out = list(paras)
    for y, pic in reversed(pics):
        frac = (y - top) / max(bottom - top, 1)
        k = 0 if frac <= 0 else len(paras) if frac >= 1 else \
            next((i + 1 for i, c in enumerate(cuts) if c >= frac), len(paras))
        out.insert(k, Item("img", pic=pic))
    return out


PAGE_NO = re.compile(r"[\d\s\-–—.|]+|(page|p\.)\s*\d+|[ivxlcdm]{1,6}\.?", re.I)


def _page_notes(items: list[Item], body: float | None) -> tuple[list[Item], list[list[str]]]:
    """The notes at the foot of a page (smaller type, starting with a mark),
    taken out of its items: (items left, [[mark, text]])."""
    if not body:
        return items, []
    texts = [i for i, it in enumerate(items) if it.kind == "text"]
    k = None
    for i in reversed(texts):
        it = items[i]
        if PAGE_NO.fullmatch(it.text):
            continue
        if it.size and it.size < body * layout.NOTE_SIZE:
            k = i
            continue
        break
    if k is None:
        return items, []
    zone = [i for i in texts if i >= k and not PAGE_NO.fullmatch(items[i].text)]
    first = next((j for j, i in enumerate(zone) if layout.note_mark(items[i].text)), None)
    if first is None:
        return items, []
    notes: list[list[str]] = []
    for i in zone[first:]:
        m = layout.note_mark(items[i].text)
        if m:
            notes.append(list(m))
        else:                                    # a note going on in another paragraph
            notes[-1][1] = join_lines(notes[-1][1], items[i].text)
    gone = set(zone[first:])
    return [it for i, it in enumerate(items) if i not in gone], notes


def assemble(pages: list[list[Item]], notes: bool = True) -> list[tuple]:
    """Blocks for bookformats: headings from the font sizes, running heads and
    page numbers removed, paragraphs joined across pages (also when the next
    one starts with a capital, when the first plainly goes on: no full stop,
    a lowercase letter, a comma or a hyphen at its end), and the notes at the
    foot of the pages linked to their calls (layout)."""
    sizes = Counter()
    for items in pages:
        for it in items:
            if it.kind == "text" and it.size:
                sizes[it.size] += len(it.text)
    body = sizes.most_common(1)[0][0] if sizes else None

    def norm(t):
        # a page number, or an OCR speck beside it, is not part of the head:
        # « LES ANTILLES AVANT 1789 sv » is « les antilles avant # »
        words = re.sub(r"\d+", "#", t.lower()).split()

        def speck(w):
            c = w.strip("#.,-–—|*")
            return "#" in w or not c or (len(c) <= 2 and c not in lex)
        if len(words) > 1 and speck(words[0]):
            words.pop(0)
        if len(words) > 1 and speck(words[-1]):
            words.pop()
        return " ".join(words)
    lex = _lexicon()
    running = set()
    if len(pages) >= 4:
        cnt, short = Counter(), Counter()
        for items in pages:
            texts = [it.text for it in items if it.kind == "text"]
            cnt.update({norm(t) for t in texts[:2] + texts[-2:] if len(t) < 120})
            # the head of a chapter's pages (LA GUADELOUPE 41): short, at the
            # very top or foot, on a few pages only
            short.update({norm(t) for t in texts[:1] + texts[-1:] if len(t) < 60})
        running = {k for k, v in cnt.items() if v >= max(3, 0.3 * len(pages))}
        chapter_heads = {k for k, v in short.items() if v >= 3 and k} - running
    else:
        chapter_heads = set()
    first_seen: set[str] = set()
    blocks: list[list] = []
    n_notes = 0
    known = layout.words(it.text for items in pages for it in items if it.kind == "text") \
        if notes else {}
    for items in pages:
        found = []
        if notes:
            items, found = _page_notes(items, body)
        page_notes = []
        if found:
            texts = [it.text for it in items if it.kind == "text"]
            for mark, text in found:
                n_notes += 1
                layout.link_call(texts, mark, n_notes, known, _lexicon())
                page_notes.append(["note", (n_notes, text)])
            it_texts = iter(texts)
            for it in items:
                if it.kind == "text":
                    it.text = next(it_texts)
        for it in items:
            if it.kind == "img":
                blocks.append(["img", it.pic])
                continue
            t = it.text
            if it.ocr and _junk(t, lex):
                continue
            title = body and it.size and it.size >= body * 1.2
            if PAGE_NO.fullmatch(t) or (norm(t) in running and not title):
                continue
            if norm(t) in chapter_heads and not title:
                if norm(t) in first_seen:
                    continue                     # its first time is the chapter's title
                first_seen.add(norm(t))
            kind = "p"
            # OCR sizes are rough: a heading there is clearly bigger, and
            # does not read as the end of a sentence cut off (« times ; »)
            h2 = 1.35 if it.ocr else 1.2
            if it.ocr and (t[:1].islower() or re.search(r"[,;:]$", t)):
                h2 = None
            if body and it.size and it.size >= body * 1.6 and len(t) < 150 and h2:
                kind = "h1"
            elif body and it.size and h2 and it.size >= body * h2 and len(t) < 200:
                kind = "h2"
            prev = next((b for b in reversed(blocks) if b[0] not in ("img", "note")), None)
            if (kind == "p" and prev is not None and prev[0] == "p"
                    and not _capitals(prev[1])
                    and layout.continues(layout.strip_calls(prev[1], lambda n: ""), t)):
                prev[1] = join_lines(prev[1], t)     # continued over a page break
                continue
            blocks.append([kind, t])
        blocks += page_notes
    return [tuple(b) for b in blocks]


def is_scan(page) -> bool:
    """A page that is the picture of a page (its text, if any, an OCR layer)."""
    import pymupdf
    area = page.rect.width * page.rect.height
    return any((pymupdf.Rect(i["bbox"]) & page.rect).get_area() >= SCAN_COVER * area
               for i in page.get_image_info())


@dataclass
class PageReader:
    """How the scanned pages of a PDF are read for its full-text version
    (instead of the library's text): Tesseract again, its doubtful words
    checked against the PDF's own text layer (another OCR: ABBYY,
    Acrobat...), those still doubtful read again by the vision model
    (focus_client) or the text model (fix_client); or the vision model reads
    whole pages (vision(png) -> text)."""
    langs: str = ocr.DEFAULT_LANGS
    tesseract: str | None = None
    vision: object = None
    focus_client: object = None
    fix_client: object = None
    dpi: int = 300


def _reading_items(r: "Reading") -> list[Item]:
    """The paragraphs of a page read again, its illustrations where they are."""
    out, pics = [], list(r.pics)
    total = sum(len(it.text) for it in r.paras) or 1
    cum = 0
    for it in r.paras:
        while pics and pics[0][0] <= cum / total:
            out.append(Item("img", pic=pics.pop(0)[1]))
        out.append(it)
        cum += len(it.text)
    return out + [Item("img", pic=p) for _, p in pics]


def _read_pages(pdf, numbers: list[int], reader: PageReader, illustrations: bool,
                progress, cancelled, log: list, counts: dict) -> dict[int, "Reading"]:
    """{page number: Reading} of the scanned pages read again, their doubtful
    words corrected (the changes, kept and refused, added to log)."""
    from . import correction, doubt
    readings, layers, pngs, errors = {}, {}, {}, []
    two = reader.focus_client is not None or reader.fix_client is not None
    total = len(numbers) * (2 if two else 1)
    for n, i in enumerate(numbers):
        if cancelled and cancelled():
            raise FullTextError("cancelled")
        if progress:
            progress(n, total)
        page = pdf[i - 1]
        pix = page.get_pixmap(dpi=reader.dpi)
        png = pix.tobytes("png")
        try:
            r = read_picture(png, reader.langs, reader.tesseract, illustrations, reader.vision)
        except Exception as exc:  # noqa: BLE001 - the page keeps the library's text
            errors.append(f"page {i}: {exc}")
            continue
        if not r.is_text:
            continue
        readings[i] = r
        k = reader.dpi / 72
        layers[i] = [[w[0] * k, w[1] * k, w[2] * k, w[3] * k, w[4]]
                     for w in page.get_text("words")]
        if reader.focus_client is not None:
            pngs[i] = png
    if errors and not readings:
        raise FullTextError(f"no page could be read: {errors[0]}")
    boxed = {i: r for i, r in readings.items() if r.boxes}
    counts.update({"read_pages": len(readings), "layer_pages": sum(1 for i in boxed if layers[i]),
                   "confirmed": 0, "from_layer": 0, "corrected": 0, "refused": 0})
    counts["layer_lines"] = 0
    for i, r in boxed.items():
        if layers[i]:
            r.text, r.boxes, garbled = correction.layer_lines(r.text, r.boxes, layers[i])
            if garbled:
                r.paras = [Item("text", t, size, ocr=True)
                           for t, size in layout.ocr_paragraphs(r.text, r.boxes)]
                counts["layer_lines"] += len(garbled)
                log += [(f"page {i}", c) for c in garbled]
    if boxed:
        witness = {i: correction.layer_witness(r.boxes, layers[i]) for i, r in boxed.items()}
        vocab = doubt.vocabulary_from_boxes(r.boxes for r in boxed.values())
        agreed = Counter(w for i, r in boxed.items()
                         for w in correction.agreed_words(r.boxes, witness[i]))
        vocab |= {w for w, c in agreed.items() if c >= 2}
        seen = correction.attested(r.text for r in boxed.values())
        for n, (i, r) in enumerate(sorted(boxed.items()), len(numbers)):
            if cancelled and cancelled():
                raise FullTextError("cancelled")
            if progress and two:
                progress(n, total)
            suspects = doubt.page(r.text, r.boxes, vocab)["suspects"]
            suspects += correction.layer_disagreements(r.text, r.boxes, witness[i], suspects)
            if not suspects:
                continue
            at_box = doubt.box_index(r.text, r.boxes)
            confirmed, from_layer, exact = correction.layer_proposals(
                r.text, suspects, r.boxes, witness[i], vocab)
            counts["confirmed"] += len(confirmed)
            rest = [j for j in range(len(suspects)) if j not in confirmed and j not in from_layer]
            proposals = dict(from_layer)
            left = [suspects[j] for j in rest]
            try:
                if left and reader.focus_client is not None:
                    crop = correction.picture_cropper(pngs[i])
                    got, _ = correction.focus_proposals(reader.focus_client, r.text, left,
                                                        r.boxes, crop)
                    proposals.update({rest[j]: w for j, w in got.items()})
                elif left and reader.fix_client is not None:
                    got, _ = correction.fix_proposals(reader.fix_client, r.text, left, vocab)
                    proposals.update({rest[j]: w for j, w in got.items()})
            except Exception as exc:  # noqa: BLE001 - those words stay as Tesseract read them
                errors.append(f"page {i}: {exc}")
            elided = correction.elisions(left, vocab, seen)
            proposals.update({rest[j]: w for j, w in elided.items() if rest[j] not in proposals})
            # the two OCRs disagree and nothing settled it: Tesseract's word stays, listed
            unsettled = [(f"page {i}", {"word": suspects[j]["word"],
                                        "new": witness[i].get(at_box.get(suspects[j]["start"]), ""),
                                        "kept": False, "why": "text layer reads otherwise"})
                         for j in rest if j not in proposals and "layer" in suspects[j]["reasons"]]
            new, changes = correction.apply_changes(r.text, suspects, proposals, vocab,
                                                    loose=reader.focus_client is not None,
                                                    exact=exact, layer=set(from_layer))
            layer_at = {suspects[j]["start"] for j in from_layer}
            for c in changes:
                if c["start"] in layer_at:
                    c["why"] = f"text layer, {c['why']}"
                    counts["from_layer"] += c["kept"]
            if new != r.text:
                _rename_boxes(r, changes)
                r.text = new
                r.paras = [Item("text", t, size, ocr=True)
                           for t, size in layout.ocr_paragraphs(r.text, r.boxes)]
            log += [(f"page {i}", c) for c in changes] + unsettled
        counts["corrected"] = sum(c["kept"] for _, c in log)
        counts["refused"] = sum(not c["kept"] for _, c in log)
    if errors:
        counts["errors"] = len(errors)
        counts["error"] = errors[0]
    return readings


def _junk(t: str, lex) -> bool:
    """An OCR paragraph made of specks or an ornament (« — … —— », « PEPE »):
    few letters, or a short one without a single known word."""
    letters = sum(c.isalpha() for c in t)
    if re.fullmatch(r"[IVXLCDM]+\.?|[A-Z]\.", t.strip()):
        return False                             # a chapter number
    if letters < 2 or letters < 0.5 * len(t.replace(" ", "")):
        return True
    words = re.findall(r"[^\W\d_]+", t.lower())
    if len(words) >= 4 and sum(len(w) <= 1 for w in words) >= 0.6 * len(words):
        return True                              # « E E e e e E e » : a margin, a gutter
    return len(t) <= 12 and bool(lex) and not any(w in lex and len(w) > 1 for w in words)


def _capitals(t: str) -> bool:
    """A line in capitals (a title, a running head): no paragraph goes on in it."""
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


def library_document(lib, doc_id: int, illustrations: bool = True, progress=None,
                     cancelled=None, notes: bool = True, reader: PageReader | None = None,
                     log: list | None = None) -> tuple[str, list[tuple], dict]:
    """(title, blocks, counts) of a PDF or image document of the library.
    reader: its scanned pages read again (PageReader), not the library's
    text; pages corrected by hand keep theirs. log: the changes made."""
    import json

    import pymupdf
    doc = lib.document(doc_id)
    if doc is None:
        raise KeyError(f"no document {doc_id}")
    if doc["kind"] not in ("pdf", "image"):
        raise FullTextError(f"{doc['name']} is a {doc['kind']} document")
    path = lib.original_path(doc_id)
    if not path.exists():
        raise FullTextError(f"original file not found: {path}")
    units = lib.units(doc_id)
    by_page = {json.loads(u["locator"] or "{}").get("page", i + 1): u
               for i, u in enumerate(units)}
    pages, counts = [], {"pages": 0, "illustrations": 0, "ocr_pages": 0}
    with pymupdf.open(path) as pdf:
        readings = {}
        if reader is not None:
            scans = [i for i, page in enumerate(pdf, 1)
                     if is_scan(page) and not (by_page.get(i) and by_page[i]["edited"])]
            readings = _read_pages(pdf, scans, reader, illustrations, progress, cancelled,
                                   log if log is not None else [], counts)
        for i, page in enumerate(pdf, 1):
            if cancelled and cancelled():
                raise FullTextError("cancelled")
            if progress and reader is None:      # pages done, before this one
                progress(i - 1, len(pdf))
            u = by_page.get(i)
            if i in readings:
                items = _reading_items(readings[i])
                counts["ocr_pages"] += 1
            elif u is not None and (u["source"] != "native" or u["edited"]):
                boxes = json.loads(u["boxes"]) if u["boxes"] else []
                items = _ocr_items(page, u["text"], boxes, illustrations)
                counts["ocr_pages"] += 1
            else:
                items = _native_items(page, illustrations)
            counts["illustrations"] += sum(1 for it in items if it.kind == "img")
            pages.append(items)
        if progress:                             # every page read: the file is written next
            progress(len(pdf), len(pdf))
    counts["pages"] = len(pages)
    meta = json.loads(doc["meta"] or "{}")
    title = meta.get("title") or Path(doc["name"]).stem
    blocks = assemble(pages, notes)
    counts["notes"] = sum(1 for k, _ in blocks if k == "note")
    return title, blocks, counts


# ---------------------------------------------------------------- EPUB pictures

TEXT_WORDS = 12               # a picture of text has at least this many words,
TEXT_CONF = 55                # read with this mean confidence,
TEXT_COVER = 0.10             # covering this share of the picture (or many words
MANY_WORDS = 40               # covering less: a page of text with a large picture)


@dataclass
class Reading:
    text: str
    words: int
    conf: float | None
    cover: float
    pics: list = field(default_factory=list)      # (fraction from the top, Picture)
    paras: list = field(default_factory=list)     # Items, with a size from the boxes
    boxes: list = field(default_factory=list)     # Tesseract's words (none: another reader)

    @property
    def is_text(self) -> bool:
        return (self.words >= TEXT_WORDS and (self.conf or 0) >= TEXT_CONF
                and (self.cover >= TEXT_COVER
                     or (self.words >= MANY_WORDS and self.cover >= 0.03)))


def _to_png(data: bytes):
    import pymupdf
    pix = pymupdf.Pixmap(data)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n > 3:                             # CMYK
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix


def read_picture(data: bytes, langs: str, tesseract: str | None, illustrations: bool,
                 vision=None) -> Reading:
    """Read a picture: is it text, what does it say, what illustrations does it
    hold. vision(png) -> text replaces Tesseract's text when given."""
    import pymupdf
    pix = _to_png(data)
    png = pix.tobytes("png")
    res = ocr.ocr_image_bytes(png, langs, tesseract)
    area = max(pix.width * pix.height, 1)
    cover = sum((b[2] - b[0]) * (b[3] - b[1]) for b in res.boxes) / area
    r = Reading(res.text, len(res.boxes), res.conf, round(cover, 3))
    if not r.is_text:
        return r
    if vision is not None:
        r.text = vision(png) or r.text
    else:
        r.boxes = res.boxes
    r.paras = [Item("text", t, size, ocr=True)
               for t, size in layout.ocr_paragraphs(r.text, r.boxes)]
    if illustrations:
        # the picture as a page, to look for its illustrations as on a PDF page
        with pymupdf.open(stream=png, filetype="png") as doc:
            page = doc[0]
            k = page.rect.width / pix.width                     # pixels -> points
            words = [(x0 * k, y0 * k, x1 * k, y1 * k) for x0, y0, x1, y1 in text_rects(res.boxes)]
            for top, pic in page_pictures(page, words):
                r.pics.append((top / max(page.rect.height, 1), pic))
    return r


def _lexicon():
    from . import doubt
    return doubt.lexicon()


def _fill(soup, tag, text: str) -> None:
    """The text into tag, its note calls as links to the notes."""
    for piece in layout.split_calls(text):
        if isinstance(piece, int):
            a = soup.new_tag("a", href=f"#iak-n{piece}", id=f"iak-r{piece}")
            a["class"] = "iak-noteref"
            sup = soup.new_tag("sup")
            sup.string = str(piece)
            a.append(sup)
            tag.append(a)
        else:
            tag.append(piece)


def _rename_boxes(r: "Reading", changes: list[dict]) -> None:
    """The kept one-word changes in Tesseract's boxes too, so that the lines
    still follow the text (paragraphs from the geometry)."""
    from . import doubt
    at = doubt.box_index(r.text, r.boxes)
    boxes = [list(b) for b in r.boxes]
    for c in changes:
        k = at.get(c["start"])
        if c["kept"] and k is not None and " " not in c["new"]:
            boxes[k][4] = str(boxes[k][4]).replace(c["word"], c["new"], 1)
    r.boxes = boxes


def corrections_path(out: Path) -> Path:
    return out.with_name(out.stem + ".corrections.tsv")


def _write_log(out: Path, log: list[tuple[str, dict]]) -> None:
    """Every change proposed, kept or refused, to review: the doubtful words
    of a picture, and the book-wide passes (where: "book")."""
    lines = ["where\tword\tcorrection\tkept\twhy\ttimes"]
    for path, c in log:
        why = f"{c['kind']}: {c['why']}" if c.get("kind") else c["why"]
        lines.append("\t".join([path, c["word"], c["new"], "yes" if c["kept"] else "no",
                                why, str(c.get("n", 1))]))
    corrections_path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class Finish:
    """Book-wide finishing of a full-text version (iak.textfix): French
    typography (None: when the book is in French), the long s of old prints,
    the words OCR cut or glued, and the modern spelling of the 17th and 18th
    centuries (iak.modernise)."""
    typography: bool | None = None
    old_print: bool = False
    cut_words: bool = True
    modern: bool = False

    def run(self, texts: list[str], lang: str) -> tuple[list[str], list[dict]]:
        from . import textfix
        typo = self.typography if self.typography is not None else lang.startswith("fr")
        if not (typo or self.old_print or self.cut_words or self.modern) or not texts:
            return texts, []
        return textfix.finish(texts, typo, self.old_print, self.cut_words, _lexicon(),
                              self.modern)


TEXT_KINDS = ("p", "h1", "h2", "li", "quote")


def finish_blocks(blocks: list[tuple], lang: str, finish: Finish) -> tuple[list[tuple], list]:
    """The text blocks and notes of a book through Finish."""
    where = [i for i, (k, _) in enumerate(blocks) if k in TEXT_KINDS or k == "note"]
    texts = [blocks[i][1][1] if blocks[i][0] == "note" else blocks[i][1] for i in where]
    texts, log = finish.run(texts, lang)
    out = list(blocks)
    for i, t in zip(where, texts):
        out[i] = ("note", (out[i][1][0], t)) if out[i][0] == "note" else (out[i][0], t)
    return out, [("book", c) for c in log]


def _is_cover(item, cover_ids) -> bool:
    return (item.get("id") in cover_ids or "cover" in (item.get("properties") or "")
            or "cover" in (item.get("id") or "").lower()
            or "cover" in posixpath.basename(item.get("href") or "").lower())


def epub_document(src: Path, out: Path, langs: str = ocr.DEFAULT_LANGS,
                  tesseract: str | None = None, illustrations: bool = True, vision=None,
                  progress=None, cancelled=None, fix_client=None, focus_client=None,
                  notes: bool = True, finish: Finish | None = None) -> dict:
    """Write out: the EPUB src with its pictures of text replaced by text.

    vision(png) -> text: the vision model reads each whole picture instead
    of Tesseract. Otherwise Tesseract reads them, and its doubtful words
    (iak.doubt, against the book's own vocabulary) are corrected: always
    the l' read as a letter (Lanxiété -> L'anxiété), and with focus_client
    the vision model reads again the lines that hold them (iak.correction),
    with fix_client the text model corrects them in their context. The
    changes, kept and refused, go to <out>.corrections.tsv. notes: the
    notes at the foot of the pages linked to their calls."""
    import zipfile
    from urllib.parse import quote, unquote

    from bs4 import BeautifulSoup

    from .book import _fix_entities, epub_layout, local, parse_xhtml
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        raw = {i.filename: zin.read(i.filename) for i in infos}
        opf, order, _ = epub_layout(zin)
    if not opf:
        raise FullTextError("not an EPUB (no package document)")
    opf_soup = BeautifulSoup(raw[opf], "xml")
    base = posixpath.dirname(opf)
    items = {}
    for it in opf_soup.find_all(lambda t: local(t.name) == "item"):
        if it.get("href"):
            items[posixpath.normpath(posixpath.join(base, unquote(it["href"])))] = it
    cover_ids = {m.get("content") for m in opf_soup.find_all(lambda t: local(t.name) == "meta")
                 if m.get("name") == "cover"}
    counts = {"pictures": 0, "text_pictures": 0, "kept": 0, "illustrations": 0, "words": 0}

    # 1. read every picture of the reading order once
    refs = []                                   # (document, element, picture path)
    soups = {}
    for name in order:
        soup, mode = parse_xhtml(raw[name])
        soups[name] = soup
        for el in soup.find_all(lambda t: local(t.name) in ("img", "image")):
            href = el.get("src") or el.get("xlink:href") or el.get("href")
            if not href or href.startswith("data:"):
                continue
            path = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(href)))
            if path in raw:
                refs.append((name, el, path))
    readings = {}
    targets = [p for p in dict.fromkeys(p for _, _, p in refs)
               if not _is_cover(dict(items[p].attrs) if p in items else {"href": p}, cover_ids)]
    for i, path in enumerate(targets, 1):
        if cancelled and cancelled():
            raise FullTextError("cancelled")
        if progress:
            progress(i, len(targets))
        try:
            readings[path] = read_picture(raw[path], langs, tesseract, illustrations, vision)
        except Exception as exc:  # noqa: BLE001 - an unreadable picture stays as it is
            readings[path] = None
            counts.setdefault("errors", []).append(f"{path}: {exc}")
    counts["pictures"] = len(targets)
    errors = counts.pop("errors", [])
    if errors and len(errors) == len(targets):
        raise FullTextError(f"no picture could be read: {errors[0]}")

    # 1b. the doubtful words of Tesseract's readings, corrected
    log: list[tuple[str, dict]] = []
    text_paths = [p for p in targets if readings.get(p) and readings[p].is_text
                  and readings[p].boxes]
    if text_paths:
        from . import correction, doubt
        vocab = doubt.vocabulary_from_boxes(readings[p].boxes for p in text_paths)
        seen = correction.attested(readings[p].text for p in text_paths)
        for i, path in enumerate(text_paths, 1):
            if cancelled and cancelled():
                raise FullTextError("cancelled")
            r = readings[path]
            suspects = doubt.page(r.text, r.boxes, vocab)["suspects"]
            if not suspects:
                continue
            if progress and (fix_client or focus_client):
                progress(i, len(text_paths))
            proposals = {}
            try:
                if focus_client is not None:
                    crop = correction.picture_cropper(_to_png(raw[path]).tobytes("png"))
                    proposals, _ = correction.focus_proposals(focus_client, r.text, suspects,
                                                              r.boxes, crop)
                elif fix_client is not None:
                    proposals, _ = correction.fix_proposals(fix_client, r.text, suspects, vocab)
            except Exception as exc:  # noqa: BLE001 - the page keeps Tesseract's words
                errors.append(f"{path}: {exc}")
            proposals.update(correction.elisions(suspects, vocab, seen))
            new, changes = correction.apply_changes(r.text, suspects, proposals, vocab,
                                                    loose=focus_client is not None)
            if new != r.text:
                _rename_boxes(r, changes)
                r.text = new
                r.paras = [Item("text", t, size, ocr=True)
                           for t, size in layout.ocr_paragraphs(r.text, r.boxes)]
            log += [(path, c) for c in changes]
        counts["corrected"] = sum(c["kept"] for _, c in log)
        counts["refused"] = sum(not c["kept"] for _, c in log)
    if errors:
        counts["errors"] = len(errors)
        counts["error"] = errors[0]

    # running heads and page numbers of the scanned pages
    def norm(t):
        return re.sub(r"\d+", "#", t.lower()).strip()
    texts = {p: [it.text for it in r.paras] for p, r in readings.items() if r and r.is_text}
    sizes = sorted(it.size for r in readings.values() if r and r.is_text
                   for it in r.paras if it.size)
    body = sizes[len(sizes) // 2] if sizes else None
    cnt = Counter()
    for paras in texts.values():
        cnt.update({norm(t) for t in paras[:1] + paras[-1:] if len(t) < 100})
    running = {k for k, v in cnt.items() if v >= 3 and len(texts) >= 4}

    # notes at the foot of the pages, linked to their calls
    known = layout.words(it.text for r in readings.values() if r and r.is_text
                         for it in r.paras)
    page_notes: dict[str, list] = {}
    n_notes = 0
    if notes:
        for path in targets:
            r = readings.get(path)
            if not (r and r.is_text):
                continue
            r.paras, found = _page_notes(r.paras, body)
            texts = [it.text for it in r.paras]
            for mark, text in found:
                n_notes += 1
                layout.link_call(texts, mark, n_notes, known, _lexicon())
                page_notes.setdefault(path, []).append((n_notes, text))
            for it, t in zip(r.paras, texts):
                it.text = t
    counts["notes"] = n_notes
    doc_notes: dict[str, list] = {}

    # the book-wide finishing: typography, long s, cut and glued words
    lang_tag = opf_soup.find(lambda t: local(t.name) == "language")
    lang = lang_tag.get_text().strip().lower() if lang_tag else ""
    slots = [(r, it) for p in targets for r in [readings.get(p)] if r and r.is_text
             for it in r.paras]
    note_slots = [(p, k) for p in page_notes for k in range(len(page_notes[p]))]
    texts = [it.text for _, it in slots] + [page_notes[p][k][1] for p, k in note_slots]
    texts, more = (finish or Finish()).run(texts, lang)
    for (_, it), t in zip(slots, texts):
        it.text = t
    for (p, k), t in zip(note_slots, texts[len(slots):]):
        page_notes[p][k] = (page_notes[p][k][0], t)
    log += [("book", c) for c in more]
    counts["finished"] = sum(c["kept"] for c in more if c.get("kind") != "typography")

    # 2. replace each picture of text where it is
    new_files: dict[str, bookformats.Picture] = {}
    n_new = 0
    for name, el, path in refs:
        r = readings.get(path)
        if r is None:
            continue
        if not r.is_text:
            counts["kept"] += 1
            continue
        soup = soups[name]
        paras = [it for it in r.paras
                 if not (re.fullmatch(r"[\d\s\-–—.|]+|[ivxlcdm]{1,6}\.?", it.text, re.I)
                         or norm(it.text) in running)]
        nodes = []
        total = sum(len(it.text) for it in paras) or 1
        pics = list(r.pics)
        cum = 0
        for it in paras:
            while pics and pics[0][0] <= cum / total:
                nodes.append(("img", pics.pop(0)[1]))
            heading = body and it.size and it.size >= body * 1.3 and len(it.text) < 150
            nodes.append(("h2" if heading else "p", it.text))
            cum += len(it.text)
        nodes += [("img", p) for _, p in pics]
        new = []
        for kind, value in nodes:
            if kind in ("p", "h2"):
                p = soup.new_tag(kind)
                _fill(soup, p, value)
                new.append(p)
                counts["words"] += len(layout.strip_calls(value, lambda n: "").split())
            else:
                n_new += 1
                file = posixpath.join(posixpath.dirname(path), f"iak-ft-{n_new}.{value.ext}")
                new_files[file] = value
                fig = soup.new_tag("div")
                fig["class"] = "iak-illustration"
                img = soup.new_tag("img")
                img["src"] = quote(posixpath.relpath(file, posixpath.dirname(name)))
                img["alt"] = ""
                fig.append(img)
                new.append(fig)
                counts["illustrations"] += 1
        # the element to replace: the picture's own wrapper (svg, p, div...)
        # when it holds nothing else
        target = el
        while target.parent is not None and local(target.parent.name) not in ("body", "section"):
            parent = target.parent
            others = [c for c in parent.contents if c is not target
                      and (getattr(c, "name", None) or str(c).strip())]
            if others:
                break
            target = parent
        for node in new:
            target.insert_before(node)
        target.decompose()
        counts["text_pictures"] += 1
        doc_notes.setdefault(name, []).extend(page_notes.pop(path, []))

    # the notes of each document at its end, linked both ways
    for name, ns in doc_notes.items():
        if not ns:
            continue
        soup = soups[name]
        body_tag = soup.find(lambda t: local(t.name) == "body")
        box = soup.new_tag("div")
        box["class"] = "iak-notes"
        for n, text in ns:
            p = soup.new_tag("p")
            p["id"] = f"iak-n{n}"
            back = soup.new_tag("a", href=f"#iak-r{n}")
            back.string = str(n)
            p.append(back)
            p.append(". ")
            _fill(soup, p, text)
            box.append(p)
        body_tag.append(box)

    # 3. pictures no longer used leave the book; the new ones join the manifest
    used = set()
    for name, soup in soups.items():
        for el in soup.find_all(lambda t: local(t.name) in ("img", "image")):
            href = el.get("src") or el.get("xlink:href") or el.get("href") or ""
            used.add(posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(href))))
    removed = {p for p in readings if p not in used and readings[p] is not None
               and readings[p].is_text}
    for p in removed:
        if p in items:
            items[p].decompose()
    manifest = opf_soup.find(lambda t: local(t.name) == "manifest")
    for i, file in enumerate(new_files, 1):
        tag = opf_soup.new_tag("item")
        tag["id"] = f"iak-ft-{i}"
        tag["href"] = quote(posixpath.relpath(file, base))
        tag["media-type"] = new_files[file].mime
        manifest.append(tag)

    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w") as zout:
        zout.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for info in infos:
            n = info.filename
            if n == "mimetype" or n in removed:
                continue
            if n in soups:
                data = str(soups[n]).encode("utf-8")
            elif n == opf:
                data = str(opf_soup).encode("utf-8")
            else:
                data = raw[n]
            zout.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        for file, pic in new_files.items():
            zout.writestr(file, pic.data, compress_type=zipfile.ZIP_STORED)
    tmp.replace(out)
    corrections_path(out).unlink(missing_ok=True)
    if log:
        _write_log(out, log)
    counts["size_before"] = src.stat().st_size
    counts["size_after"] = out.stat().st_size
    return counts


# ---------------------------------------------------------------- library

FORMATS = ("epub", "pdf", "html", "docx", "rtf", "md", "gutenberg")


def output_dir(lib, doc_id: int) -> Path:
    return lib.folder / "fulltext" / str(doc_id)


def outputs(lib, doc_id: int) -> list[dict]:
    """The full-text versions made of a document, newest first."""
    folder = output_dir(lib, doc_id)
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".part")]
    return [{"name": p.name, "size": p.stat().st_size, "created": p.stat().st_mtime,
             "format": p.suffix.lstrip(".")}
            for p in sorted(files, key=lambda p: -p.stat().st_mtime)]


def convert(lib, doc_id: int, fmt: str = "epub", illustrations: bool = True,
            method: str | None = None, vision=None, progress=None, cancelled=None,
            notes: bool = True, fix_client=None, focus_client=None,
            finish: Finish | None = None) -> dict:
    """Make the full-text version of a library document; returns its file
    name and counts. EPUB documents stay EPUB; method "vision" reads their
    pictures with vision(png) -> text. For a PDF, method "library" takes the
    library's text; "ocr", "focus", "fix", "vision" read its scanned pages
    again (PageReader)."""
    import json
    doc = lib.document(doc_id)
    if doc is None:
        raise KeyError(f"no document {doc_id}")
    stem = Path(doc["name"]).stem
    folder = output_dir(lib, doc_id)
    folder.mkdir(parents=True, exist_ok=True)
    if doc["kind"] == "epub":
        method = "ocr" if method in (None, "library") else method
        src = lib.original_path(doc_id)
        if not src.exists():
            raise FullTextError(f"original file not found: {src}")
        out = folder / f"{stem}.texte.epub"
        counts = epub_document(src, out, lib.opts.langs, lib.opts.tesseract, illustrations,
                               vision if method == "vision" else None, progress, cancelled,
                               fix_client if method == "fix" else None,
                               focus_client if method == "focus" else None, notes, finish)
        return {"file": out.name, "format": "epub", **counts}
    if doc["kind"] not in ("pdf", "image"):
        raise FullTextError(f"{doc['name']} is already text ({doc['kind']})")
    if fmt not in FORMATS:
        raise FullTextError(f"format is one of {', '.join(FORMATS)}")
    reader = None
    if method not in (None, "library") and doc["kind"] == "pdf":
        reader = PageReader(lib.opts.langs, lib.opts.tesseract,
                            vision if method == "vision" else None,
                            focus_client if method == "focus" else None,
                            fix_client if method == "fix" else None)
    log: list = []
    title, blocks, counts = library_document(lib, doc_id, illustrations, progress, cancelled,
                                             notes, reader, log)
    if not any(k not in ("img", "note") for k, _ in blocks):
        raise FullTextError("no text in this document: read it with OCR (re-extract) first")
    lang = (json.loads(doc["meta"] or "{}").get("language") or "").split("-")[0] or \
        _guess_lang(blocks)
    # Project Gutenberg: a zip of the text and the HTML
    out = folder / (f"{stem}.gutenberg.zip" if fmt == "gutenberg" else f"{stem}.texte.{fmt}")
    blocks, more = finish_blocks(blocks, lang, finish or Finish())
    if progress:
        progress(1, 1)                           # the file is written next
    bookformats.write(blocks, fmt, title, lang, out)
    corrections_path(out).unlink(missing_ok=True)
    log += more
    if log:
        _write_log(out, log)
    if more:
        counts["finished"] = sum(c["kept"] for _, c in more if c.get("kind") != "typography")
    return {"file": out.name, "format": fmt, **counts,
            "size_before": lib.original_path(doc_id).stat().st_size,
            "size_after": out.stat().st_size}


def _guess_lang(blocks) -> str:
    from . import translate
    sample = " ".join(t for k, t in blocks if k not in ("img", "note"))[:20000]
    return translate.detect_language(sample) or "fr"
