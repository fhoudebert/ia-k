"""Modern spelling of a French book of the 17th and 18th centuries, book-wide
(after Prescel, https://github.com/fhoudebert/prescel, scripts/
epub_modernise.py, MIT licence, by the same author; the word lists are
Prescel's, in iak/dictionaries/).

- vocabulary: « luy » -> « lui », « mesme » -> « même », « aussi tost » ->
  « aussitôt » (vocabulaire_17_18);
- oi: imperfects and conditionals in -ois, -oit, -oient (« estoit » ->
  « était », « auroient » -> « auraient »): the verb list, then the rule
  -oi- -> -ai- when the result is a word of the lexicon; never « croit »,
  « soit », « mois », « droit »… nor names;
- ez: old plurals in -ez (« bontez » -> « bontés »), never after « vous »,
  before a hyphen (« allez-vous ») nor « nez », « chez », « assez »;
- ants: old plurals in -ans, -ens (« charmans » -> « charmants », « momens »
  -> « moments »), never a modern word (« sens », « gens »);
- erent: the past historic in -erent (« allerent » -> « allèrent »).

As in Prescel, the sure cases are applied and the doubtful ones only listed
(a form that is a modern word too: « teste », tête or the verb?). Without
a lexicon, only the lists and the book's own words decide. The case is kept
(Estoit -> Était, ESTOIT -> ÉTAIT) and only letters change.
"""
from __future__ import annotations

import itertools
import re
from collections import Counter

LETTERS = "A-Za-zÀ-ÖØ-öø-ÿœŒæÆ"
WORD = re.compile(r"[%s]+" % LETTERS)
OI_END = re.compile(r"^(.+?)(ois|oit|oient)$")
# frequent forms (complete dictionaries/verbes_oi.py)
OI_BUILTIN = {
    "estois": "étais", "estoit": "était", "estoient": "étaient",
    "etois": "étais", "etoit": "était", "etoient": "étaient",
    "avois": "avais", "avoit": "avait", "avoient": "avaient",
    "faisois": "faisais", "faisoit": "faisait", "faisoient": "faisaient",
    "disois": "disais", "disoit": "disait", "disoient": "disaient",
    "pouvois": "pouvais", "pouvoit": "pouvait", "pouvoient": "pouvaient",
    "devois": "devais", "devoit": "devait", "devoient": "devaient",
    "voulois": "voulais", "vouloit": "voulait", "vouloient": "voulaient",
    "savois": "savais", "savoit": "savait", "savoient": "savaient",
    "aurois": "aurais", "auroit": "aurait", "auroient": "auraient",
    "serois": "serais", "seroit": "serait", "seroient": "seraient",
    "ferois": "ferais", "feroit": "ferait", "feroient": "feraient",
    "irois": "irais", "iroit": "irait", "iroient": "iraient",
    "viendrois": "viendrais", "viendroit": "viendrait", "viendroient": "viendraient",
}
# « oi » of modern French: never changed, even without a lexicon
OI_KEEP = set("""
mois fois trois bois lois rois vois voit crois croit dois doit sois soit bois boit reçois reçoit
droit droits endroit endroits étroit adroit maladroit exploit exploits toit toits froid
emploi emplois choix poids pois noix voix croix foi soi moi toi loi roi quoi pourquoi
aperçois aperçoit conçois conçoit déçoit perçoit reçoivent doivent voient croient soient
""".split())
# words in -ez that are not old plurals in -és
EZ_KEEP = set("nez chez assez rez lez sez raz".split())
# pronouns between « vous » and the verb: « vous ne les avez »
VOUS_BEFORE = re.compile(r"\bvous(?:\s+|['’])(?:(?:ne|n['’]|pas|le|la|les|l['’]|lui|leur|en|y|me|m['’]|"
                         r"te|t['’]|se|s['’]|nous)(?:\s+|(?<=['’])))*$", re.I)


def _lists() -> tuple[dict, dict, dict, dict]:
    from .dictionaries import pluriels_ants, pluriels_ez, verbes_oi, vocabulaire_17_18

    def low(d):
        return {a.lower(): b for a, b in d.items() if a.lower() != b.lower()}
    return (low(vocabulaire_17_18.VOCABULAIRE_17_18), low(verbes_oi.ANCIEN_FRANCAIS),
            low(pluriels_ez.PLURIELS_EZ), low(pluriels_ants.PLURIELS_ANTS))


