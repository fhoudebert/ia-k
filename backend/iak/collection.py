"""Collections: named groups of documents, to limit search and questions.

A document can be in several collections; deleting a collection leaves its
documents in the library, deleting a document takes it out of its
collections. Names are unique whatever their case.
"""
from __future__ import annotations

import sqlite3
import time

MAX_NAME = 80


class CollectionError(ValueError):
    pass


def _name(name) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise CollectionError("a collection needs a name")
    if len(name) > MAX_NAME:
        raise CollectionError(f"collection names are at most {MAX_NAME} characters")
    return name


def all_(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT c.id, c.name, c.created_at, count(cd.doc_id) AS n_docs FROM collections c"
        " LEFT JOIN collection_docs cd ON cd.collection_id = c.id"
        " GROUP BY c.id ORDER BY c.name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def get(con, collection_id: int) -> dict:
    row = con.execute("SELECT id, name, created_at FROM collections WHERE id=?",
                      (collection_id,)).fetchone()
    if row is None:
        raise KeyError(f"no collection {collection_id}")
    out = dict(row)
    out["doc_ids"] = [r[0] for r in con.execute(
        "SELECT doc_id FROM collection_docs WHERE collection_id=? ORDER BY added_at",
        (collection_id,))]
    return out


def by_name(con, name: str) -> dict:
    row = con.execute("SELECT id FROM collections WHERE name=? COLLATE NOCASE",
                      (_name(name),)).fetchone()
    if row is None:
        raise KeyError(f"no collection named {name}")
    return get(con, row[0])


def create(con, name: str) -> dict:
    name = _name(name)
    try:
        with con:
            cur = con.execute("INSERT INTO collections (name, created_at) VALUES (?, ?)",
                              (name, time.time()))
    except sqlite3.IntegrityError as exc:
        raise CollectionError(f"a collection named {name} already exists") from exc
    return get(con, cur.lastrowid)


def rename(con, collection_id: int, name: str) -> dict:
    get(con, collection_id)
    name = _name(name)
    try:
        with con:
            con.execute("UPDATE collections SET name=? WHERE id=?", (name, collection_id))
    except sqlite3.IntegrityError as exc:
        raise CollectionError(f"a collection named {name} already exists") from exc
    return get(con, collection_id)


def delete(con, collection_id: int) -> None:
    get(con, collection_id)
    with con:
        con.execute("DELETE FROM collections WHERE id=?", (collection_id,))


def add(con, collection_id: int, doc_ids: list[int]) -> int:
    """Put documents in a collection; returns how many were not in it yet."""
    get(con, collection_id)
    known = {r[0] for r in con.execute(
        f"SELECT id FROM documents WHERE id IN ({','.join('?' * len(doc_ids))})", doc_ids)} \
        if doc_ids else set()
    missing = [d for d in doc_ids if d not in known]
    if missing:
        raise KeyError(f"no document {missing[0]}")
    now = time.time()
    with con:
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO collection_docs (collection_id, doc_id, added_at)"
                        " VALUES (?, ?, ?)", [(collection_id, d, now) for d in doc_ids])
        return con.total_changes - before


def remove(con, collection_id: int, doc_ids: list[int]) -> int:
    get(con, collection_id)
    with con:
        before = con.total_changes
        con.executemany("DELETE FROM collection_docs WHERE collection_id=? AND doc_id=?",
                        [(collection_id, d) for d in doc_ids])
        return con.total_changes - before


def of_documents(con) -> dict[int, list[int]]:
    """{document id: [collection ids]}."""
    out: dict[int, list[int]] = {}
    for doc_id, cid in con.execute("SELECT doc_id, collection_id FROM collection_docs"):
        out.setdefault(doc_id, []).append(cid)
    return out
