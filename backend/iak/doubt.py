"""Doubtful words of a page read by OCR, and how doubtful each page is.

A word is doubtful when:
- "conf": Tesseract read it with a confidence below CONF_LOW, and it is not
  a known word (a known word counts only below CONF_VERY_LOW: noisy scans
  give low confidences to words read right);
- "unknown": it is not in the library's own vocabulary (see below), nor in
  a word list of config/words/, nor in the lexicon on the key
  (build/words/, scripts/fetch_words.py); words with a capital are left alone (names);
- "elision": an elision OCR got wrong, the apostrophe lost or the l' read as
  a letter (quon, Lanxiété), with the lexicon only;
- "chars": its letters look like an OCR slip: letters and digits mixed
  ("l1vre", "1es"), a capital inside a lowercase word ("aTlas"), odd signs inside
  the word (| ¦ ¢ © ® ° § ~ ^ _), one letter four times in a row.

The vocabulary is the library's: every word of native text and of pages
corrected by hand, and the OCR words read with a high confidence (at least
CONF_TRUSTED) seen twice. So it knows the spelling of the books themselves,
old French or Latin included, without a dictionary. More words can be added
with plain text files in config/words/ (one word per line, # comments): a
lexicon of the language, the names of a fonds...

The page score is the share of its words that are doubtful; pages at or
above DOUBT_RATIO are worth reading again (vision, or the text model).
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from pathlib import Path

CONF_LOW = 60          # Tesseract word confidence (0-100) below which a word is doubtful
CONF_VERY_LOW = 30     # below it, even a known word is doubtful
CONF_TRUSTED = 90      # OCR words at least this sure feed the vocabulary
DOUBT_RATIO = 0.05     # a page with 5 % of doubtful words is worth reading again
MAX_LIST = 300         # doubtful words returned for one page

ROOT = Path(__file__).resolve().parents[2]
WORDS_DIR = ROOT / "config" / "words"
# lexicons shipped on the key (scripts/fetch_words.py): build/words/<lang>.txt
LEXICON_DIR = ROOT / "build" / "words"

TOKEN = re.compile(r"[^\s'’]+")        # l'atlas, qu’il: elisions apart
EDGE = "\"'’‘«»“”()[]{}<>.,;:!?…—–-*"
ODD = set("|¦¢©®°§~^_\\")
LETTERS = re.compile(r"[^\W\d_]", re.U)


def _core(token: str) -> tuple[int, str]:
    """(offset in token, word) without the punctuation around it."""
    left = len(token) - len(token.lstrip(EDGE))
    return left, token.strip(EDGE)


def words_of(text: str):
    """(start, end, word) of each word of a text, offsets in the text."""
    for m in TOKEN.finditer(text):
        off, w = _core(m.group())
        if w and LETTERS.search(w):
            start = m.start() + off
            yield start, start + len(w), w


def _norm(w: str) -> str:
    return w.lower().replace("’", "'").replace("œ", "oe").replace("æ", "ae")


# the first part of an elision (l'atlas, qu'il, aujourd'hui): a word before an apostrophe
ELIDES = {"l", "d", "j", "m", "n", "s", "t", "c", "qu", "lorsqu", "puisqu", "jusqu", "quoiqu",
          "presqu", "quelqu", "entr", "aujourd"}


def odd_chars(w: str) -> bool:
    if any(c in ODD for c in w):
        return True
    letters = sum(c.isalpha() for c in w)
    digits = sum(c.isdigit() for c in w)
    if letters and digits and not re.fullmatch(r"\d+(?:e|er|re|ème|eme|nd|st|th|o)s?", w.lower()):
        return True                              # "l1vre", but 1er, 2e, 15th are fine
    if w[0].islower() and any(c.isupper() for c in w[1:]):
        return True                              # "aTlas" (McDonald starts with a capital)
    return bool(re.search(r"(.)\1\1\1", w))


# ------------------------------------------------------------------ vocabulary

_cache: dict = {}
_lock = threading.Lock()


def _signature(con) -> tuple:
    r = con.execute("SELECT count(*), coalesce(max(id), 0), coalesce(sum(length(text)), 0),"
                    " coalesce(sum(edited), 0) FROM units").fetchone()
    files = tuple(sorted((p.name, p.stat().st_mtime) for p in WORDS_DIR.glob("*.txt"))) \
        if WORDS_DIR.is_dir() else ()
    return tuple(r) + files


def _list_words() -> set[str]:
    out: set[str] = set()
    if not WORDS_DIR.is_dir():
        return out
    for p in sorted(WORDS_DIR.glob("*.txt")):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.add(_norm(line))
    return out


_lexicon: dict = {}


def lexicon() -> frozenset[str]:
    """The words of the lexicons on the key (French: about 336,000 forms),
    loaded once; empty without them."""
    files = tuple(sorted((p.name, p.stat().st_mtime) for p in LEXICON_DIR.glob("*.txt"))) \
        if LEXICON_DIR.is_dir() else ()
    with _lock:
        if _lexicon.get("files") == files:
            return _lexicon["words"]
    words: set[str] = set()
    for name, _ in files:
        text = (LEXICON_DIR / name).read_text(encoding="utf-8", errors="replace")
        words.update(_norm(w) for w in text.split())
    with _lock:
        _lexicon.update(files=files, words=frozenset(words))
    return _lexicon["words"]


def vocabulary(con, key: str = "") -> set[str]:
    """The words the library trusts (cached until the pages change)."""
    sig = (key, _signature(con))
    with _lock:
        if _cache.get("sig") == sig:
            return _cache["words"]
    trusted: set[str] = set()
    seen = Counter()
    for u in con.execute("SELECT text, source, edited, boxes FROM units"):
        if u["source"] == "native" or u["edited"]:
            trusted.update(_norm(w) for _, _, w in words_of(u["text"]))
        elif u["boxes"]:
            for b in json.loads(u["boxes"]):
                if len(b) > 5 and b[5] is not None and b[5] >= CONF_TRUSTED:
                    _, w = _core(str(b[4]))
                    if w:
                        seen[_norm(w)] += 1
    words = trusted | {w for w, n in seen.items() if n >= 2} | _list_words()
    with _lock:
        _cache.update(sig=sig, words=words)
    return words


def vocabulary_from_boxes(pages) -> set[str]:
    """The trusted words of a book read by Tesseract (an EPUB of pictures):
    words read with CONF_TRUSTED or more, seen twice, and config/words/."""
    seen = Counter()
    for boxes in pages:
        for b in boxes or []:
            if len(b) > 5 and b[5] is not None and b[5] >= CONF_TRUSTED:
                _, w = _core(str(b[4]))
                for part in TOKEN.findall(w):
                    _, core = _core(part)
                    if core:
                        seen[_norm(core)] += 1
    return {w for w, n in seen.items() if n >= 2} | _list_words()


# ------------------------------------------------------------------ pages

def _aligned_conf(text: str, boxes: list) -> dict[int, tuple[float, list]]:
    """{start offset of a word in text: (confidence, box)} for the words of
    the boxes found in order in the text (a corrected word has none)."""
    out: dict[int, tuple[float, list]] = {}
    cursor = 0
    for b in boxes:
        if len(b) < 6 or not str(b[4]).strip():
            continue
        w = str(b[4])
        i = text.find(w, cursor)
        if i < 0 or i - cursor > 300:            # not found nearby: corrected away
            continue
        cursor = i + len(w)
        for start, _, _ in words_of(w):          # l'atlas: the box is atlas's too
            out[i + start] = (b[5], b[:4])
    return out


def box_index(text: str, boxes: list) -> dict[int, int]:
    """{start offset of a word in text: index of its box} (as _aligned_conf)."""
    out: dict[int, int] = {}
    cursor = 0
    for k, b in enumerate(boxes):
        if len(b) < 5 or not str(b[4]).strip():
            continue
        w = str(b[4])
        i = text.find(w, cursor)
        if i < 0 or i - cursor > 300:
            continue
        cursor = i + len(w)
        for start, _, _ in words_of(w):
            out[i + start] = k
    return out


ELISION = re.compile(r"^(?:qu|[ldjmnstcLDJMNSTCQ]|[TFVPI])([aeiouyhàâäéèêëîïôöûüœæ]\w+)$")


def elided(w: str, lex) -> bool:
    """An elision OCR got wrong: a word of the lexicon after a lost
    apostrophe (quon, saccomplit) or an l' read as a letter (Lanxiété)."""
    m = ELISION.match(w)
    return bool(m) and len(m.group(1)) >= 2 and _norm(m.group(1)) in lex


