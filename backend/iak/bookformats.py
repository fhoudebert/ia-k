"""Output formats for books translated from a flat text (a PDF, a scan read
in the library, a text or Markdown file): PDF, EPUB, HTML, DOCX, RTF,
Markdown, text.

The book is a list of blocks (kind, text): h1 and h2 headings, p paragraphs,
li list items, quote, code; ("img", Picture) for an illustration (RTF
and text cannot show them: RTF gets them as pictures, text a mark); and
("note", (n, text)) for note n, whose call is the mark layout.call(n) in a
paragraph. Notes go to the end of the chapter of their call: EPUB 3
footnotes (popups on readers that know them), linked both ways in HTML,
superscripts and a list in PDF, Word and RTF, [^n] in Markdown. EPUB is cut into chapters at h1 headings (or
every 150 blocks), for e-readers; the PDF is laid out on A5 pages, the size
of a reader's screen, with fonts embedded by MuPDF (Latin, Greek, Cyrillic,
Hebrew and CJK scripts; Arabic is not shaped well).
"""
from __future__ import annotations

import base64
import html
import io
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .layout import CALL, split_calls, strip_calls

FORMATS = ("pdf", "epub", "html", "docx", "rtf", "md", "txt")
EXT = {f: "." + f for f in FORMATS}

CSS = """
body { font-family: serif; line-height: 1.45; margin: 0 4%; }
h1 { font-size: 1.5em; margin: 1.2em 0 .6em; text-align: center; }
h2 { font-size: 1.2em; margin: 1em 0 .4em; }
p { margin: 0 0 .5em; text-align: justify; text-indent: 1.2em; }
h1 + p, h2 + p { text-indent: 0; }
blockquote { margin: .5em 1.5em; font-style: italic; }
pre { white-space: pre-wrap; font-family: monospace; font-size: .85em; }
li { margin-bottom: .2em; }
figure { margin: .8em 0; text-align: center; }
figure img { max-width: 100%; height: auto; }
a.noteref { text-decoration: none; }
.notes { margin-top: 1.5em; border-top: 1px solid #999; padding-top: .4em; font-size: .85em; }
.note p, p.note { text-indent: 0; margin: 0 0 .3em; }
"""