def match_case(orig: str, new: str) -> str:
    if len(orig) > 1 and orig.isupper():
        return new.upper()
    if orig[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


def with_accents(word: str, lexicon) -> str | None:
    """The word with the acute accents it lacks (« penitents » -> « pénitents »),
    never on its last syllable; None when no such form is in the lexicon."""
    if not lexicon:
        return None
    pos = [i for i, c in enumerate(word[:-2]) if c == "e"]
    for k in range(1, min(3, len(pos)) + 1):
        for combo in itertools.combinations(pos, k):
            cand = list(word)
            for i in combo:
                cand[i] = "é"
            cand = "".join(cand)
            if cand in lexicon:
                return cand
    return None


def _counts(texts):
    counts, caps = Counter(), Counter()
    for t in texts:
        for m in WORD.finditer(t):
            w = m.group(0)
            counts[w.lower()] += 1
            before = t[max(0, m.start() - 3):m.start()]
            if w[:1].isupper() and not re.search(r"[.!?»]\s*$|^\s*$", before):
                caps[w.lower()] += 1          # a capital inside a sentence: a name?
    return counts, caps


def _name(w, n, caps) -> bool:
    return caps.get(w, 0) >= max(2, 0.6 * n)


def propose_oi(counts, caps, lexicon, verbs) -> list[tuple]:
    rows = []
    for w, n in counts.items():
        m = OI_END.match(w)
        if not m or len(w) < 4:
            continue
        stem, end = m.groups()
        target = verbs.get(w) or OI_BUILTIN.get(w)
        listed = target is not None
        if target is None:
            target = stem + {"ois": "ais", "oit": "ait", "oient": "aient"}[end]
        if not listed and lexicon and target not in lexicon and target.endswith("ait") \
                and target[:-3] + "aît" in lexicon:
            target = target[:-3] + "aît"          # « reconnoit » -> « reconnaît »
        modern = w in OI_KEEP or w in lexicon
        if modern and not listed:
            continue                              # « trois », « droit »: nothing to do
        if modern:
            rows.append((w, target, False, "also a modern word: to check case by case"))
        elif _name(w, n, caps):
            rows.append((w, target, False, "mostly with a capital: a name?"))
        elif listed:
            rows.append((w, target, True, "verb list"))
        elif target in lexicon:
            rows.append((w, target, True, "rule -oi- -> -ai-"))
        else:
            rows.append((w, target, False, f"« {target} » unknown: to check"))
    return rows


def propose_ez(counts, caps, lexicon, plurals) -> list[tuple]:
    rows = []
    for w, n in counts.items():
        if not w.endswith("ez") or len(w) < 4 or w in EZ_KEEP:
            continue
        if w in plurals:
            rows.append((w, plurals[w], True, "plural list"))
            continue
        target = w[:-2] + "és"
        if w.endswith("iez") and lexicon and target not in lexicon and w[:-3] + "yés" in lexicon:
            target = w[:-3] + "yés"               # « envoiez » -> « envoyés »
        known = target in lexicon or counts.get(target, 0) >= 2
        why = "old plural in -ez"
        if not known:
            accented = with_accents(target, lexicon)
            if accented is not None:              # « deputez » -> « députés »
                target, known, why = accented, True, "old plural in -ez, accent put back"
        verb = w in lexicon
        if not known and (verb or not lexicon):
            continue                              # « aviez », « étiez »: verbs
        if _name(w, n, caps):
            rows.append((w, target, False, "mostly with a capital: a name?"))
        elif not known:
            rows.append((w, target, False, f"« {target} » unknown: to check"))
        else:
            rows.append((w, target, True, why + (", kept after « vous »" if verb else "")))
    return rows


def propose_ants(counts, caps, lexicon, plurals) -> list[tuple]:
    rows = []
    for w, n in counts.items():
        if len(w) < 5 or not (w.endswith("ans") or w.endswith("ens")):
            continue
        if w in plurals:
            rows.append((w, plurals[w], True, "plural list"))
            continue
        if w in lexicon or not lexicon and counts.get(w[:-1] + "ts", 0) < 2:
            continue                              # « sens », « gens », « paysans »
        target = w[:-1] + "ts"
        why = "old plural in -ans, -ens"
        if not (target in lexicon or counts.get(target, 0) >= 2):
            accented = with_accents(target, lexicon)
            if accented is None:
                continue
            target, why = accented, why + ", accent put back"
        rows.append((w, target, True, why))
    return rows


def propose_erent(counts, caps, lexicon) -> list[tuple]:
    rows = []
    if not lexicon:
        return rows
    for w, n in counts.items():
        if len(w) < 6 or not (w.endswith("erent") or w.endswith("érent")) or w in lexicon:
            continue                              # « cohérent »: right already
        target = w[:-5] + "èrent"
        why = "past historic in -erent"
        if target not in lexicon:
            accented = with_accents(target, lexicon)
            if accented is None:
                rows.append((w, target, False, f"« {target} » unknown: to check (long s?)"))
                continue
            target, why = accented, why + ", accent put back"
        other = with_accents(w, lexicon)
        if other is not None:
            rows.append((w, target, False, f"also « {other} »: to check case by case"))
        else:
            rows.append((w, target, True, why))
    return rows


def propose_vocab(counts, texts, lexicon, vocab) -> list[tuple]:
    rows = []
    low = "\n".join(texts).lower().replace("’", "'")
    for old, new in vocab.items():
        if WORD.fullmatch(old):
            n = counts.get(old, 0)
        else:
            pat = re.escape(old).replace(r"\ ", r"\s+")
            n = len(re.findall(r"(?<![%s])%s(?![%s])" % (LETTERS, pat, LETTERS), low))
        if not n:
            continue
        if old in lexicon:
            rows.append((old, new, False, "also a modern word: to check"))
        else:
            rows.append((old, new, True, "vocabulary list"))
    return rows


def _ez_verb_here(m, verb: bool) -> bool:
    """An -ez to keep: after « vous », before a hyphen or « vous » (« allez-vous »,
    « où allez vous ? »), at the head of a sentence when it is a verb too."""
    s, a, b = m.string, m.start(), m.end()
    if VOUS_BEFORE.search(s[max(0, a - 40):a]):
        return True
    if b < len(s) and s[b] in "-‑":
        return True
    if re.match(r"\s+vous\b", s[b:b + 8], re.I):
        return True
    return verb and m.group(0)[:1].isupper() and \
        bool(re.search(r"(?:^|[.!?:«»\"“]\s*)$", s[max(0, a - 3):a]))


def modernise(texts: list[str], lexicon=frozenset()) -> tuple[list[str], list[dict]]:
    """The paragraphs of a book in modern spelling, and the list of the
    changes and of the doubtful forms left."""
    vocab, verbs, ez_plurals, ants_plurals = _lists()
    counts, caps = _counts(texts)
    rows = (propose_vocab(counts, texts, lexicon, vocab)
            + propose_oi(counts, caps, lexicon, verbs)
            + propose_ez(counts, caps, lexicon, ez_plurals)
            + propose_ants(counts, caps, lexicon, ants_plurals)
            + propose_erent(counts, caps, lexicon))
    first = {}
    for r in rows:
        first.setdefault(r[0], r)                # the first list that knows a form decides
    rows = list(first.values())
    mapping = {old: new for old, new, ok, _ in rows if ok}
    phrases = sorted((k for k in mapping if not WORD.fullmatch(k)), key=len, reverse=True)
    words = {k: v for k, v in mapping.items() if WORD.fullmatch(k)}
    done, kept = Counter(), Counter()
    phrase_re = None
    if phrases:
        alts = "|".join(re.escape(p).replace("'", "['’]").replace(r"\ ", r"\s+") for p in phrases)
        phrase_re = re.compile(r"(?<![%s])(?:%s)(?![%s])" % (LETTERS, alts, LETTERS), re.I)

    def phrase(m):
        key = re.sub(r"\s+", " ", m.group(0).lower().replace("’", "'"))
        new = mapping.get(key)
        if new is None:
            return m.group(0)
        done[key] += 1
        new = match_case(m.group(0), new)
        return new.replace("'", "’") if "’" in m.group(0) else new

    def word(m):
        w = m.group(0)
        lw = w.lower()
        new = words.get(lw)
        if new is None:
            return w
        s = m.string
        if re.match(r"[aeiouéèêh]", w, re.I) and not re.match(r"[aeiouéèêâîôûh]", new, re.I) and \
                re.search(r"(?<![%s])[LlSsCc]['’]$" % LETTERS, s[max(0, m.start() - 3):m.start()]):
            return w                              # « l'estut »: « l'fallut » is impossible
        if lw.endswith("ez") and _ez_verb_here(m, lw in lexicon):
            kept[lw] += 1
            return w
        done[lw] += 1
        return match_case(w, new)

    out = []
    for t in texts:
        if phrase_re is not None:
            t = phrase_re.sub(phrase, t)
        out.append(WORD.sub(word, t))
    log = []
    for old, new, ok, why in rows:
        n = done[old] if ok else counts.get(old, 0) or 1
        if ok and not n:
            continue
        if kept[old]:
            why += f", kept {kept[old]} time(s) as a verb"
        log.append({"word": old, "new": new, "kept": ok, "why": why, "kind": "modern spelling",
                    "n": n})
    return out, sorted(log, key=lambda r: (not r["kept"], -r["n"]))
