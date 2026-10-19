"""Text extraction, one function per document kind.

Each extractor returns an Extraction: document metadata and a list of Units,
the citable parts of the document (pages, sections, blocks of lines).
Scanned PDF pages and images go through Tesseract; word boxes are kept, in
points for PDF pages and in pixels for images, so that a passage can later be
highlighted on the original.
"""
from __future__ import annotations

import html
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from . import ocr as tess

KINDS = {
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image",
    ".tiff": "image", ".bmp": "image", ".webp": "image",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "md", ".markdown": "md",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".epub": "epub",
}

# below this many characters of native text, a PDF page is treated as scanned
MIN_NATIVE_CHARS = 40
OCR_DPI = 300
TXT_UNIT_CHARS = 4000


class ExtractError(RuntimeError):
    pass


@dataclass
class Unit:
    label: str
    locator: dict
    text: str
    source: str = "native"            # native | ocr
    ocr_conf: float | None = None
    width: float | None = None
    height: float | None = None
    boxes: list | None = None


@dataclass
class Extraction:
    meta: dict = field(default_factory=dict)
    units: list[Unit] = field(default_factory=list)


@dataclass
class Options:
    ocr: bool = True
    langs: str = tess.DEFAULT_LANGS
    tesseract: str | None = None


def kind_of(path: str | Path) -> str | None:
    return KINDS.get(Path(path).suffix.lower())


def extract(path: str | Path, opts: Options | None = None) -> Extraction:
    opts = opts or Options()
    kind = kind_of(path)
    if kind is None:
        raise ExtractError(f"unsupported file type: {Path(path).suffix or path}")
    return {
        "pdf": extract_pdf, "image": extract_image, "docx": extract_docx,
        "txt": extract_txt, "md": extract_md, "html": extract_html,
        "epub": extract_epub,
    }[kind](Path(path), opts)


# --------------------------------------------------------------------------
# PDF and images (PyMuPDF)

def _pymupdf():
    try:
        import pymupdf
    except ImportError as exc:
        raise ExtractError("PyMuPDF is not installed (pip install pymupdf)") from exc
    return pymupdf


def _ocr_page(page, opts: Options, zoom: float) -> Unit:
    """Render a page and OCR it; boxes are converted back to page units."""
    pix = page.get_pixmap(matrix=_pymupdf().Matrix(zoom, zoom))
    res = tess.ocr_image_bytes(pix.tobytes("png"), opts.langs, opts.tesseract)
    boxes = [[round(b[0] / zoom, 1), round(b[1] / zoom, 1), round(b[2] / zoom, 1),
              round(b[3] / zoom, 1), b[4], b[5]] for b in res.boxes]
    return Unit(label="", locator={}, text=res.text, source="ocr", ocr_conf=res.conf,
                boxes=boxes)


def extract_pdf(path: Path, opts: Options) -> Extraction:
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # pymupdf raises its own types
        raise ExtractError(f"cannot open PDF: {exc}") from exc
    with doc:
        if doc.needs_pass:
            raise ExtractError("the PDF is password-protected")
        meta = {k: v for k, v in (doc.metadata or {}).items() if v and k in
                ("title", "author", "subject", "keywords", "creationDate")}
        meta["pages"] = doc.page_count
        out = Extraction(meta=meta)
        for i, page in enumerate(doc):
            label = page.get_label() if hasattr(page, "get_label") else ""
            label = f"p. {label or i + 1}"
            text = page.get_text("text")
            if len(text.strip()) >= MIN_NATIVE_CHARS or not opts.ocr:
                words = page.get_text("words")
                unit = Unit(label=label, locator={"page": i + 1}, text=text.strip(),
                            boxes=[[round(w[0], 1), round(w[1], 1), round(w[2], 1),
                                    round(w[3], 1), w[4], None] for w in words])
            else:
                try:
                    unit = _ocr_page(page, opts, OCR_DPI / 72)
                except tess.OcrError as exc:
                    # keep the native pages usable; this page stays empty
                    meta.setdefault("warnings", [])
                    if not meta["warnings"]:
                        meta["warnings"].append(f"OCR unavailable: {exc}")
                    unit = Unit(label="", locator={}, text=text.strip(), source="ocr")
                unit.label, unit.locator = label, {"page": i + 1}
            unit.width, unit.height = round(page.rect.width, 1), round(page.rect.height, 1)
            out.units.append(unit)
    return out