@dataclass
class Picture:
    """An illustration: JPEG or PNG bytes and their size in pixels; dpi says
    how big it was on the page (to show it at about that size)."""
    data: bytes
    mime: str = "image/jpeg"
    width: int = 0
    height: int = 0
    dpi: int = 150

    @property
    def ext(self) -> str:
        return "png" if self.mime == "image/png" else "jpg"

    def data_uri(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


def pictures(blocks) -> list[Picture]:
    return [b[1] for b in blocks if b[0] == "img"]


def place_notes(chapters: list[list[tuple]]) -> list[list[tuple]]:
    """Each note at the end of the chapter where its call is (a note without
    a call found stays where it is)."""
    where = {}
    for i, ch in enumerate(chapters):
        for k, t in ch:
            if k != "img" and k != "note" and isinstance(t, str):
                for m in CALL.finditer(t):
                    where.setdefault(int(m.group(1)), i)
    out = [[] for _ in chapters]
    moved = [[] for _ in chapters]
    for i, ch in enumerate(chapters):
        for b in ch:
            if b[0] == "note" and b[1][0] in where:
                moved[where[b[1][0]]].append(b)
            else:
                out[i].append(b)
    return [o + m for o, m in zip(out, moved)]


def arranged(blocks) -> list[tuple]:
    """The blocks with the notes moved to the end of their chapter."""
    if not any(b[0] == "note" for b in blocks):
        return list(blocks)
    return [b for ch in place_notes(_chapters(blocks)) for b in ch]


def _inline(text: str, links: bool, epub: bool) -> str:
    """Escaped text with its note calls as superscripts (linked to the note)."""
    out = []
    for piece in split_calls(text):
        if isinstance(piece, int):
            sup = f"<sup>{piece}</sup>"
            if links:
                kind = ' epub:type="noteref"' if epub else ""
                out.append(f'<a class="noteref"{kind} href="#n{piece}" id="r{piece}">{sup}</a>')
            else:
                out.append(sup)
        else:
            out.append(_esc(piece))
    return "".join(out)


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def html_body(blocks: list[tuple], src=None, width=None, links: bool = True,
              epub: bool = False) -> str:
    """src(picture) gives an image's address (default: a data URI); width(picture)
    an optional CSS width; links: note calls and notes linked to each other;
    epub: EPUB 3 footnotes (epub:type)."""
    out, in_list, in_notes = [], False, False
    src = src or (lambda pic: pic.data_uri())

    def inl(t):
        return _inline(t, links, epub)
    for kind, text in blocks:
        if kind == "li" and not in_list:
            out.append("<ul>")
            in_list = True
        elif kind != "li" and in_list:
            out.append("</ul>")
            in_list = False
        if kind == "note" and not in_notes:
            out.append('<section class="notes">' if not epub else
                       '<section class="notes" epub:type="footnotes">')
            in_notes = True
        elif kind != "note" and in_notes:
            out.append("</section>")
            in_notes = False
        if kind in ("h1", "h2"):
            out.append(f"<{kind}>{inl(text)}</{kind}>")
        elif kind == "li":
            out.append(f"<li>{inl(text)}</li>")
        elif kind == "quote":
            out.append(f"<blockquote><p>{inl(text)}</p></blockquote>")
        elif kind == "code":
            out.append(f"<pre>{_esc(strip_calls(text))}</pre>")
        elif kind == "img":
            style = f' style="width:{width(text)}"' if width else ""
            out.append(f'<figure><img src="{html.escape(src(text))}" alt=""{style}/></figure>')
        elif kind == "note":
            n, body = text
            mark = f'<a href="#r{n}">{n}</a>' if links else str(n)
            if epub:
                out.append(f'<aside class="note" epub:type="footnote" id="n{n}">'
                           f"<p>{mark}. {inl(body)}</p></aside>")
            else:
                out.append(f'<p class="note" id="n{n}">{mark}. {inl(body)}</p>')
        else:
            out.append(f"<p>{inl(text)}</p>")
    if in_list:
        out.append("</ul>")
    if in_notes:
        out.append("</section>")
    return "\n".join(out)


def write_html(blocks, title: str, lang: str, out: Path) -> None:
    out.write_text(
        f'<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{html_body(arranged(blocks))}\n</body>\n</html>\n", encoding="utf-8")


def _chapters(blocks, size: int = 150) -> list[list[tuple[str, str]]]:
    """Chapters at the h1 headings, or at the h2 ones in a book without h1."""
    cut = "h1" if any(b[0] == "h1" for b in blocks) else "h2"
    chapters: list[list] = [[]]
    for b in blocks:
        if (b[0] == cut and chapters[-1]) or len(chapters[-1]) >= size:
            chapters.append([])
        chapters[-1].append(b)
    return [c for c in chapters if c]


def write_epub(blocks, title: str, lang: str, out: Path) -> None:
    """EPUB 3 (with an NCX for older readers)."""
    chapters = place_notes(_chapters(blocks))
    book_id = f"urn:uuid:{uuid.uuid4()}"
    titles = []
    for i, ch in enumerate(chapters, 1):
        head = next((t for k, t in ch if k in ("h1", "h2")), None)
        titles.append(strip_calls(head, lambda n: "") if head else (title if i == 1 else f"{i}"))

    names = {id(p): f"images/i{n}.{p.ext}" for n, p in enumerate(pictures(blocks), 1)}

    def xhtml(i, ch):
        body = html_body(ch, src=lambda pic: names[id(pic)], epub=True)
        return (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" '
                f'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">\n'
                f"<head><title>{_esc(titles[i - 1])}</title>"
                f'<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
                f"<body>\n{body}\n</body>\n</html>\n")
    manifest = "\n".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
                         for i in range(1, len(chapters) + 1))
    manifest += "".join(f'\n<item id="i{n}" href="{name}" media-type="{p.mime}"/>'
                        for n, p in enumerate(pictures(blocks), 1)
                        for name in [names[id(p)]])
    spine = "\n".join(f'<itemref idref="c{i}"/>' for i in range(1, len(chapters) + 1))
    modified = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id" xml:lang="{lang}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="id">{book_id}</dc:identifier>
<dc:title>{_esc(title)}</dc:title>
<dc:language>{lang}</dc:language>
<meta property="dcterms:modified">{modified}</meta>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="css" href="style.css" media-type="text/css"/>
{manifest}
</manifest>
<spine toc="ncx">
{spine}
</spine>
</package>
"""
    items = "\n".join(f'<li><a href="c{i}.xhtml">{_esc(t)}</a></li>'
                      for i, t in enumerate(titles, 1))
    nav = (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
           f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
           f'xml:lang="{lang}" lang="{lang}">\n<head><title>{_esc(title)}</title></head>\n'
           f'<body><nav epub:type="toc" id="toc"><h1>{_esc(title)}</h1><ol>\n{items}\n</ol></nav>'
           f"</body>\n</html>\n")
    points = "\n".join(f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{_esc(t)}</text>'
                       f'</navLabel><content src="c{i}.xhtml"/></navPoint>'
                       for i, t in enumerate(titles, 1))
    ncx = (f'<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
           f'version="2005-1"><head><meta name="dtb:uid" content="{book_id}"/></head>'
           f"<docTitle><text>{_esc(title)}</text></docTitle><navMap>\n{points}\n</navMap></ncx>\n")
    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?>\n<container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>\n',
                   compress_type=zipfile.ZIP_DEFLATED)
        for name, data in (("content.opf", opf), ("nav.xhtml", nav), ("toc.ncx", ncx),
                           ("style.css", CSS)):
            z.writestr(f"OEBPS/{name}", data, compress_type=zipfile.ZIP_DEFLATED)
        for i, ch in enumerate(chapters, 1):
            z.writestr(f"OEBPS/c{i}.xhtml", xhtml(i, ch), compress_type=zipfile.ZIP_DEFLATED)
        for p in pictures(blocks):                 # already compressed
            z.writestr(f"OEBPS/{names[id(p)]}", p.data, compress_type=zipfile.ZIP_STORED)
    tmp.replace(out)


def write_pdf(blocks, title: str, lang: str, out: Path) -> None:
    """A5 pages laid out by MuPDF from the HTML, page numbers at the foot."""
    import pymupdf
    css = CSS + "body { font-size: 11pt; margin: 0; } p { margin-bottom: .35em; }"
    page = pymupdf.paper_rect("a5")
    where = page + (42, 46, -42, -50)
    archive = pymupdf.Archive()
    names = {}
    for n, p in enumerate(pictures(blocks), 1):
        names[id(p)] = f"i{n}.{p.ext}"
        archive.add(p.data, names[id(p)])

    def width(pic):                 # about its size on the page, within the margins
        pt = pic.width / max(pic.dpi, 1) * 72 if pic.width else where.width
        return f"{min(pt, where.width):.0f}pt"
    # MuPDF lays out the notes as superscripts and a list, without links
    body = html_body(arranged(blocks), src=lambda pic: names[id(pic)], width=width, links=False)
    story = pymupdf.Story(html=f'<body lang="{lang}">{body}</body>', user_css=css,
                          archive=archive)
    # laid out in memory, then saved once: on Windows a temporary file is still
    # held by MuPDF when it should be deleted (WinError 32)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    more, n = 1, 0
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        n += 1
        if n > 20000:                      # a layout that never ends
            break
    writer.close()
    # page numbers and the document's title and language
    with pymupdf.open(stream=buf.getvalue(), filetype="pdf") as doc:
        doc.set_metadata({"title": title, "producer": "IA-K"})
        for i, p in enumerate(doc, 1):
            p.insert_textbox(pymupdf.Rect(0, page.height - 36, page.width, page.height - 20),
                             str(i), fontsize=8, align=1, color=(0.35, 0.35, 0.35))
        data = doc.tobytes(garbage=3, deflate=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(out)


def write_docx(blocks, title: str, lang: str, out: Path) -> None:
    import docx
    d = docx.Document()
    d.core_properties.title = title
    d.core_properties.language = lang
    from docx.shared import Pt

    def runs(par, text):                   # note calls as superscripts
        for piece in split_calls(text):
            if isinstance(piece, int):
                par.add_run(str(piece)).font.superscript = True
            else:
                par.add_run(piece)
        return par
    for kind, text in arranged(blocks):
        if kind == "h1":
            d.add_heading(strip_calls(text, lambda n: ""), 1)
        elif kind == "h2":
            d.add_heading(strip_calls(text, lambda n: ""), 2)
        elif kind == "li":
            runs(d.add_paragraph(style="List Bullet"), text)
        elif kind == "quote":
            runs(d.add_paragraph(style="Quote"), text)
        elif kind == "note":
            n, body = text
            par = d.add_paragraph()
            par.add_run(f"{n}. ").font.superscript = False
            runs(par, body)
            for run in par.runs:
                run.font.size = Pt(9)
        elif kind == "img":
            from docx.shared import Inches
            inches = min(6.0, text.width / max(text.dpi, 1)) if text.width else 5.0
            try:
                d.add_picture(io.BytesIO(text.data), width=Inches(inches))
            except Exception:  # noqa: BLE001 - a picture python-docx cannot read
                d.add_paragraph("[illustration]")
        else:
            runs(d.add_paragraph(), text)
    d.save(str(out))


def _rtf(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:                                  # RTF wants 16-bit signed code units
            data = ch.encode("utf-16-le")
            for j in range(0, len(data), 2):
                v = int.from_bytes(data[j:j + 2], "little")
                out.append(f"\\u{v - 65536 if v > 32767 else v}?")
    return "".join(out)


def _rtf_calls(text: str) -> str:
    return "".join(f"{{\\super {p}}}" if isinstance(p, int) else _rtf(p)
                   for p in split_calls(text))


def write_rtf(blocks, title: str, lang: str, out: Path) -> None:
    lines = ["{\\rtf1\\ansi\\ansicpg1252\\deff0",
             "{\\fonttbl{\\f0\\froman Times New Roman;}{\\f1\\fmodern Courier New;}}",
             f"{{\\info{{\\title {_rtf(title)}}}}}", "\\fs24"]
    for kind, text in arranged(blocks):
        if kind == "note":
            n, body = text
            lines.append(f"{{\\pard\\sa60\\fs18 {n}. {_rtf_calls(body)}\\par}}")
            continue
        if kind == "img":
            blip = "\\pngblip" if text.mime == "image/png" else "\\jpegblip"
            tw = int(min(6.0, text.width / max(text.dpi, 1)) * 1440) if text.width else 7200
            th = int(tw * text.height / text.width) if text.width else 5400
            lines.append(f"{{\\pard\\qc\\sa120{{\\pict{blip}\\picw{text.width}\\pich{text.height}"
                         f"\\picwgoal{tw}\\pichgoal{th}\n{text.data.hex()}\n}}\\par}}")
            continue
        t = _rtf_calls(text)
        if kind == "h1":
            lines.append(f"{{\\pard\\qc\\sb240\\sa120\\b\\fs32 {t}\\par}}")
        elif kind == "h2":
            lines.append(f"{{\\pard\\sb200\\sa100\\b\\fs28 {t}\\par}}")
        elif kind == "li":
            lines.append(f"{{\\pard\\li360\\fi-240\\sa60 \\bullet\\tab {t}\\par}}")
        elif kind == "quote":
            lines.append(f"{{\\pard\\li567\\ri567\\sa120\\i {t}\\par}}")
        elif kind == "code":
            lines.append(f"{{\\pard\\sa120\\f1\\fs20 {t}\\par}}".replace("\n", "\\line "))
        else:
            lines.append(f"{{\\pard\\qj\\fi340\\sa100 {t}\\par}}")
    lines.append("}")
    out.write_text("\n".join(lines) + "\n", encoding="ascii")


def write_text(blocks, title: str, lang: str, out: Path, markdown: bool) -> None:
    """Markdown: illustrations in a folder <name>_images next to the file."""
    parts, prev_li = [], False
    n_img = 0
    for kind, text in arranged(blocks):
        if kind == "note":
            n, body = text
            parts.append(f"[^{n}]: {strip_calls(body, lambda k: f'[^{k}]')}" if markdown
                         else f"[{n}] {strip_calls(body)}")
            prev_li = False
            continue
        if kind == "img":
            n_img += 1
            if markdown:
                folder = out.with_name(out.stem + "_images")
                folder.mkdir(exist_ok=True)
                (folder / f"i{n_img}.{text.ext}").write_bytes(text.data)
                parts.append(f"![](<{folder.name}/i{n_img}.{text.ext}>)")
            else:
                parts.append("[illustration]")
            prev_li = False
            continue
        text = strip_calls(text, (lambda k: f"[^{k}]") if markdown else (lambda k: f"[{k}]"))
        if markdown:
            line = {"h1": "# ", "h2": "## ", "li": "- ", "quote": "> "}.get(kind, "") + text
            if kind == "code":
                line = text if text.startswith("```") else f"```\n{text}\n```"
        else:
            line = ("- " + text) if kind == "li" else text
        if kind == "li" and prev_li:
            parts[-1] += "\n" + line
        else:
            parts.append(line)
        prev_li = kind == "li"
    out.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


def write(blocks, fmt: str, title: str, lang: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "pdf":
        write_pdf(blocks, title, lang, out)
    elif fmt == "epub":
        write_epub(blocks, title, lang, out)
    elif fmt == "html":
        write_html(blocks, title, lang, out)
    elif fmt == "docx":
        write_docx(blocks, title, lang, out)
    elif fmt == "rtf":
        write_rtf(blocks, title, lang, out)
    elif fmt in ("md", "txt"):
        write_text(blocks, title, lang, out, markdown=fmt == "md")
    else:
        raise ValueError(f"unknown format {fmt}")
