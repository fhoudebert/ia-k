"""The chapters of a book in full text (after Prescel's epub_structure.py,
https://github.com/fhoudebert/prescel, MIT licence, by the same author).

An OCR'd book seldom shows its chapters by the size of the letters: its
titles are in capitals of the body's size, often spaced over several lines
(« LES » / « ANTILLES » / « AVANT »), and its running heads repeat them. Two
witnesses of the chapters are used:

- the book's own table of contents (« SOMMAIRE », « TABLE DES MATIÈRES »):
  its titles, between their page numbers;
- the patterns of chapter titles: « CHAPITRE II », « LIVRE PREMIER »…

A run of short paragraphs in capitals that reads as a title of the table
(OCR slips allowed, a word lost) becomes a heading of level 1, in the
table's words, at the first place it is found after the chapter before;
the running heads left of that title later in its chapter go. Headings
found from the letter sizes that are no chapter become level 2.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

TOC_HEAD = re.compile(r"^\W*(?:SOMMAIRE|TABLE(?:\s+DES\s+MATI[EÈ]RES|\s+ANALYTIQUE)?|"
                      r"Table\s+des\s+mati[eè]res|CONTENTS|Contents)\W*$")
TOC_END = re.compile(r"\b(?:ILLUSTRATIONS|TABLE\s+DES\s+(?:GRAVURES|FIGURES|PLANCHES|"
                     r"ILLUSTRATIONS)|BIBLIOGRAPHIE|INDEX|ERRATA)\b")
CHAPTER = re.compile(r"^\W{0,5}(?:CHAPITRE|Chapitre|CHAP\.?|Chap\.)\s*(?:[IVXLCDM]+|\d+|"
                     r"[A-ZÉÈ][A-Za-zÀ-ÿ\-]{3,14}|[Ii]er)\b[\s.:—\-]*(.*)$")
BOOK = re.compile(r"^\W{0,5}(?:LIVRE|Livre|PARTIE|Partie|TOME|Tome)\s+(?:[IVXLCDM]+|\d+|"
                  r"[A-ZÉÈ][A-Za-zÀ-ÿ\-]{3,14})\b[\s.:—\-]*(.*)$")
TITLE_MATCH = 0.8          # a run of capitals reads as a title of the table at least this well
CAPS_LEN = 70              # a paragraph of a title in capitals is at most this long


def plain(t: str) -> str:
    """Lowercase letters and digits, without accents, single spaces."""
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9œæ]+", t))


def capitals(t: str) -> bool:
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


# ---------------------------------------------------------------- table of contents

def is_toc_page(texts: list[str]) -> bool:
    return bool(texts) and any(TOC_HEAD.match(t.strip()) for t in texts[:2])


def toc_titles(texts: list[str], max_page: int = 2000, lexicon=frozenset()) -> list[str]:
    """The titles of a table of contents: what stands between its page
    numbers (« Préface historique 7 Les Antilles avant 1789 15 »), up to a
    list of illustrations or the bibliography. A page number grows through
    the table and stays below max_page (« 1789 » belongs to its title)."""
    body = "\n".join(t for t in texts if not TOC_HEAD.match(t.strip()))
    end = TOC_END.search(body)
    if end:
        body = body[:end.start()]
    cuts, last = [], 0
    for m in re.finditer(r"(?<![\w])\d{1,4}(?![\w])", body):
        n = int(m.group())
        if last <= n <= max_page:
            cuts.append(m)
            last = n
    segs, at = [], 0
    for m in cuts:
        segs.append(body[at:m.start()])
        at = m.end()
    segs.append(body[at:])
    titles = []
    for seg in segs:
        seg = re.sub(r"(?:\s*[.·…•]\s*){2,}", " ", seg)          # dot leaders
        seg = re.sub(r"^\W*(?:Pages?|PAGES?)\b", "", seg)
        seg = " ".join(seg.split()).strip(" .,;:—-·…•")
        words = seg.split()
        while words and len(words) > 1 and _speck(words[-1], lexicon):
            words.pop()                               # « …aux C RE e E »: a garbled line
        seg = " ".join(words).strip(" .,;:—-·…•")
        if sum(c.isalpha() for c in seg) >= 4:
            titles.append(seg)
    return titles


# ---------------------------------------------------------------- chapters

def _score(words: list[str], title: str) -> float:
    """How well some words read as a title: the whole title, or its end when
    a word of its beginning was lost (« HUGHES REPREND LA GUADELOUPE AUX
    ANGLAIS » for « Victor Hughes reprend la Guadeloupe aux Anglais »)."""
    t = plain(title).split()
    w = " ".join(words)
    best = SequenceMatcher(None, w, " ".join(t)).ratio()
    if len(words) >= 2 and len(words) < len(t) and len(words) >= 0.6 * len(t):
        tail = " ".join(t[-len(words):])
        best = max(best, SequenceMatcher(None, w, tail).ratio() - 0.02)
    return best


def _runs(blocks) -> list[tuple[int, int]]:
    """(start, end) of the runs of short paragraphs in capitals."""
    out, i = [], 0
    while i < len(blocks):
        j = i
        while j < len(blocks) and blocks[j][0] in ("p", "h1", "h2") \
                and len(blocks[j][1]) <= CAPS_LEN and capitals(blocks[j][1]):
            j += 1
        if j > i:
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _best_in(blocks, run, title) -> tuple[float, int, int]:
    """The part of a run that reads best as title: (score, start, end)."""
    a, b = run
    words = [plain(blocks[k][1]).split() for k in range(a, b)]
    best = (0.0, a, a)
    for x in range(a, b):
        flat: list[str] = []
        for y in range(x, b):
            flat = flat + words[y - a]
            if len(flat) > 3 * len(plain(title).split()) + 2:
                break
            s = _score(flat, title)
            if s > best[0]:
                best = (s, x, y + 1)
    return best


def _speck(w: str, lexicon) -> bool:
    if w.strip(".,;:").isdigit():
        return False                                  # a year: Les Antilles avant 1789
    letters = sum(c.isalpha() for c in w)
    return letters < 0.5 * len(w) or (letters <= 2 and plain(w) not in lexicon
                                      and not (lexicon == frozenset() and w.islower()))


def _wording(title: str, printed: str, lexicon) -> str:
    """The table's title, its words the table's OCR got wrong taken from the
    title printed in the text (« Los Corsaires » / « LES CORSAIRES »)."""
    if not lexicon:
        return title
    tw, pw = title.split(), printed.split()
    out = list(tw)
    ops = SequenceMatcher(None, [plain(w) for w in tw], [plain(w) for w in pw]).get_opcodes()
    if ops and ops[-1][0] == "insert" and ops[-1][1] == len(tw):
        # the table lost the end of the title: the text has it (« …aux » + ANGLAIS)
        extra = pw[ops[-1][3]:ops[-1][4]]
        if all(w.isalpha() and len(w) >= 3 and plain(w) in lexicon for w in extra):
            out += [w.lower() for w in extra]
    for op, a, b, c, d in ops:
        if op == "replace" and b - a == d - c:
            for k in range(b - a):
                mine, theirs = tw[a + k], pw[c + k]
                if plain(mine) not in lexicon and plain(theirs) in lexicon:
                    new = theirs.lower()
                    out[a + k] = new[:1].upper() + new[1:] if mine[:1].isupper() else new
    return " ".join(out)


def chapters(blocks: list[tuple], titles: list[str],
             lexicon=frozenset()) -> tuple[dict[int, tuple | None], int]:
    """The changes that make the titles of the table headings of level 1:
    ({index: new block, or None to drop it}, how many chapters found)."""
    runs = _runs(blocks)
    starts: list[tuple[int, int, str]] = []          # (start, end, title)
    pos = 0
    for title in titles:
        found = None
        for run in runs:
            if run[1] <= pos:
                continue
            run = (max(run[0], pos), run[1])
            s, a, b = _best_in(blocks, run, title)
            if s >= TITLE_MATCH and b > a:
                found = (a, b, title)
                break
        if found:
            a, b, _ = found
            printed = " ".join(blocks[k][1] for k in range(a, b))
            starts.append((a, b, _wording(title, printed, lexicon)))
            pos = b
    if len(starts) < 2:
        return {}, 0
    seen = Counter(w for k, t in blocks if k in ("p", "h1", "h2") for w in plain(t).split())
    changes: dict[int, tuple | None] = {}
    at = {a: (b, title) for a, b, title in starts}
    skip_to = 0
    current = None
    for i, (kind, text) in enumerate(blocks):
        if i < skip_to:
            changes[i] = None                         # the rest of a title on several lines
            continue
        if i in at:
            end, current = at[i]
            changes[i] = ("h1", current)
            skip_to = end
            continue
        if current and kind in ("p", "h1", "h2") and len(text) <= CAPS_LEN and capitals(text) \
                and _score(plain(text).split(), current) >= TITLE_MATCH:
            changes[i] = None                         # a running head of the chapter
        elif kind in ("h1", "h2"):
            words = plain(text).split()
            good = sum(w in lexicon or seen[w] >= 3 for w in words)
            letters = sum(c.isalpha() for c in text)
            if words and good < 0.5 * len(words) or letters < 0.6 * len(text.replace(" ", "")):
                changes[i] = ("p", text)              # OCR of an ornament, a map upside down
            elif not capitals(text) and (len(text) > 60 or re.search(r"[.,;:\-]$|[a-zà-ÿ]$", text)):
                changes[i] = ("p", text)              # a sentence, its type misjudged
            elif kind == "h1":
                changes[i] = ("h2", text)             # a heading by its size, inside a chapter
    return changes, len(starts)


def patterns(blocks: list[tuple]) -> tuple[dict[int, tuple | None], int]:
    """« LIVRE PREMIER » as level 1, « CHAPITRE II » as level 2 (level 1
    without books)."""
    found = [(i, "book" if BOOK.match(t) else "chapter") for i, (k, t) in enumerate(blocks)
             if k in ("p", "h1", "h2") and len(t) <= 120 and (BOOK.match(t) or CHAPTER.match(t))]
    if len(found) < 2:
        return {}, 0
    books = any(kind == "book" for _, kind in found)
    level = {"book": "h1", "chapter": "h2" if books else "h1"}
    return {i: (level[kind], blocks[i][1]) for i, kind in found}, len(found)


def plan(blocks: list[tuple], toc: list[str],
         lexicon=frozenset()) -> tuple[dict[int, tuple | None], int]:
    """Chapters from the table of contents when it gives them, else from
    the patterns of chapter titles: ({index: new block or None}, chapters)."""
    if toc:
        changes, n = chapters(blocks, toc, lexicon)
        if n:
            return changes, n
    return patterns(blocks)


def structure(blocks: list[tuple], toc: list[str],
              lexicon=frozenset()) -> tuple[list[tuple], int]:
    """The blocks with their chapters: (blocks, chapters found)."""
    changes, n = plan(blocks, toc, lexicon)
    out = []
    for i, b in enumerate(blocks):
        new = changes.get(i, b)
        if new is not None:
            out.append(new)
    return out, n