def extract_image(path: Path, opts: Options) -> Extraction:
    """PNG, JPEG, TIFF (multi-page too) and screenshots."""
    pymupdf = _pymupdf()
    if not opts.ocr:
        raise ExtractError("images need OCR, which is turned off")
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ExtractError(f"cannot open image: {exc}") from exc
    with doc:
        out = Extraction(meta={"pages": doc.page_count})
        for i, page in enumerate(doc):
            w, h = page.rect.width, page.rect.height
            # Screenshots have small text: Tesseract reads better when enlarged.
            zoom = 2.0 if max(w, h) < 2000 else 1.0
            unit = _ocr_page(page, opts, zoom)
            unit.label = f"p. {i + 1}" if doc.page_count > 1 else "image"
            unit.locator = {"page": i + 1}
            unit.width, unit.height = round(w, 1), round(h, 1)
            out.units.append(unit)
    return out


# --------------------------------------------------------------------------
# DOCX

def extract_docx(path: Path, opts: Options) -> Extraction:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ExtractError("python-docx is not installed (pip install python-docx)") from exc
    try:
        d = docx.Document(str(path))
    except Exception as exc:
        raise ExtractError(f"cannot open DOCX: {exc}") from exc
    props = d.core_properties
    meta = {k: v for k, v in (("title", props.title), ("author", props.author)) if v}
    sections: list[tuple[str, list[str]]] = [("", [])]
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if re.match(r"(Heading|Titre|Title)\b", style, re.I):
                sections.append((text, [text]))
            else:
                sections[-1][1].append(text)
        elif tag == "tbl":
            rows = []
            for row in Table(child, d).rows:
                cells = [c.text.strip() for c in row.cells]
                rows.append(" | ".join(cells))
            if rows:
                sections[-1][1].append("\n".join(rows))
    out = Extraction(meta=meta)
    for heading, paras in sections:
        if not paras:
            continue
        n = len(out.units) + 1
        out.units.append(Unit(label=_short(heading) or f"§ {n}",
                              locator={"section": n, "heading": heading},
                              text="\n\n".join(paras)))
    return out


# --------------------------------------------------------------------------
# TXT and Markdown

def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1").replace("\r\n", "\n")


def _blocks_by_size(text: str, limit: int = TXT_UNIT_CHARS) -> list[tuple[int, str]]:
    """Cut text into blocks of whole paragraphs; return (first line number, text)."""
    blocks: list[tuple[int, str]] = []
    cur: list[str] = []
    cur_len = 0
    start_line = line = 1
    for para in re.split(r"(\n\s*\n)", text):
        if cur_len + len(para) > limit and cur_len > 0 and para.strip():
            blocks.append((start_line, "".join(cur).strip()))
            cur, cur_len, start_line = [], 0, line
        cur.append(para)
        cur_len += len(para)
        line += para.count("\n")
    if "".join(cur).strip():
        blocks.append((start_line, "".join(cur).strip()))
    return blocks


def extract_txt(path: Path, opts: Options) -> Extraction:
    text = read_text_file(path)
    out = Extraction()
    for start, block in _blocks_by_size(text):
        out.units.append(Unit(label=f"l. {start}", locator={"line": start}, text=block))
    return out


def extract_md(path: Path, opts: Options) -> Extraction:
    text = read_text_file(path)
    heads = list(re.finditer(r"^(#{1,3})\s+(.+?)\s*#*\s*$", text, re.M))
    if not heads:
        return extract_txt(path, opts)
    out = Extraction()
    cuts = [0] + [m.start() for m in heads] + [len(text)]
    for a, b in zip(cuts, cuts[1:]):
        block = text[a:b].strip()
        if not block:
            continue
        line = text.count("\n", 0, a) + 1
        m = re.match(r"#{1,3}\s+(.+?)\s*#*\s*$", block.split("\n", 1)[0])
        heading = m.group(1) if m else ""
        for start, part in _blocks_by_size(block):
            out.units.append(Unit(label=_short(heading) or f"l. {line + start - 1}",
                                  locator={"line": line + start - 1, "heading": heading},
                                  text=part))
    return out