def page(text: str, boxes: list | None, vocab: set[str], source: str = "ocr",
         edited: bool = False) -> dict:
    """The doubtful words of a page: {"words", "doubtful", "ratio", "suspects":
    [{"start", "end", "word", "reasons", "conf", "box"}]}. Native text and
    pages corrected by hand are trusted: no doubtful word."""
    words = list(words_of(text))
    out = {"words": len(words), "doubtful": 0, "ratio": 0.0, "suspects": []}
    if source == "native" or edited or not words:
        return out
    conf = _aligned_conf(text, boxes or [])
    lex = lexicon()
    # a word cut at the end of a line (docu-/mentée): both halves are fine
    # when the whole is known
    whole: set[int] = set()
    for m in re.finditer(r"([^\W\d_]+)-[ \t]*\n[ \t]*([^\W\d_]+)", text):
        w2 = _norm(m.group(1) + m.group(2))
        if w2 in vocab or w2 in lex:
            whole.update((m.start(1), m.start(2)))
    for start, end, w in words:
        if start in whole or len(w) == 1:
            continue
        if text[end:end + 1] in "'’" and _norm(w) in ELIDES:
            continue                             # l', qu', aujourd'
        if _norm(w) == "hui" and _norm(text[max(0, start - 8):start]) == "aujourd'":
            continue
        reasons = []
        c, box = conf.get(start, (None, None))
        known = _norm(w) in vocab or _norm(w) in lex
        if c is not None and 0 <= c < (CONF_VERY_LOW if known else CONF_LOW):
            reasons.append("conf")
        if odd_chars(w):
            reasons.append("chars")
        elif lex and _norm(w) not in lex and elided(w, lex):
            reasons.append("elision")                # Lanxiété, quon: even if frequent
        elif (len(w) >= 3 and w.isalpha() and w[0].islower() and (vocab or lex)
              and not (c is not None and c >= CONF_TRUSTED) and not known):
            reasons.append("unknown")
        if reasons:
            out["doubtful"] += 1
            if len(out["suspects"]) < MAX_LIST:
                out["suspects"].append({"start": start, "end": end, "word": w,
                                        "reasons": reasons, "conf": c, "box": box})
    out["ratio"] = round(out["doubtful"] / len(words), 3)
    return out


