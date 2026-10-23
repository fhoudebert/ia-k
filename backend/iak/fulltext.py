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

from . import bookformats, ocr
from .book import BookError, _text_blocks, join_lines

GRID = 60                     # cells across a page for the illustration search
MIN_AREA = 0.012              # an illustration covers at least this share of the page
RENDER_DPI = 100              # page rendered to look for illustrations
CROP_DPI = 150                # resolution of the illustrations kept
JPEG_QUALITY = 82
SCAN_COVER = 0.7              # a picture covering this much of a page is the scan itself
_END = re.compile(r"[.!?:;»\"”)\]…]$")


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
    """Text blocks with their font size, and pictures, in reading order."""
    entries = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") != 0:
            continue
        text, weight, nch = "", 0.0, 0
        for line in b.get("lines", []):
            lt = re.sub(r"\s+", " ", "".join(s.get("text", "") for s in line.get("spans", []))).strip()
            for s in line.get("spans", []):
                n = len(s.get("text", "").strip())
                weight += s.get("size", 0) * n
                nch += n
            if lt:
                text = join_lines(text, lt)
        if text:
            entries.append((b["bbox"][1], Item("text", text, round(weight / max(nch, 1), 1))))
    if illustrations:
        words = text_rects([list(w[:4]) + [w[4], None] for w in page.get_text("words")])
        for top, pic in page_pictures(page, words):
            entries.append((top, Item("img", pic=pic)))
    entries.sort(key=lambda e: e[0])
    return [it for _, it in entries]