# --------------------------------------------------------------------------
# HTML and EPUB

class _HtmlText(HTMLParser):
    """Collect text by sections, starting a new section at each h1-h3."""
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "blockquote",
             "h1", "h2", "h3", "h4", "h5", "h6", "pre", "table", "ul", "ol", "dd", "dt"}
    SKIP = {"script", "style", "head", "title", "svg", "nav"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[list] = [["", []]]   # [heading, chunks]
        self.skip = 0
        self.heading: list[str] | None = None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
            return
        if tag in ("h1", "h2", "h3"):
            self.heading = []
            self.sections.append(["", []])
        if tag in self.BLOCK:
            self.sections[-1][1].append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in ("h1", "h2", "h3") and self.heading is not None:
            self.sections[-1][0] = " ".join("".join(self.heading).split())
            self.heading = None
        if tag in self.BLOCK:
            self.sections[-1][1].append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip:
            return
        self.sections[-1][1].append(data)
        if self.heading is not None:
            self.heading.append(data)

    def result(self) -> list[tuple[str, str]]:
        out = []
        for heading, chunks in self.sections:
            text = "".join(chunks)
            text = re.sub(r"[ \t ]+", " ", text)
            text = re.sub(r" *\n *", "\n", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                out.append((heading, text))
        return out


def html_sections(markup: str) -> tuple[str, list[tuple[str, str]]]:
    p = _HtmlText()
    p.feed(markup)
    p.close()
    return " ".join(p.title.split()), p.result()


def _decode_markup(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def extract_html(path: Path, opts: Options) -> Extraction:
    title, sections = html_sections(_decode_markup(path.read_bytes()))
    out = Extraction(meta={"title": title} if title else {})
    for i, (heading, text) in enumerate(sections, 1):
        for start, part in _blocks_by_size(text):
            out.units.append(Unit(label=_short(heading) or f"§ {i}",
                                  locator={"section": i, "heading": heading},
                                  text=part))
    return out


_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container",
       "opf": "http://www.idpf.org/2007/opf",
       "dc": "http://purl.org/dc/elements/1.1/"}


def extract_epub(path: Path, opts: Options) -> Extraction:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ExtractError(f"not a valid EPUB: {exc}") from exc
    with z:
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = container.find(".//c:rootfile", _NS).get("full-path")
            opf = ET.fromstring(z.read(opf_path))
        except (KeyError, AttributeError, ET.ParseError) as exc:
            raise ExtractError(f"EPUB without a readable package file: {exc}") from exc
        base = posixpath.dirname(opf_path)
        meta = {}
        for key, tag in (("title", "dc:title"), ("author", "dc:creator"),
                         ("language", "dc:language")):
            el = opf.find(f".//{tag}", _NS)
            if el is not None and (el.text or "").strip():
                meta[key] = el.text.strip()
        manifest = {it.get("id"): it.get("href")
                    for it in opf.findall(".//opf:manifest/opf:item", _NS)}
        spine = [manifest.get(ref.get("idref"))
                 for ref in opf.findall(".//opf:spine/opf:itemref", _NS)]
        out = Extraction(meta=meta)
        chapter = 0
        for href in spine:
            if not href:
                continue
            name = posixpath.normpath(posixpath.join(base, unquote(href)))
            try:
                markup = _decode_markup(z.read(name))
            except KeyError:
                continue
            _, sections = html_sections(markup)
            text = "\n\n".join(t for _, t in sections)
            if not text.strip():
                continue
            chapter += 1
            heading = next((h for h, _ in sections if h), "")
            for start, part in _blocks_by_size(text):
                out.units.append(Unit(
                    label=_short(heading) or f"chap. {chapter}",
                    locator={"section": chapter, "href": href, "heading": heading},
                    text=part))
    return out


def _short(text: str, n: int = 60) -> str:
    text = " ".join(html.unescape(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
