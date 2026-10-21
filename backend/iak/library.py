"""The document library: import, list, correct, reindex, delete.

Layout of a library folder (its location is a preference; it does not have
to be on the key):
    iak.sqlite          the database
    originals/ab/abcd…  copies of imported files, named by content hash
    backups/            database copies made before destructive operations
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import chunk, db, embed, extract, ocr, search


@dataclass
class ImportResult:
    doc_id: int
    status: str            # imported | duplicate | error
    name: str
    message: str = ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Library:
    def __init__(self, folder: str | Path, ocr_langs: str = ocr.DEFAULT_LANGS,
                 tesseract: str | None = None, vectors=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.con: sqlite3.Connection = db.connect(self.folder / "iak.sqlite")
        self.opts = extract.Options(langs=ocr_langs, tesseract=tesseract)
        if vectors is not None:          # an index shared between connections (API)
            self._vindex = vectors

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ import

    def import_file(self, path: str | Path, copy: bool = True) -> ImportResult:
        """Import one file. Identical content is never imported twice; files
        with the same name but different content are both kept."""
        path = Path(path).resolve()
        kind = extract.kind_of(path)
        if kind is None:
            return ImportResult(0, "error", path.name, f"unsupported type {path.suffix}")
        digest = sha256_file(path)
        row = self.con.execute("SELECT id, name, status FROM documents WHERE sha256 = ?",
                               (digest,)).fetchone()
        if row and row["status"] == "error":
            # a failed import is retried rather than reported as a duplicate
            self.delete(row["id"], backup=False)
        elif row:
            return ImportResult(row["id"], "duplicate", path.name,
                                f"same content as document {row['id']} ({row['name']})")
        stored = None
        if copy:
            stored = self.folder / "originals" / digest[:2] / f"{digest}{path.suffix.lower()}"
            if not stored.exists():
                db.copy_into(path, stored)
        with self.con:
            cur = self.con.execute(
                "INSERT INTO documents (sha256, name, kind, size, source_path, stored_path,"
                " folder, added_at, status) VALUES (?,?,?,?,?,?,?,?, 'extracting')",
                (digest, path.name, kind, path.stat().st_size, str(path),
                 str(stored.relative_to(self.folder)) if stored else None,
                 path.parent.name, time.time()))
            doc_id = cur.lastrowid
        try:
            self._extract_into(doc_id, self.original_path(doc_id))
        except Exception as exc:  # keep the record, show the error
            with self.con:
                self.con.execute("UPDATE documents SET status='error', error=? WHERE id=?",
                                 (str(exc)[:500], doc_id))
            return ImportResult(doc_id, "error", path.name, str(exc))
        warnings = json.loads(self.document(doc_id)["meta"]).get("warnings", [])
        return ImportResult(doc_id, "imported", path.name, "; ".join(warnings))

    def _extract_into(self, doc_id: int, path: Path, keep_edits: bool = False) -> None:
        result = extract.extract(path, self.opts)
        edited = {}
        if keep_edits:
            edited = {r["ord"]: r for r in self.con.execute(
                "SELECT ord, text FROM units WHERE doc_id=? AND edited=1", (doc_id,))}
        with self.con:
            self.con.execute("DELETE FROM units WHERE doc_id=?", (doc_id,))
            for ord_, u in enumerate(result.units):
                text = edited[ord_]["text"] if ord_ in edited else u.text
                self.con.execute(
                    "INSERT INTO units (doc_id, ord, label, locator, text, text_extracted,"
                    " source, edited, ocr_conf, width, height, boxes)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, ord_, u.label, json.dumps(u.locator, ensure_ascii=False),
                     text, u.text, u.source, 1 if ord_ in edited else 0, u.ocr_conf,
                     u.width, u.height,
                     json.dumps(u.boxes, ensure_ascii=False) if u.boxes is not None else None))
            self.con.execute("UPDATE documents SET meta=?, status='extracted', error=NULL"
                             " WHERE id=?", (json.dumps(result.meta, ensure_ascii=False), doc_id))
        self.reindex(doc_id)

    # ------------------------------------------------------------------ index

    def reindex(self, doc_id: int, unit_ids: list[int] | None = None) -> int:
        """Rebuild the passages (and so the search index) of a document, or of
        some of its units, from the current unit texts. Returns passage count."""
        q = "SELECT id, text FROM units WHERE doc_id=?"
        args: list = [doc_id]
        if unit_ids:
            q += f" AND id IN ({','.join('?' * len(unit_ids))})"
            args += unit_ids
        units = self.con.execute(q, args).fetchall()
        with self.con:
            for u in units:
                self.con.execute("DELETE FROM passages WHERE unit_id=?", (u["id"],))
                for i, p in enumerate(chunk.split(u["text"])):
                    self.con.execute(
                        "INSERT INTO passages (doc_id, unit_id, ord, text, start, end)"
                        " VALUES (?,?,?,?,?,?)", (doc_id, u["id"], i, p.text, p.start, p.end))
            self.con.execute("UPDATE documents SET status='indexed' WHERE id=?", (doc_id,))
        return self.con.execute("SELECT count(*) FROM passages WHERE doc_id=?",
                                (doc_id,)).fetchone()[0]

    def reextract(self, doc_id: int, keep_edits: bool = True) -> None:
        """Run extraction (and OCR) again, e.g. after changing OCR languages."""
        self._extract_into(doc_id, self.original_path(doc_id), keep_edits=keep_edits)

    def set_unit_text(self, unit_id: int, text: str) -> None:
        """Save a user correction of a page or section and reindex only it."""
        row = self.con.execute("SELECT doc_id FROM units WHERE id=?", (unit_id,)).fetchone()
        if row is None:
            raise KeyError(f"no unit {unit_id}")
        with self.con:
            self.con.execute("UPDATE units SET text=?, edited=1 WHERE id=?", (text, unit_id))
        self.reindex(row["doc_id"], [unit_id])

    # ------------------------------------------------------------------ query

    def documents(self, kind: str | None = None) -> list[sqlite3.Row]:
        q = ("SELECT d.*, (SELECT count(*) FROM units u WHERE u.doc_id=d.id) AS n_units,"
             " (SELECT count(*) FROM units u WHERE u.doc_id=d.id AND u.source='ocr') AS n_ocr,"
             " (SELECT count(*) FROM passages p WHERE p.doc_id=d.id) AS n_passages"
             " FROM documents d")
        if kind:
            return self.con.execute(q + " WHERE d.kind=? ORDER BY d.added_at", (kind,)).fetchall()
        return self.con.execute(q + " ORDER BY d.added_at").fetchall()

    def document(self, doc_id: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def units(self, doc_id: int) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM units WHERE doc_id=? ORDER BY ord",
                                (doc_id,)).fetchall()

    def original_path(self, doc_id: int) -> Path:
        row = self.document(doc_id)
        if row is None:
            raise KeyError(f"no document {doc_id}")
        if row["stored_path"]:
            return self.folder / row["stored_path"]
        source = Path(row["source_path"])
        if not source.exists():
            # referenced from a followed folder: maybe under another drive
            # letter or mount point on this computer
            from . import folders
            moved = folders.original_for(self.con, doc_id)
            if moved is not None:
                return moved
        return source

    # ------------------------------------------------------------------ delete

    def delete(self, doc_id: int, backup: bool = True) -> None:
        """Remove a document, its units, passages and stored copy."""
        row = self.document(doc_id)
        if row is None:
            raise KeyError(f"no document {doc_id}")
        if backup:
            db.backup(self.con, self.folder / "backups")
        with self.con:
            self.con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        if row["stored_path"]:
            stored = self.folder / row["stored_path"]
            try:
                stored.unlink()
            except OSError:
                pass

    def backup(self) -> Path:
        return db.backup(self.con, self.folder / "backups")

    # ------------------------------------------------------------- semantic

    @property
    def vectors(self) -> embed.VectorIndex:
        if not hasattr(self, "_vindex"):
            self._vindex = embed.VectorIndex(self.con)
        return self._vindex

    def embed_pending(self, embedder: embed.Embedder, progress=None) -> int:
        """Check the embedding model, then embed the passages that lack a
        vector. Raises embed.EmbeddingMismatch for another model."""
        embed.check_model(self.con, embedder)
        return embed.embed_pending(self.con, embedder, progress)

    def rebuild_vectors(self, embedder: embed.Embedder, progress=None) -> int:
        """Drop every vector and embed everything again (model change)."""
        self.backup()
        embed.clear(self.con)
        embedder._checked = None
        return self.embed_pending(embedder, progress)

    def find(self, query: str, limit: int = 20, filters: search.Filters | None = None,
             embedder: embed.Embedder | None = None, mode: str = "auto") -> tuple[list, str]:
        """Search passages. mode: auto (hybrid when possible), hybrid, sem, kw.
        Returns (hits, mode actually used)."""
        if mode == "kw" or (mode == "auto" and (embedder is None or not len(self.vectors))):
            return search.search(self.con, query, limit, filters=filters), "kw"
        if embedder is None:
            raise ValueError("semantic search needs an embedding server")
        embed.check_model(self.con, embedder)
        qvec = embedder.query(query)
        if mode == "sem":
            return search.semantic(self.con, self.vectors, qvec, limit, filters), "sem"
        return search.hybrid(self.con, query, self.vectors, qvec, limit, filters), "hybrid"

    # ------------------------------------------------------------- highlights

    def passage_boxes(self, passage_id: int) -> dict:
        """Where a passage is on its page: the word boxes of the unit that fall
        inside the passage, merged into one rectangle per line. Words are
        matched in order against the unit text; words the user corrected
        away are simply not highlighted."""
        p = self.con.execute("SELECT unit_id, start, end FROM passages WHERE id=?",
                             (passage_id,)).fetchone()
        if p is None:
            raise KeyError(f"no passage {passage_id}")
        u = self.con.execute("SELECT text, boxes, width, height FROM units WHERE id=?",
                             (p["unit_id"],)).fetchone()
        out = {"unit_id": p["unit_id"], "width": u["width"], "height": u["height"],
               "rects": []}
        if not u["boxes"]:
            return out
        text, cursor, words = u["text"], 0, []
        for b in json.loads(u["boxes"]):
            w = str(b[4])
            i = text.find(w, cursor)
            if i < 0 or i - cursor > 300:      # not found nearby: skip this word
                continue
            cursor = i + len(w)
            if cursor > p["start"] and i < p["end"]:
                words.append(b[:4])
        lines: list[list[float]] = []
        for x0, y0, x1, y1 in words:
            last = lines[-1] if lines else None
            if last and abs(y0 - last[1]) < (y1 - y0) / 2 and x0 >= last[0] - 2:
                last[2], last[3] = max(last[2], x1), max(last[3], y1)
                last[1] = min(last[1], y0)
            else:
                lines.append([x0, y0, x1, y1])
        out["rects"] = [[round(v, 1) for v in r] for r in lines]
        return out