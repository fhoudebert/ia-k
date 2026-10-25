"""Paragraphs and notes of a page, for the full-text versions.

OCR gives lines, and Tesseract's own paragraphs often cut a paragraph at a
line it found misaligned. With the word boxes, the paragraphs are rebuilt
from the page's geometry, the way a reader sees them:
- a new paragraph starts at an indented first line, after extra space
  between lines, after a short line that ends a sentence, at a change of
  type size, and at a centred line (a title);
- otherwise the line continues the paragraph (hyphenations removed).
Without boxes that follow the text (a page corrected by hand), Tesseract's
paragraphs are kept, joined where one plainly goes on in the next (it ends
without a full stop, on a lowercase letter, a comma or a hyphen).

Notes: at the foot of a page, paragraphs in a smaller type that start with
a mark (1, (1), 1., *, †, a)) are notes; the same mark after a word in the
page's text (atlas1, atlas¹, atlas (1), atlas*) is their call. Both ends must
be found for a link; a note whose call is not found stays a note.
"""
from __future__ import annotations

import re
from statistics import median

from .book import _text_blocks, join_lines

END = re.compile(r"[.!?:;»\"”)\]…]$")
GOES_ON = re.compile(r"[a-zà-ÿœæ,\-]$")
NOTE_START = re.compile(r"^(?:\((\d{1,3}|[a-z])\)|(\d{1,3})[.)]?|(\*{1,3}|[†‡§]))\s+(\S.*)$", re.S)
SUPER = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
# how Tesseract often reads a superscript 1, 2 or 3 right after a word:
# signs (atlas! atlas?), or a letter (atlasi), taken only when the word
# without it is in the document and with it is not (lui stays lui)
MISREAD = {"1": "[!ı|'’]", "2": "[?]", "3": "[»]"}
MISREAD_LETTER = {"1": "il", "2": "z"}
NOTE_SIZE = 0.88          # notes are in a type at most this size of the body's
# marks of note calls inside paragraphs (private use characters: never in a text)
CALL_OPEN, CALL_CLOSE = "", ""


def call(n: int) -> str:
    return f"{CALL_OPEN}{n}{CALL_CLOSE}"


CALL = re.compile(f"{CALL_OPEN}(\\d+){CALL_CLOSE}")


# ---------------------------------------------------------------- paragraphs

def continues(prev: str, nxt: str) -> bool:
    """Does the paragraph prev go on in nxt (a cut Tesseract or a page made)?"""
    if not prev or not nxt or END.search(prev):
        return False
    return nxt[:1].islower() or bool(GOES_ON.search(prev))


def text_paragraphs(text: str) -> list[str]:
    """Tesseract's paragraphs, joined where one plainly goes on."""
    out: list[str] = []
    for t in _text_blocks(text):
        if out and continues(out[-1], t) and not NOTE_START.match(t):
            out[-1] = join_lines(out[-1], t)
        else:
            out.append(t)
    return out


