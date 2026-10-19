"""Keyword search over passages (SQLite FTS5, BM25 ranking).

The query is cut into words; each word is searched as such (accents ignored),
a word ending with * as a prefix, and "quoted phrases" as phrases. Passages
matching more words rank higher. Semantic search will be added beside this
one and the two lists fused (step 4).
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
    score: float          # BM25, higher is better


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


def search(con: sqlite3.Connection, query: str, limit: int = 20,
           doc_ids: list[int] | None = None, kinds: list[str] | None = None,
           folder: str | None = None, added_after: float | None = None,
           added_before: float | None = None) -> list[Hit]:
    q = fts_query(query)
    if not q:
        return []
    sql = ["""
        SELECT p.id, p.doc_id, p.unit_id, p.text, d.name, u.label,
               snippet(passages_fts, 0, '[', ']', '…', 24) AS snip,
               -bm25(passages_fts) AS score
        FROM passages_fts
        JOIN passages p ON p.id = passages_fts.rowid
        JOIN units u ON u.id = p.unit_id
        JOIN documents d ON d.id = p.doc_id
        WHERE passages_fts MATCH ?"""]
    args: list = [q]
    if doc_ids:
        sql.append(f"AND p.doc_id IN ({','.join('?' * len(doc_ids))})")
        args += doc_ids
    if kinds:
        sql.append(f"AND d.kind IN ({','.join('?' * len(kinds))})")
        args += kinds
    if folder:
        sql.append("AND d.folder = ?")
        args.append(folder)
    if added_after is not None:
        sql.append("AND d.added_at >= ?")
        args.append(added_after)
    if added_before is not None:
        sql.append("AND d.added_at < ?")
        args.append(added_before)
    sql.append("ORDER BY bm25(passages_fts) LIMIT ?")
    args.append(limit)
    rows = con.execute("\n".join(sql), args).fetchall()
    return [Hit(r[0], r[1], r[2], r[4], r[5], r[3], r[6], round(r[7], 3)) for r in rows]


def locator(con: sqlite3.Connection, unit_id: int) -> dict:
    row = con.execute("SELECT locator FROM units WHERE id=?", (unit_id,)).fetchone()
    return json.loads(row[0]) if row else {}