def _sizes_from_boxes(paras: list[Item], boxes) -> None:
    """A font size for OCR paragraphs, from the height of their words' boxes
    (so that headings stand out as on a native page). The boxes follow the
    text word for word unless it was corrected: then no size is given."""
    words = [b for b in boxes if str(b[4]).strip()]
    counts = [len(p.text.split()) for p in paras]
    if not words or sum(counts) != len(words):
        return
    i = 0
    for p, n in zip(paras, counts):
        heights = sorted(b[3] - b[1] for b in words[i:i + n])
        i += n
        if heights:
            # the median word, rounded: capitals and descenders even out
            p.size = round(heights[len(heights) // 2] * 0.75, 0)


def _ocr_items(page, text: str, boxes, illustrations: bool) -> list[Item]:
    """The library's text of a page (OCR, corrected...) as paragraphs, and its
    illustrations placed where they are on the page."""
    paras = [Item("text", t) for t in _text_blocks(text)]
    _sizes_from_boxes(paras, boxes or [])
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


def assemble(pages: list[list[Item]]) -> list[tuple]:
    """Blocks for bookformats: headings from the font sizes, running heads and
    page numbers removed, paragraphs joined across pages."""
    sizes = Counter()
    for items in pages:
        for it in items:
            if it.kind == "text" and it.size:
                sizes[it.size] += len(it.text)
    body = sizes.most_common(1)[0][0] if sizes else None

    def norm(t):
        return re.sub(r"\d+", "#", t.lower()).strip()
    running = set()
    if len(pages) >= 4:
        cnt = Counter()
        for items in pages:
            texts = [it.text for it in items if it.kind == "text"]
            cnt.update({norm(t) for t in texts[:2] + texts[-2:] if len(t) < 120})
        running = {k for k, v in cnt.items() if v >= max(3, 0.3 * len(pages))}
    blocks: list[list] = []
    for items in pages:
        for it in items:
            if it.kind == "img":
                blocks.append(["img", it.pic])
                continue
            t = it.text
            if re.fullmatch(r"[\d\s\-–—.|]+|(page|p\.)\s*\d+|[ivxlcdm]{1,6}\.?", t, re.I) \
                    or norm(t) in running:
                continue
            kind = "p"
            if body and it.size and it.size >= body * 1.6 and len(t) < 150:
                kind = "h1"
            elif body and it.size and it.size >= body * 1.2 and len(t) < 200:
                kind = "h2"
            prev = next((b for b in reversed(blocks) if b[0] != "img"), None)
            if (kind == "p" and prev is not None and prev[0] == "p"
                    and not _END.search(prev[1]) and t[:1].islower()):
                prev[1] = join_lines(prev[1], t)     # continued over a page break
                continue
            blocks.append([kind, t])
    return [tuple(b) for b in blocks]


def library_document(lib, doc_id: int, illustrations: bool = True, progress=None,
                     cancelled=None) -> tuple[str, list[tuple], dict]:
    """(title, blocks, counts) of a PDF or image document of the library."""
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
        for i, page in enumerate(pdf, 1):
            if cancelled and cancelled():
                raise FullTextError("cancelled")
            if progress:                         # pages done, before this one
                progress(i - 1, len(pdf))
            u = by_page.get(i)
            if u is not None and (u["source"] != "native" or u["edited"]):
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
    return title, assemble(pages), counts


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
    r.paras = [Item("text", t) for t in _text_blocks(r.text)]
    _sizes_from_boxes(r.paras, res.boxes)
    if illustrations:
        # the picture as a page, to look for its illustrations as on a PDF page
        with pymupdf.open(stream=png, filetype="png") as doc:
            page = doc[0]
            k = page.rect.width / pix.width                     # pixels -> points
            words = [(x0 * k, y0 * k, x1 * k, y1 * k) for x0, y0, x1, y1 in text_rects(res.boxes)]
            for top, pic in page_pictures(page, words):
                r.pics.append((top / max(page.rect.height, 1), pic))
    return r


def _is_cover(item, cover_ids) -> bool:
    return (item.get("id") in cover_ids or "cover" in (item.get("properties") or "")
            or "cover" in (item.get("id") or "").lower()
            or "cover" in posixpath.basename(item.get("href") or "").lower())


def epub_document(src: Path, out: Path, langs: str = ocr.DEFAULT_LANGS,
                  tesseract: str | None = None, illustrations: bool = True, vision=None,
                  progress=None, cancelled=None) -> dict:
    """Write out: the EPUB src with its pictures of text replaced by text."""
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
                p.string = value
                new.append(p)
                counts["words"] += len(value.split())
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
    counts["size_before"] = src.stat().st_size
    counts["size_after"] = out.stat().st_size
    return counts


# ---------------------------------------------------------------- library

FORMATS = ("epub", "pdf", "html", "docx", "rtf", "md")


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
            method: str = "ocr", vision=None, progress=None, cancelled=None) -> dict:
    """Make the full-text version of a library document; returns its file
    name and counts. EPUB documents stay EPUB; method "vision" reads their
    pictures with vision(png) -> text."""
    import json
    doc = lib.document(doc_id)
    if doc is None:
        raise KeyError(f"no document {doc_id}")
    stem = Path(doc["name"]).stem
    folder = output_dir(lib, doc_id)
    folder.mkdir(parents=True, exist_ok=True)
    if doc["kind"] == "epub":
        src = lib.original_path(doc_id)
        if not src.exists():
            raise FullTextError(f"original file not found: {src}")
        out = folder / f"{stem}.texte.epub"
        counts = epub_document(src, out, lib.opts.langs, lib.opts.tesseract, illustrations,
                               vision if method == "vision" else None, progress, cancelled)
        return {"file": out.name, "format": "epub", **counts}
    if doc["kind"] not in ("pdf", "image"):
        raise FullTextError(f"{doc['name']} is already text ({doc['kind']})")
    if fmt not in FORMATS:
        raise FullTextError(f"format is one of {', '.join(FORMATS)}")
    title, blocks, counts = library_document(lib, doc_id, illustrations, progress, cancelled)
    if not any(k != "img" for k, _ in blocks):
        raise FullTextError("no text in this document: read it with OCR (re-extract) first")
    lang = (json.loads(doc["meta"] or "{}").get("language") or "").split("-")[0] or \
        _guess_lang(blocks)
    out = folder / f"{stem}.texte.{fmt}"
    bookformats.write(blocks, fmt, title, lang, out)
    return {"file": out.name, "format": fmt, **counts,
            "size_before": lib.original_path(doc_id).stat().st_size,
            "size_after": out.stat().st_size}


def _guess_lang(blocks) -> str:
    from . import translate
    sample = " ".join(t for k, t in blocks if k != "img")[:20000]
    return translate.detect_language(sample) or "fr"