def _lines(text: str, boxes) -> list[dict] | None:
    """The lines of an OCR page with their box, or None when the boxes do not
    follow the text word for word (corrected since)."""
    words = [b for b in boxes or [] if str(b[4]).strip()]
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    if not words or sum(len(ln.split()) for ln in lines) != len(words):
        return None
    out, i = [], 0
    for ln in lines:
        n = len(ln.split())
        if not n:
            continue
        ws = words[i:i + n]
        i += n
        if [str(w[4]) for w in ws] != ln.split():
            return None
        heights = sorted(w[3] - w[1] for w in ws)
        out.append({"text": ln, "x0": min(w[0] for w in ws), "y0": min(w[1] for w in ws),
                    "x1": max(w[2] for w in ws), "y1": max(w[3] for w in ws),
                    "h": heights[len(heights) // 2]})
    return out


def ocr_paragraphs(text: str, boxes) -> list[tuple[str, float | None]]:
    """(paragraph, type size) of an OCR page: from the geometry of its lines
    when the boxes follow the text, else Tesseract's paragraphs joined."""
    lines = _lines(text, boxes)
    if not lines:
        return [(t, None) for t in text_paragraphs(text)]
    # the type size of a line from the height of its words (capitals and
    # descenders even out in the median)
    return [(t, round(h * 0.75, 0)) for t, h, _ in paragraphs(lines)]


def native_lines(page) -> list[dict]:
    """The lines of a PDF page's own text, with their box and type size;
    "brk": the first line of a PyMuPDF block. Superscript marks (note calls)
    become superscript digits: atlas¹."""
    out = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") != 0:
            continue
        first = True
        for line in b.get("lines", []):
            parts, sizes = [], {}
            for sp in line.get("spans", []):
                t = sp.get("text", "")
                if sp.get("flags", 0) & 1 and re.fullmatch(r"\s*\d{1,3}\s*", t):
                    t = t.strip().translate(SUPER)
                elif t.strip():
                    sz = round(sp.get("size", 0), 1)
                    sizes[sz] = sizes.get(sz, 0) + len(t.strip())
                parts.append(t)
            text = re.sub(r"\s+", " ", "".join(parts)).strip()
            if not text or not sizes:
                continue
            x0, y0, x1, y1 = line["bbox"]
            out.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                        "h": max(sizes, key=sizes.get), "brk": first})
            first = False
    return out


def paragraphs(lines: list[dict]) -> list[tuple[str, float, float]]:
    """(paragraph, median line size, top) from lines {"text", "x0", "y0",
    "x1", "y1", "h"} in reading order ("h": the type size, or the words'
    height)."""
    if not lines:
        return []
    H = median(ln["h"] for ln in lines) or 1
    gaps = [b["y0"] - a["y1"] for a, b in zip(lines, lines[1:]) if b["y0"] > a["y1"]]
    G = median(gaps) if gaps else H * 0.4
    paras: list[list[dict]] = []
    for i, ln in enumerate(lines):
        if not paras:
            paras.append([ln])
            continue
        prev = paras[-1][-1]
        near = lines[max(0, i - 3):i + 4]
        left = min(x["x0"] for x in near)
        right = max(x["x1"] for x in near)
        h = max(ln["h"], prev["h"])
        new = False
        # another type size (a line of a word or two has no reliable height)
        few = min(len(ln["text"].split()), len(prev["text"].split())) < 3
        if abs(ln["h"] - prev["h"]) > (0.35 if few else 0.15) * h:
            new = True
        elif ln["y0"] - prev["y1"] > G + 0.8 * H:                    # extra space
            new = True
        elif ln["y0"] < prev["y0"] - H:                              # next column
            new = not continues(prev["text"], ln["text"])
        elif ln["x0"] - left > 0.8 * H and abs(ln["x0"] - prev["x0"]) > 0.5 * H:
            new = True                                               # first line indented
        elif right - prev["x1"] > 2.5 * H and (END.search(prev["text"])
                                                or ln["text"][:1].isupper()):
            new = True                                               # the last, short line
        elif _centred(ln, left, right, H) or _centred(prev, left, right, H):
            new = True                                               # a title
        elif NOTE_START.match(ln["text"]) and ln["h"] < prev["h"] * 0.95:
            new = True
        elif ln.get("brk") and not continues(prev["text"], ln["text"]):
            new = True                                               # a new PDF text block
        if new:
            paras.append([ln])
        else:
            paras[-1].append(ln)
    out = []
    for p in paras:
        t = ""
        for ln in p:
            t = join_lines(t, ln["text"])
        hs = sorted(ln["h"] for ln in p)
        out.append((t, hs[len(hs) // 2], p[0]["y0"]))
    return out


def _centred(ln, left, right, H) -> bool:
    margin_l, margin_r = ln["x0"] - left, right - ln["x1"]
    return margin_l > 2 * H and margin_r > 2 * H and abs(margin_l - margin_r) < 1.5 * H


# ---------------------------------------------------------------- notes

def note_mark(text: str) -> tuple[str, str] | None:
    """(mark, text) of a note paragraph: "1 Voir…", "(1) Voir…", "* Voir…"."""
    m = NOTE_START.match(text)
    if not m:
        return None
    return (m.group(1) or m.group(2) or m.group(3)), m.group(4).strip()


def _call_patterns(mark: str) -> list[re.Pattern]:
    if mark.isdigit():
        sup = re.escape(mark.translate(SUPER))
        d = re.escape(mark)
        pats = [re.compile(f"(?<=\\S){sup}(?![⁰-⁹¹²³])"),
                re.compile(f"(?<=[^\\W\\d_]){d}(?!\\d)"),           # atlas1, atlas1,
                re.compile(f"\\s?\\({d}\\)"),                         # atlas (1)
                re.compile(f"(?<=[^\\W\\d_])\\[{d}\\]")]
        if mark in MISREAD:
            # after a word, or a closing quote or full stop: lumière »' ; tirée'.
            pats.append(re.compile(f"(?<=[^\\W\\d_]|[»”.]){MISREAD[mark]}(?=[\\s,.;:]|$)"))
        return pats
    if len(mark) == 1 and mark.isalpha():
        return [re.compile(f"\\s?\\({mark}\\)")]
    return [re.compile(f"(?<=\\S){re.escape(mark)}(?!\\*)")]


WORD = re.compile(r"[^\W\d_]+")


def words(texts) -> dict[str, int]:
    """How often each word (lowercase) appears in texts."""
    out: dict[str, int] = {}
    for t in texts:
        for w in WORD.findall(t.lower()):
            out[w] = out.get(w, 0) + 1
    return out


def link_call(texts: list[str], mark: str, n: int, known: dict[str, int] | None = None,
              lexicon=frozenset()) -> bool:
    """Put the call mark n in place of the first call `mark` found in texts
    (changed in place). True when found. known: the words of the document,
    for a call read as a letter (atlasi); lexicon: more known words."""
    for pat in _call_patterns(mark):
        for i, t in enumerate(texts):
            m = pat.search(t)
            if m:
                texts[i] = t[:m.start()] + call(n) + t[m.end():]
                return True
    if known and mark in MISREAD_LETTER:
        pat = re.compile(f"\\b([^\\W\\d_]{{2,}})([{MISREAD_LETTER[mark]}])(?=[\\s,.;:]|$)")
        for i, t in enumerate(texts):
            for m in pat.finditer(t):
                w, whole = m.group(1).lower(), m.group(0).lower()
                # the word without the letter is known, with it is not (with
                # a lexicon: not in it; without: not seen more than once)
                if (known.get(w, 0) >= 1 or w in lexicon) and whole not in lexicon \
                        and (lexicon or known.get(whole, 0) <= 1):
                    texts[i] = t[:m.end(1)] + call(n) + t[m.end():]
                    return True
    return False


def split_calls(text: str) -> list:
    """Text pieces and note numbers (int), in order."""
    out, last = [], 0
    for m in CALL.finditer(text):
        if m.start() > last:
            out.append(text[last:m.start()])
        out.append(int(m.group(1)))
        last = m.end()
    if last < len(text):
        out.append(text[last:])
    return out


def strip_calls(text: str, fmt=lambda n: f"[{n}]") -> str:
    return CALL.sub(lambda m: fmt(int(m.group(1))), text)
