"""Output formats for books translated from a flat text (a PDF, a scan read
in the library, a text or Markdown file): PDF, EPUB, HTML, DOCX, RTF,
Markdown, text.

The book is a list of blocks (kind, text): h1 and h2 headings, p paragraphs,
li list items, quote, code. EPUB is cut into chapters at h1 headings (or
every 150 blocks), for e-readers; the PDF is laid out on A5 pages, the size
of a reader's screen, with fonts embedded by MuPDF (Latin, Greek, Cyrillic,
Hebrew and CJK scripts; Arabic is not shaped well).
"""
from __future__ import annotations

import html
import time
import uuid
import zipfile
from pathlib import Path

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
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def html_body(blocks: list[tuple[str, str]]) -> str:
    out, in_list = [], False
    for kind, text in blocks:
        if kind == "li" and not in_list:
            out.append("<ul>")
            in_list = True
        elif kind != "li" and in_list:
            out.append("</ul>")
            in_list = False
        if kind in ("h1", "h2"):
            out.append(f"<{kind}>{_esc(text)}</{kind}>")
        elif kind == "li":
            out.append(f"<li>{_esc(text)}</li>")
        elif kind == "quote":
            out.append(f"<blockquote><p>{_esc(text)}</p></blockquote>")
        elif kind == "code":
            out.append(f"<pre>{_esc(text)}</pre>")
        else:
            out.append(f"<p>{_esc(text)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def write_html(blocks, title: str, lang: str, out: Path) -> None:
    out.write_text(
        f'<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{html_body(blocks)}\n</body>\n</html>\n", encoding="utf-8")


def _chapters(blocks, size: int = 150) -> list[list[tuple[str, str]]]:
    chapters: list[list] = [[]]
    for b in blocks:
        if (b[0] == "h1" and chapters[-1]) or len(chapters[-1]) >= size:
            chapters.append([])
        chapters[-1].append(b)
    return [c for c in chapters if c]


def write_epub(blocks, title: str, lang: str, out: Path) -> None:
    """EPUB 3 (with an NCX for older readers)."""
    chapters = _chapters(blocks)
    book_id = f"urn:uuid:{uuid.uuid4()}"
    titles = []
    for i, ch in enumerate(chapters, 1):
        head = next((t for k, t in ch if k in ("h1", "h2")), None)
        titles.append(head or (title if i == 1 else f"{i}"))

    def xhtml(i, ch):
        return (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">\n'
                f"<head><title>{_esc(titles[i - 1])}</title>"
                f'<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
                f"<body>\n{html_body(ch)}\n</body>\n</html>\n")
    manifest = "\n".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
                         for i in range(1, len(chapters) + 1))
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
    tmp.replace(out)


def write_pdf(blocks, title: str, lang: str, out: Path) -> None:
    """A5 pages laid out by MuPDF from the HTML, page numbers at the foot."""
    import pymupdf
    css = CSS + "body { font-size: 11pt; margin: 0; } p { margin-bottom: .35em; }"
    story = pymupdf.Story(html=f'<body lang="{lang}">{html_body(blocks)}</body>', user_css=css)
    page = pymupdf.paper_rect("a5")
    where = page + (42, 46, -42, -50)
    tmp = out.with_name(out.name + ".part")
    writer = pymupdf.DocumentWriter(str(tmp))
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
    with pymupdf.open(str(tmp)) as doc:
        doc.set_metadata({"title": title, "producer": "IA-K"})
        for i, p in enumerate(doc, 1):
            p.insert_textbox(pymupdf.Rect(0, page.height - 36, page.width, page.height - 20),
                             str(i), fontsize=8, align=1, color=(0.35, 0.35, 0.35))
        doc.save(str(out), garbage=3, deflate=True)
    tmp.unlink()


def write_docx(blocks, title: str, lang: str, out: Path) -> None:
    import docx
    d = docx.Document()
    d.core_properties.title = title
    d.core_properties.language = lang
    for kind, text in blocks:
        if kind == "h1":
            d.add_heading(text, 1)
        elif kind == "h2":
            d.add_heading(text, 2)
        elif kind == "li":
            d.add_paragraph(text, style="List Bullet")
        elif kind == "quote":
            d.add_paragraph(text, style="Quote")
        else:
            d.add_paragraph(text)
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


def write_rtf(blocks, title: str, lang: str, out: Path) -> None:
    lines = ["{\\rtf1\\ansi\\ansicpg1252\\deff0",
             "{\\fonttbl{\\f0\\froman Times New Roman;}{\\f1\\fmodern Courier New;}}",
             f"{{\\info{{\\title {_rtf(title)}}}}}", "\\fs24"]
    for kind, text in blocks:
        t = _rtf(text)
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
    parts, prev_li = [], False
    for kind, text in blocks:
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
