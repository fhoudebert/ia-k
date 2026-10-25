"""Finishing the text of a full-text version, book-wide (after Prescel,
https://github.com/fhoudebert/prescel, scripts typo_fr.py, epub_longs.py
and epub_recolle.py, MIT licence, by the same author):

- long_s: the long s (ſ) of old prints read "f": « eft » -> « est »,
  « auffi » -> « aussi », « faifoit » -> « faisoit ». A word is corrected
  only into a form the book writes elsewhere (twice) or the lexicon knows,
  never when the f form is itself a real word (fait / sait, font / sont);
- words: words OCR cut with a space (« mar eschal », « autre ment ») are
  joined when the whole word is in the book (or the lexicon) and one of the
  pieces is never a word alone; words OCR glued (« chambredu ») are split
  when only one cut gives words of the book;
- typo: French typography: no-break spaces before ; : ! ? » and after «,
  none before . , ) ], « ... » -> « … », dialogue dashes. Letters never
  change, only spaces, « ... » and the dash.

Each pass works on all the paragraphs of a book at once (the book is its own
dictionary) and lists what it changed, and what it left, for review. Note
calls (layout.call) are left where they are.
"""
from __future__ import annotations

import itertools
import re
from collections import Counter

from .layout import CALL_CLOSE, CALL_OPEN

WORD = re.compile(r"[^\W\d_]+")

# ---------------------------------------------------------------- typography
# typo_fr.py (Prescel), with its boundary mark moved out of the way of the
# note calls

M = ""                 # boundary between two pieces (invisible to the rules)
NBSP = " "
SP = "[   \t]"     # ordinary and no-break spaces
_M = "(?:%s)*" % M
LETTER = "A-Za-zÀ-ÖØ-öø-ÿŒœÆæ"


def _rules(s):
    # « ... » -> « … »
    s = re.sub(r"(?<!\.)\.\.\.(?!\.)", "…", s)
    # dash: « - » between spaces, or at the head of a paragraph (dialogue)
    s = re.sub(r"(?<=\S)(%s)%s+-%s+" % (_M, SP, SP), r"\1 — ", s)
    s = re.sub(r"^(%s)[-—]%s*" % (_M, SP), r"\1— ", s)
    # no space before . , … ) ]
    s = re.sub(r"(?<=[^\s%s])(%s)%s+(%s)([.,)\]])" % (M, _M, SP, _M), r"\1\2\3", s)
    s = re.sub(r"(?<=[%s%s])(%s)%s+(%s)(…)" % (LETTER, "»", _M, SP, _M), r"\1\2\3", s)
    # no space after ( [
    s = re.sub(r"([(\[])(%s)%s+" % (_M, SP), r"\1\2", s)
    # no-break space before ; : ! ?  (not between two digits: « 10:30 »)
    s = re.sub(r"(?<=[^\s;:!?(\[%s])(%s)%s*(%s)([;!?])" % (M, _M, SP, _M), r"\1\2" + NBSP + r"\3", s)
    s = re.sub(r"(?<=[^\s;:!?\d%s])(%s)%s*(%s)(:)" % (M, _M, SP, _M), r"\1\2" + NBSP + r"\3", s)
    # no-break space before » and after «
    s = re.sub(r"(?<=[^\s%s«])(%s)%s*(%s)(»)" % (M, _M, SP, _M), r"\1\2" + NBSP + r"\3", s)
    s = re.sub(r"(«)(%s)%s*(?=\S)" % (_M, SP), r"\1" + NBSP + r"\2", s)
    # a space after . , ; : ! ? … ) ] » when a word (or « ( [) follows at once
    s = re.sub(r"([.,;:!?…)»])(%s)(?=[%s«(\[])" % (_M, LETTER), r"\1\2 ", s)
    # « ] »: only when it closes a passage in brackets begun outside a word
    s = re.sub(r"((?<![%s])\[(?![%s'’]{1,5}\])[^\]]*\])(%s)(?=[%s«(\[])" % (LETTER, LETTER, _M, LETTER),
               r"\1\2 ", s)
    # a dash stuck to its neighbours (« France.— 1-2 »): a space on each side
    s = re.sub(r"(?<=[^\s—(\[%s])(%s)—" % (M, _M), r"\1 —", s)
    s = re.sub(r"—(%s)(?=[^\s—.,;:)\]%s])" % (_M, M), r"—\1 ", s)
    # multiple spaces
    s = re.sub(r"(?<=\S)(%s)[ ]{2,}" % _M, r"\1 ", s)
    s = re.sub(r" +(%s)%s" % (_M, NBSP), r"\1" + NBSP, s)
    return s


def _letters(s: str) -> str:
    return re.sub(r"[\s  ]", "", s.replace("…", "...").replace("—", "-"))