def unit(lib, unit_id: int) -> dict:
    u = lib.con.execute("SELECT id, text, source, edited, boxes, width, height FROM units"
                        " WHERE id=?", (unit_id,)).fetchone()
    if u is None:
        raise KeyError(f"no unit {unit_id}")
    res = page(u["text"], json.loads(u["boxes"]) if u["boxes"] else [],
               vocabulary(lib.con, str(lib.folder)), u["source"], bool(u["edited"]))
    return {"unit_id": u["id"], "width": u["width"], "height": u["height"], **res}


def document(lib, doc_id: int) -> list[dict]:
    """Doubt of each page of a document, without the word lists."""
    vocab = vocabulary(lib.con, str(lib.folder))
    out = []
    for u in lib.units(doc_id):
        res = page(u["text"], json.loads(u["boxes"]) if u["boxes"] else [], vocab,
                   u["source"], bool(u["edited"]))
        out.append({"unit_id": u["id"], "label": u["label"], "words": res["words"],
                    "doubtful": res["doubtful"], "ratio": res["ratio"],
                    "doubt": res["ratio"] >= DOUBT_RATIO})
    return out


def marked(text: str, suspects: list[dict]) -> str:
    """The text with each doubtful word between ⟦ and ⟧ (for the text model)."""
    out, last = [], 0
    for s in sorted(suspects, key=lambda s: s["start"]):
        out += [text[last:s["start"]], "⟦", text[s["start"]:s["end"]], "⟧"]
        last = s["end"]
    out.append(text[last:])
    return "".join(out)
