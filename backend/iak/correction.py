"""Correcting the doubtful words of an OCR page (iak.doubt), word by word.

Two ways, both much faster than reading the whole page again:
- "fix", the text (chat) model: it gets each doubtful word in its context
  (a few words around it) and answers with the word, corrected or not, one
  short line per word. No image; a page costs a few dozen tokens of answer.
- "focus", targeted vision: each line holding doubtful words is cut out of
  the page image (a strip, enlarged) and read by the vision model; the
  reading of the line is aligned with its OCR and only the doubtful words
  take the model's. A line is a small image: quick even on a processor.

Every proposed word is judged before it is kept: a word the library knows
(iak.doubt's vocabulary) is kept; another is kept only when it is close to
the OCR's (a few letters changed: a misreading, not another word) and has
no odd letters itself. The rest is refused, and listed with the reading, so
that a model's invention ("arréle" -> "arrêtèle") does not slip in. The
result is a proposal for the page's text, like the other readings.
"""
from __future__ import annotations

import base64
import difflib
import json
import re
import unicodedata

from . import doubt
from .analyze import SYSTEM, AnalysisError

FIX_PROMPT = (
    "Each numbered line below shows a word of a printed page that OCR may have misread, "
    "between ⟦ and ⟧, with the words around it. Typical OCR slips: rn/m, li/h, c/e, n/u, "
    "1/l/I, 0/o, é/e, a long s ſ read as f, a word split in two or two words joined. "
    "For each number, give the right word when its letters and the sentence make it "
    "certain; otherwise give the word as it is. When the line lists words of the same "
    "book that are close to it, and one of them fits the sentence, give that one. Keep "
    "the original spelling, even old: do "
    "not modernise or translate. Answer with one line per number, \"number: word\", and "
    "nothing else.")
FOCUS_PROMPT = (
    "Transcribe exactly the line of printed text in this image, letter by letter. Keep "
    "the original spelling, accents, punctuation and capitals, even old or wrong: do not "
    "modernise or correct. Output only the line, without any comment.")
NOTHING_DOUBTFUL = "no doubtful word on this page"      # as transcription's
NO_PLACE = "the doubtful words have no place on the page image"
CONTEXT_WORDS = 7         # words on each side of a doubtful word, for the text model
BATCH = 30                # doubtful words per request to the text model
LINE_PX = 48              # height of a text line in the crop sent to the vision model
MAX_LINES = 80            # lines read on one page by the vision model


# ---------------------------------------------------------------- judging

def distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def judge(old: str, new: str, vocab: set[str], loose: bool = False) -> str | None:
    """None when new is the OCR's word, else why it is kept ("known",
    "close") or refused ("too different", "odd", "unknown parts", "empty").
    loose: the vision model has seen the letters, more may change."""
    if new == old:
        return None
    if not new.strip():
        return "empty"
    def known(w):
        return doubt._norm(w) in vocab
    limit = max(2, len(old) // 2) if loose else max(1, len(old) // 4)
    if " " in new:                               # a word split in two
        parts = new.split()
        if not all(known(p) or len(p) <= 2 for p in parts):
            return "unknown parts"
        return "known" if distance(old.lower(), "".join(parts).lower()) <= limit \
            else "too different"
    if doubt.odd_chars(new):
        return "odd"
    if known(new):
        return "known"
    return "close" if distance(old.lower(), new.lower()) <= limit else "too different"


KEPT = ("known", "close")


def apply_changes(text: str, suspects: list[dict], proposals: dict[int, str],
                  vocab: set[str], loose: bool = False) -> tuple[str, list[dict]]:
    """The text with the kept proposals {index in suspects: word}, and the
    list of changes [{"word", "new", "start", "kept", "why"}]."""
    changes = []
    for i, new in sorted(proposals.items()):
        s = suspects[i]
        new = new.strip()
        old_first = next((c for c in s["word"] if c.isalpha()), "")
        if old_first.isupper() and new[:1].islower():
            new = new[:1].upper() + new[1:]          # a capital stays (a sentence, a name)
        why = judge(s["word"], new, vocab, loose)
        if why is None:
            continue
        changes.append({"word": s["word"], "new": new, "start": s["start"],
                        "kept": why in KEPT, "why": why})
    out = text
    for c in sorted(changes, key=lambda c: -c["start"]):     # from the end: offsets hold
        if c["kept"]:
            out = out[:c["start"]] + c["new"] + out[c["start"] + len(c["word"]):]
    return out, changes


# ---------------------------------------------------------------- candidates

def _plain(w: str) -> str:
    """Lowercase, without accents: francais and français meet."""
    return "".join(c for c in unicodedata.normalize("NFD", w.lower())
                   if not unicodedata.combining(c))


class Candidates:
    """Known words close to a doubtful one: the same letters but for the
    accents, else the nearest of about the same length (difflib)."""

    def __init__(self, vocab: set[str]):
        self.plain: dict[str, list[str]] = {}
        self.by_len: dict[int, list[str]] = {}
        for w in vocab:
            if w.isalpha():
                self.plain.setdefault(_plain(w), []).append(w)
                self.by_len.setdefault(len(w), []).append(w)

    def __call__(self, word: str, n: int = 3) -> list[str]:
        w = doubt._norm(word)
        out = sorted(x for x in self.plain.get(_plain(w), []) if x != w)
        if len(out) < n and len(w) >= 3:
            pool = [x for k in (len(w) - 1, len(w), len(w) + 1) for x in self.by_len.get(k, [])]
            out += [x for x in difflib.get_close_matches(w, pool, n, cutoff=0.8)
                    if x != w and x not in out]
        if word[:1].isupper():
            out = [x[:1].upper() + x[1:] for x in out]
        return out[:n]


_candidates: dict = {}


def candidates_for(vocab: set[str]) -> Candidates:
    if _candidates.get("vocab") is not vocab:
        _candidates.update(vocab=vocab, cand=Candidates(vocab))
    return _candidates["cand"]


# ---------------------------------------------------------------- the text model

def context(text: str, s: dict, n: int = CONTEXT_WORDS) -> str:
    before = text[:s["start"]].split()[-n:]
    after = text[s["end"]:].split()[:n]
    # the punctuation stuck to the word stays outside the marks
    left = text[:s["start"]]
    lead = "" if not left or left[-1].isspace() else before.pop() if before else ""
    right = text[s["end"]:]
    tail = "" if not right or right[0].isspace() else after.pop(0) if after else ""
    return " ".join(before + [f"{lead}⟦{s['word']}⟧{tail}"] + after).replace("\n", " ")


ANSWER = re.compile(r"^\s*[-*]?\s*(\d{1,3})\s*[:.)\-–]\s*(.*?)\s*$")


def parse_answer(raw: str, numbers: range) -> dict[int, str]:
    """{number: word} from lines "n: word" (quotes, marks and stray
    punctuation around the word dropped)."""
    out = {}
    for line in raw.splitlines():
        m = ANSWER.match(line)
        if not m or int(m.group(1)) not in numbers:
            continue
        w = m.group(2).replace("⟦", "").replace("⟧", "").strip().strip("\"'«»“”`*")
        w = re.sub(r"\s*(?:\(.*\)|->.*|→.*)$", "", w).strip()   # "atlas (corrected)"
        if w:
            out.setdefault(int(m.group(1)), w)
    return out


def fix_words(lib, client, unit_id: int) -> tuple[str, list[dict], dict]:
    """(new text, changes, info) for a page, with the text model."""
    found = doubt.unit(lib, unit_id)
    suspects = found["suspects"]
    if not suspects:
        raise AnalysisError(NOTHING_DOUBTFUL)
    text = lib.con.execute("SELECT text FROM units WHERE id=?", (unit_id,)).fetchone()["text"]
    vocab = doubt.vocabulary(lib.con, str(lib.folder))
    near = candidates_for(vocab)
    proposals: dict[int, str] = {}
    truncated = False

    def item(k, s):
        close = near(s["word"])
        return f"{k}. {context(text, s)}" + (f"   (close words of the book: {', '.join(close)})"
                                              if close else "")
    for b0 in range(0, len(suspects), BATCH):
        batch = suspects[b0:b0 + BATCH]
        lines = "\n".join(item(k, s) for k, s in enumerate(batch, 1))
        info: dict = {}
        raw = client.chat([{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": f"{FIX_PROMPT}\n\n{lines}"}],
                          max_tokens=14 * len(batch) + 60, temperature=0.0, info=info)
        truncated |= info.get("finish_reason") == "length"
        for k, w in parse_answer(raw, range(1, len(batch) + 1)).items():
            proposals[b0 + k - 1] = w
    new, changes = apply_changes(text, suspects, proposals, vocab)
    return new, changes, {"doubtful": found["doubtful"], "answered": len(proposals),
                          "truncated": truncated}


# ---------------------------------------------------------------- targeted vision

def _line_of(text: str, start: int) -> tuple[int, int]:
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", start)
    return a, len(text) if b < 0 else b


def proposals_from_line(line: str, reading: str, suspects: list[tuple[int, dict]]) -> dict:
    """{index: word} for the doubtful words of an OCR line (offsets in the
    line), from the model's reading of it, aligned word by word."""
    ta = [(m.start(), m.group()) for m in re.finditer(r"\S+", line)]
    tb = [m.group() for m in re.finditer(r"\S+", reading)]
    sm = difflib.SequenceMatcher(None, [t for _, t in ta], tb, autojunk=False)
    out = {}

    def hits(p, t):
        return [(i, s) for i, s in suspects if p <= s["start"] < p + len(t)]
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        olds, news = ta[i1:i2], tb[j1:j2]
        if len(olds) == len(news):
            for (p, t), n in zip(olds, news):
                for i, s in hits(p, t):
                    # the token's punctuation around the word: same on both sides
                    pre, post = t[:s["start"] - p], t[s["start"] - p + len(s["word"]):]
                    core = n
                    if pre and core.startswith(pre):
                        core = core[len(pre):]
                    if post and core.endswith(post):
                        core = core[:-len(post)]
                    out[i] = core.strip(doubt.EDGE) or core
        elif len(olds) == 1 and len(news) == 2:     # one word split in two
            for i, s in hits(*olds[0]):
                out[i] = " ".join(w.strip(doubt.EDGE) for w in news)
    return out


def _crop_png(lib, unit_id: int, rect_px, unit_size) -> bytes:
    """The rectangle (in the unit's OCR pixels) of the page, as PNG, enlarged
    so that a text line is about LINE_PX high."""
    import pymupdf
    row = lib.con.execute("SELECT doc_id, locator FROM units WHERE id=?", (unit_id,)).fetchone()
    page_no = json.loads(row["locator"] or "{}").get("page", 1)
    path = lib.original_path(row["doc_id"])
    if not path.exists():
        raise AnalysisError(f"original file not found: {path}")
    with pymupdf.open(path) as d:
        page = d[page_no - 1]
        k = page.rect.width / max(unit_size[0], 1)          # OCR pixels -> points
        x0, y0, x1, y1 = (v * k for v in rect_px)
        clip = pymupdf.Rect(x0, y0, x1, y1) & page.rect
        zoom = min(8.0, max(1.0, LINE_PX / max(y1 - y0, 1) * 0.8))
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).tobytes("png")


def focus_words(lib, client, unit_id: int, progress=None) -> tuple[str, list[dict], dict]:
    """(new text, changes, info) for a page, reading its doubtful lines again
    with the vision model."""
    found = doubt.unit(lib, unit_id)
    suspects = [s for s in found["suspects"] if s.get("box")]
    if not suspects:
        raise AnalysisError(NOTHING_DOUBTFUL if not found["suspects"] else NO_PLACE)
    text = lib.con.execute("SELECT text FROM units WHERE id=?", (unit_id,)).fetchone()["text"]
    u = lib.con.execute("SELECT boxes, width, height FROM units WHERE id=?",
                        (unit_id,)).fetchone()
    size = (u["width"] or 1, u["height"] or 1)
    boxes_at = doubt._aligned_conf(text, json.loads(u["boxes"]) if u["boxes"] else [])
    vocab = doubt.vocabulary(lib.con, str(lib.folder))
    lines: dict[tuple[int, int], list[tuple[int, dict]]] = {}
    for i, s in enumerate(suspects):
        lines.setdefault(_line_of(text, s["start"]), []).append((i, s))
    proposals: dict[int, str] = {}
    read = 0
    for n, ((a, b), items) in enumerate(sorted(lines.items())[:MAX_LINES], 1):
        if progress:
            progress(n, min(len(lines), MAX_LINES))
        rects = [box for start, (_, box) in boxes_at.items() if a <= start < b and box]
        if not rects:
            continue
        x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
        x1, y1 = max(r[2] for r in rects), max(r[3] for r in rects)
        h = y1 - y0
        png = _crop_png(lib, unit_id, (x0 - h, y0 - 0.35 * h, x1 + h, y1 + 0.35 * h), size)
        content = [{"type": "text", "text": FOCUS_PROMPT},
                   {"type": "image_url", "image_url": {
                       "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]
        line = text[a:b]
        raw = client.chat([{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": content}],
                          max_tokens=max(40, len(line)), temperature=0.0,
                          extra={"presence_penalty": 1.0})
        reading = raw.strip().strip("`").splitlines()[0].strip() if raw.strip() else ""
        read += 1
        local = [(i, {**s, "start": s["start"] - a}) for i, s in items]
        proposals.update(proposals_from_line(line, reading, local))
    new, changes = apply_changes(text, suspects, proposals, vocab, loose=True)
    return new, changes, {"doubtful": found["doubtful"], "lines": read,
                          "answered": len(proposals), "truncated": False}
