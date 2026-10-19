"""Semantic search: passage embeddings (EmbeddingGemma through llama-server),
their storage, and the model check that keeps the index consistent.

EmbeddingGemma expects task prefixes: passages are embedded as
"title: <document> | text: <passage>", queries as
"task: search result | query: <question>". Vectors are normalised, so the
cosine similarity is a dot product; they are searched exhaustively with
numpy, which takes a few tens of milliseconds for 100,000 passages.

The index is only valid for the model that built it. The first time, a
fingerprint of the embedding server is stored (model file, dimension and the
vector of a fixed probe sentence); afterwards, a server whose probe vector
differs (cosine below 0.95, i.e. another model, not just another
quantisation of the same one) is refused until the index is rebuilt.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

import numpy as np

from . import db
from .servers import Client, ServerError

QUERY_PREFIX = "task: search result | query: "
PROBE = "Sonde IA-K : registre de reliure d'un atlas ancien, cartes marines."
SAME_MODEL_MIN_COSINE = 0.95


def doc_text(title: str, text: str) -> str:
    title = " ".join((title or "none").split()) or "none"
    return f"title: {title} | text: {text}"


class EmbeddingMismatch(RuntimeError):
    pass


class Embedder:
    """OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(self, client: Client, batch: int = 16):
        self.client = client
        self.batch = batch

    def embed(self, texts: list[str]) -> np.ndarray:
        out = []
        for i in range(0, len(texts), self.batch):
            part = texts[i:i + self.batch]
            data = self.client._call(self.client.v1 + "/embeddings",
                                     {"model": "", "input": part})
            rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
            if len(rows) != len(part):
                raise ServerError(f"embedding server returned {len(rows)} vectors "
                                  f"for {len(part)} texts")
            out.extend(r["embedding"] for r in rows)
        mat = np.asarray(out, dtype=np.float32).reshape(len(texts), -1)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return mat / np.where(norms == 0, 1, norms)

    def query(self, text: str) -> np.ndarray:
        return self.embed([QUERY_PREFIX + text])[0]

    def fingerprint(self) -> dict:
        props = self.client.props()
        name = os.path.basename(props.get("model_path") or "") or self.client.model()
        vec = self.embed([QUERY_PREFIX + PROBE])[0]
        return {"model": name, "dim": int(vec.shape[0]),
                "probe": [round(float(x), 6) for x in vec]}


def check_model(con: sqlite3.Connection, emb: Embedder) -> str:
    """'new' (fingerprint stored now), 'same' or 'variant' (same model, other
    file or quantisation). Raises EmbeddingMismatch for another model."""
    cached = getattr(emb, "_checked", None)
    if cached and cached[0] is con:
        return cached[1]
    result = _check_model(con, emb)
    emb._checked = (con, result)
    return result


def _check_model(con: sqlite3.Connection, emb: Embedder) -> str:
    fp = emb.fingerprint()
    stored = db.get_setting(con, "embedding")
    has_vectors = con.execute("SELECT 1 FROM vectors LIMIT 1").fetchone() is not None
    if stored is None or not has_vectors:
        db.set_setting(con, "embedding", fp)
        return "new"
    if stored["dim"] != fp["dim"]:
        raise EmbeddingMismatch(
            f"the index was built with {stored['model']} ({stored['dim']} dimensions); "
            f"this server has {fp['model']} ({fp['dim']} dimensions)")
    cos = float(np.dot(np.asarray(stored["probe"], np.float32),
                       np.asarray(fp["probe"], np.float32)))
    if cos < SAME_MODEL_MIN_COSINE:
        raise EmbeddingMismatch(
            f"the index was built with {stored['model']}; this server ({fp['model']}) "
            f"gives different vectors (similarity {cos:.2f})")
    return "same" if cos > 0.9999 else "variant"


def pending(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM passages p LEFT JOIN vectors v"
                       " ON v.passage_id = p.id WHERE v.passage_id IS NULL").fetchone()[0]


def embed_pending(con: sqlite3.Connection, emb: Embedder, progress=None,
                  chunk: int = 64) -> int:
    """Embed every passage that has no vector yet. Returns the number done."""
    done = 0
    total = pending(con)
    while True:
        rows = con.execute(
            "SELECT p.id, p.text, d.name, d.meta FROM passages p"
            " JOIN documents d ON d.id = p.doc_id"
            " LEFT JOIN vectors v ON v.passage_id = p.id"
            " WHERE v.passage_id IS NULL ORDER BY p.id LIMIT ?", (chunk,)).fetchall()
        if not rows:
            return done
        texts = [doc_text(_title(r[2], r[3]), r[1]) for r in rows]
        mat = emb.embed(texts)
        with con:
            con.executemany("INSERT OR REPLACE INTO vectors (passage_id, vec) VALUES (?, ?)",
                            [(r[0], mat[i].tobytes()) for i, r in enumerate(rows)])
        _bump(con)
        done += len(rows)
        if progress:
            progress(done, total)


def clear(con: sqlite3.Connection) -> None:
    with con:
        con.execute("DELETE FROM vectors")
    db.set_setting(con, "embedding", None)
    _bump(con)


def _bump(con: sqlite3.Connection) -> None:
    """Record a vector change (also visible to other connections)."""
    db.set_setting(con, "vectors_gen", (db.get_setting(con, "vectors_gen", 0) or 0) + 1)


def _title(name: str, meta_json: str) -> str:
    import json
    try:
        title = json.loads(meta_json or "{}").get("title")
    except ValueError:
        title = None
    return title or os.path.splitext(name)[0]


@dataclass
class VectorIndex:
    """All passage vectors in memory, reloaded when the table changes."""
    con: sqlite3.Connection
    _stamp: tuple | None = None
    _ids: np.ndarray | None = None
    _mat: np.ndarray | None = None

    def _load(self) -> None:
        # Cheap change detection: writes through this connection (total_changes)
        # or committed by another one (data_version). Any write reloads.
        stamp = (self.con.total_changes,
                 self.con.execute("PRAGMA data_version").fetchone()[0])
        if stamp == self._stamp:
            return
        rows = self.con.execute("SELECT passage_id, vec FROM vectors").fetchall()
        if rows:
            self._ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
            self._mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        else:
            self._ids, self._mat = np.zeros(0, np.int64), None
        self._stamp = stamp

    def __len__(self) -> int:
        self._load()
        return int(self._ids.shape[0])

    def search(self, qvec: np.ndarray, k: int = 50) -> list[tuple[int, float]]:
        self._load()
        if self._mat is None or not len(self._ids):
            return []
        scores = self._mat @ qvec.astype(np.float32)
        k = min(k, scores.shape[0])
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(self._ids[i]), float(scores[i])) for i in top]