def typo(text: str) -> str:
    """French typography of one paragraph; the note calls are boundaries the
    rules do not see (« mythe¹, que » keeps its comma against the call)."""
    pieces = re.split(f"({CALL_OPEN}\\d+{CALL_CLOSE})", text)
    texts, calls = pieces[0::2], pieces[1::2]
    out = _rules(M.join(texts)).split(M)
    if len(out) != len(texts) or _letters("".join(out)) != _letters("".join(texts)):
        return text                       # in doubt, nothing changes
    return "".join(itertools.chain.from_iterable(itertools.zip_longest(out, calls,
                                                                       fillvalue="")))


# ---------------------------------------------------------------- long s

# f forms that are real words too: never corrected (epub_longs.py)
COLLISIONS = set("""
font fait fais foi fois fort forts forte fortes fuite fuites fuis fuit fuir falut faut
faute fautes fauter fera ferai feras ferez ferons feront feroit feroient ferois ferions
ferment fervent fervente fol fols folle fou fous fable fables fain faine fac fec fecond
fonde fonder fondé fondez foule foules fouler fûr fin fins fil fils fille
fier fiers fiere fieres fixe fûre fonge fage feu feux fond fonds fer fers fête fêtes
fire fis fit fît firent fut fût furent fus fusse fussent faire faites faite faits
force forces forcer forcé forcée forcez fosse faisons faisois faisoit faisoient faisant
faisait faisaient fausse faussé feint fente folie fondre fondu forme formes fossé
""".split())
# word starts that do not exist in French: « fleur » cannot be « sleur »
BAD_START = re.compile(r"^s[lrdbgvnmfhzkqwcpj]")


def _s_form(word: str, counts: Counter, lexicon) -> str | None:
    """The best s form of a word read with f (lowercase), or None."""
    if "ß" in word or "ſ" in word:
        return word.replace("ß", "ss").replace("ſ", "s")
    pos = [i for i, c in enumerate(word) if c == "f"]
    if not pos or len(pos) > 5:
        return None
    best, best_n = None, 0
    for k in range(1, len(pos) + 1):
        for combo in itertools.combinations(pos, k):
            cand = list(word)
            for i in combo:
                cand[i] = "s"
            cand = "".join(cand)
            if "fs" in cand or "sf" in cand or BAD_START.match(cand):
                continue
            n = counts.get(cand, 0) + (2 if cand in lexicon else 0)
            if n >= 2 and (n > best_n or (n == best_n and best and
                                          cand.count("f") < best.count("f"))):
                best, best_n = cand, n
    return best


def long_s(texts: list[str], lexicon=frozenset()) -> tuple[list[str], list[dict]]:
    counts = Counter(w.lower() for t in texts for w in WORD.findall(t))
    mapping, log = {}, []
    for w, n in counts.items():
        if len(w) < 2 or not ("f" in w or "ß" in w or "ſ" in w) or w in lexicon:
            continue
        s = _s_form(w, counts, lexicon)
        if not s:
            continue
        ns = counts.get(s, 0)
        if w in COLLISIONS:
            log.append({"word": w, "new": s, "kept": False, "why": "real word too",
                        "kind": "long s", "n": n})
        elif ns * 3 < n and (len(w) < 5 or ns < 5) and s not in lexicon:
            log.append({"word": w, "new": s, "kept": False, "why": "the f form is more frequent",
                        "kind": "long s", "n": n})
        else:
            mapping[w] = s
            log.append({"word": w, "new": s, "kept": True, "why": "book" if ns >= 2 else "lexicon",
                        "kind": "long s", "n": n})

    def fix(m):
        tok = m.group(0)
        target = mapping.get(tok.lower())
        if target is None:
            return tok
        if "ß" in tok or "ſ" in tok:
            return tok.replace("ß", "ss").replace("ſ", "s")
        return "".join("s" if c == "f" and t == "s" else c for c, t in zip(tok, target))
    return [WORD.sub(fix, t) for t in texts], sorted(log, key=lambda r: -r["n"])


# ---------------------------------------------------------------- cut and glued words

# endings a space often cuts from their word (« autre ment »), seldom words themselves
SUFFIXES = {"ment", "mens", "ments", "ent", "ient", "oient", "rent", "oit", "tion", "tions",
            "ance", "ances", "ité", "eur", "eurs", "ture", "tures"}
OPENERS = {"si", "et", "au", "aux", "en", "le", "la", "les", "de", "du", "des", "que", "quant",
           "or", "puis", "ce", "il", "ils", "on", "car", "mais", "à", "a", "ne", "se", "sur",
           "par", "pour"}
PAIR = re.compile(r"(?<![\w-])([^\W\d_]+)[  ](?=([^\W\d_]+)(?![\w'’-]))")


