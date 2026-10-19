"""Passage search: keywords (SQLite FTS5, BM25), meaning (embeddings), and
both fused (hybrid, the default when an embedding server is available).

Keywords: the query is cut into words; each word is searched as such (accents
ignored), a word ending with * as a prefix, and "quoted phrases" as phrases.
Passages matching more words rank higher.

Hybrid: the keyword list and the semantic list are merged by reciprocal rank
fusion (each list gives 1 / (60 + rank)); a passage found by both comes first.
Keywords catch names, numbers and exact terms, embeddings catch reformulations
and old spellings.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

# frequent words that only add noise to an OR query
STOPWORDS = set("""
a à au aux avec ce ces cette dans de des du elle en est et il ils je la le les
leur lui ma mais me même mes mon ne nos notre nous on ou où par pas pour qu que
qui sa se ses son sont sur ta te tes ton tu un une vos votre vous y quel quelle
quels quelles comment combien pourquoi quand
the of and to in is are was were be by for on at as it its this that these
those with from or an not what which who how why when where do does did
""".split())


@dataclass
class Hit:
    passage_id: int
    doc_id: int
    unit_id: int
    doc_name: str
    label: str            # citation of the unit: "p. 12", "chap. 3"
    text: str             # the whole passage
    snippet: str          # matched words between [ and ]
    score: float          # BM25 (keywords), cosine (meaning) or fused score
    via: str = "kw"       # kw, sem, or kw+sem


def fts_query(text: str) -> str:
    """Turn user text into a safe FTS5 query (an OR of terms and phrases)."""
    parts: list[str] = []
    for phrase in re.findall(r'"([^"]+)"', text):
        words = re.findall(r"\w+", phrase)
        if words:
            parts.append('"' + " ".join(words) + '"')
    rest = re.sub(r'"[^"]*"', " ", text)
    for m in re.finditer(r"(\w+)(\*?)", rest):
        word, star = m.group(1), m.group(2)
        if word.lower() in STOPWORDS or (len(word) < 2 and not word.isdigit()):
            continue
        parts.append(f'"{word}"' + ("*" if star else ""))
    seen, out = set(), []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return " OR ".join(out)


@dataclass
class Filters:
    doc_ids: list[int] | None = None
    kinds: list[str] | None = None
    folder: str | None = None
    added_after: float | None = None
    added_before: float | None = None

    def sql(self) -> tuple[str, list]:
        sql, args = [], []
        if self.doc_ids:
            sql.append(f"AND p.doc_id IN ({','.join('?' * len(self.doc_ids))})")
            args += self.doc_ids
        if self.kinds:
            sql.append(f"AND d.kind IN ({','.join('?' * len(self.kinds))})")
            args += self.kinds
        if self.folder:
            sql.append("AND d.folder = ?")
            args.append(self.folder)
        if self.added_after is not None:
            sql.append("AND d.added_at >= ?")
            args.append(self.added_after)
        if self.added_before is not None:
            sql.append("AND d.added_at < ?")
            args.append(self.added_before)
        return "\n".join(sql), args


def search(con: sqlite3.Connection, query: str, limit: int = 20,
           doc_ids: list[int] | None = None, kinds: list[str] | None = None,
           folder: str | None = None, added_after: float | None = None,
           added_before: float | None = None, filters: Filters | None = None) -> list[Hit]:
    """Keyword search."""
    f = filters or Filters(doc_ids, kinds, folder, added_after, added_before)
    q = fts_query(query)
    if not q:
        return []
    where, args = f.sql()
    rows = con.execute(f"""
        SELECT p.id, p.doc_id, p.unit_id, p.text, d.name, u.label,
               snippet(passages_fts, 0, '[', ']', '…', 24) AS snip,
               -bm25(passages_fts) AS score
        FROM passages_fts
        JOIN passages p ON p.id = passages_fts.rowid
        JOIN units u ON u.id = p.unit_id
        JOIN documents d ON d.id = p.doc_id
        WHERE passages_fts MATCH ? {where}
        ORDER BY bm25(passages_fts) LIMIT ?""", [q, *args, limit]).fetchall()
    return [Hit(r[0], r[1], r[2], r[4], r[5], r[3], r[6], round(r[7], 3)) for r in rows]


def _short_snippet(text: str, n: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


# Semantic search always returns neighbours, even unrelated ones. With
# EmbeddingGemma, related passages score about 0.25-0.45 against a query and
# unrelated ones about 0-0.1: keep those above MIN_COSINE and at least half
# as close as the best one.
MIN_COSINE = 0.10
RELATIVE_CUTOFF = 0.5


def semantic(con: sqlite3.Connection, index, qvec, limit: int = 20,
             filters: Filters | None = None, min_cosine: float = MIN_COSINE,
             relative: float = RELATIVE_CUTOFF) -> list[Hit]:
    """Meaning search: the passages whose vectors are closest to the query's."""
    f = filters or Filters()
    # take more than needed, the filters are applied afterwards
    scored = index.search(qvec, limit * 5 if (f.doc_ids or f.kinds or f.folder
                                                or f.added_after or f.added_before) else limit)
    if not scored:
        return []
    floor = max(min_cosine, scored[0][1] * relative)
    scored = [(pid, sc) for pid, sc in scored if sc >= floor]
    if not scored:
        return []
    where, args = f.sql()
    ids = [pid for pid, _ in scored]
    rows = con.execute(f"""
        SELECT p.id, p.doc_id, p.unit_id, p.text, d.name, u.label
        FROM passages p
        JOIN units u ON u.id = p.unit_id
        JOIN documents d ON d.id = p.doc_id
        WHERE p.id IN ({','.join('?' * len(ids))}) {where}""", [*ids, *args]).fetchall()
    by_id = {r[0]: r for r in rows}
    out = []
    for pid, score in scored:
        r = by_id.get(pid)
        if r:
            out.append(Hit(r[0], r[1], r[2], r[4], r[5], r[3], _short_snippet(r[3]),
                           round(score, 4), "sem"))
        if len(out) >= limit:
            break
    return out


RRF_K = 60


def hybrid(con: sqlite3.Connection, query: str, index, qvec, limit: int = 20,
           filters: Filters | None = None, depth: int = 40) -> list[Hit]:
    """Keyword and meaning results fused by reciprocal rank."""
    kw = search(con, query, depth, filters=filters)
    sem = semantic(con, index, qvec, depth, filters)
    fused: dict[int, Hit] = {}
    scores: dict[int, float] = {}
    for hits in (kw, sem):
        for rank, h in enumerate(hits):
            scores[h.passage_id] = scores.get(h.passage_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            if h.passage_id in fused:
                fused[h.passage_id].via = "kw+sem"
            else:
                fused[h.passage_id] = h
    out = sorted(fused.values(), key=lambda h: -scores[h.passage_id])[:limit]
    for h in out:
        h.score = round(scores[h.passage_id] * 1000, 2)
    return out


def locator(con: sqlite3.Connection, unit_id: int) -> dict:
    row = con.execute("SELECT locator FROM units WHERE id=?", (unit_id,)).fetchone()
    return json.loads(row[0]) if row else {}