def words(texts: list[str], lexicon=frozenset()) -> tuple[list[str], list[dict]]:
    """Join the words OCR cut with a space, split those it glued."""
    counts, pairs, lower_first, triples = Counter(), Counter(), Counter(), Counter()
    for t in texts:
        ws = WORD.findall(t)
        counts.update(w.lower() for w in ws)
        lower_first.update(w.lower() for w in ws if w[:1].islower())
        pairs.update((m.group(1).lower(), m.group(2).lower()) for m in PAIR.finditer(t))
        lw = [w.lower() for w in ws]
        triples.update(zip(lw, lw[1:], lw[2:]))
    free = Counter(counts)            # occurrences outside the pairs that make a word
    for (x, y), n in pairs.items():
        if counts[x + y] >= 2:
            free[x] -= n
            free[y] -= n
    decided: dict = {}

    def decide(a, b):
        key = (a.lower(), b.lower())
        if key in decided:
            return decided[key]
        whole = key[0] + key[1]
        n_whole, n_pair = counts[whole], pairs[key]

        def alone(w):                 # never alone elsewhere in the book
            return free[w] < 2 and w not in lexicon
        if b[:1].isupper() and not a.isupper():
            r = (False, "capital second piece")
        elif a[:1].islower() and n_whole and not lower_first[whole]:
            r = (False, "the whole word is a name")
        elif key[1] in SUFFIXES and key[0] not in SUFFIXES and n_whole >= 5 \
                and n_whole >= 5 * n_pair:
            r = (True, f"cut ending, « {whole} » in the book {n_whole} times")
        elif not (alone(key[0]) or alone(key[1])):
            r = (False, "two words")
        elif n_whole >= 2 and n_pair <= n_whole:
            r = (True, f"« {whole} » in the book {n_whole} times")
        elif whole in lexicon and alone(key[0]) and alone(key[1]):
            r = (True, "a word of the lexicon, neither piece is a word")
        else:
            r = (False, "not sure")
        decided[key] = r
        return r

    log: dict[tuple, dict] = {}

    def join(m):
        x, y = m.group(1), m.group(2)
        if counts[(x + y).lower()] == 0 and (x + y).lower() not in lexicon:
            return m.group(0)
        ok, why = decide(x, y)
        if not ok and (counts[(x + y).lower()] < 2 or why == "two words"):
            return m.group(0)        # listed only when in doubt and the whole word is real
        entry = log.setdefault((f"{x} {y}", x + y), {"word": f"{x} {y}", "new": x + y, "kept": ok,
                                                    "why": why, "kind": "cut word", "n": 0})
        entry["n"] += 1
        return x if ok else m.group(0)          # the space goes; y follows already
    texts = [PAIR.sub(join, t) for t in texts]

    def splits(w):
        lw, out = w.lower(), []
        for i in range(2, len(w) - 1):
            a, b = lw[:i], lw[i:]
            if counts[a] >= 5 and counts[b] >= 5 and pairs[(a, b)] >= 2:
                out.append((w[:i], w[i:]))
        if not out:
            for i in range(2, len(w) - 3):
                for j in range(i + 2, len(w) - 1):
                    a, b, c = lw[:i], lw[i:j], lw[j:]
                    if min(counts[a], counts[b], counts[c]) >= 5 and triples[(a, b, c)] >= 2:
                        out.append((w[:i], w[i:j], w[j:]))
        return out

    def unglue(m):
        w = m.group(0)
        lw = w.lower()
        if len(w) < 5 or w.isupper() or counts[lw] > 1 or lw in lexicon:
            return w
        cand = splits(w)
        if len(cand) != 1:
            return w
        parts = cand[0]
        if w[:1].isupper() and parts[0].lower() not in OPENERS:
            return w                              # a name (« Pasque »)
        if re.search(r"[ae]ns$", lw) and (lw[:-1] + "ts") in lexicon:
            return w                              # an old plural (passans)
        new = " ".join(parts)
        entry = log.setdefault((w, new), {"word": w, "new": new, "kept": True,
                                          "why": "glued words of the book", "kind": "glued word",
                                          "n": 0})
        entry["n"] += 1
        return new
    texts = [WORD.sub(unglue, t) for t in texts]
    return texts, sorted(log.values(), key=lambda r: (not r["kept"], -r["n"]))


# ---------------------------------------------------------------- all

def finish(texts: list[str], typography: bool = True, old_print: bool = False,
           cut_words: bool = True, lexicon=frozenset(),
           modern: bool = False) -> tuple[list[str], list[dict]]:
    """The paragraphs of a book finished, and the list of the changes;
    modern: the spelling of the 17th and 18th centuries modernised
    (iak.modernise), after the long s and the cut words."""
    log: list[dict] = []
    if old_print:
        texts, more = long_s(texts, lexicon)
        log += more
    if cut_words:
        texts, more = words(texts, lexicon)
        log += more
    if modern:
        from .modernise import modernise
        texts, more = modernise(texts, lexicon)
        log += more
    if typography:
        new = [typo(t) for t in texts]
        n = sum(a != b for a, b in zip(texts, new))
        if n:
            log.append({"word": "", "new": "", "kept": True, "kind": "typography", "n": n,
                        "why": f"French typography in {n} paragraph(s)"})
        texts = new
    return texts, log
