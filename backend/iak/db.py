"""SQLite storage: documents, units (pages or sections), passages, FTS5 index.

Vocabulary
  document  an imported file, identified by the SHA-256 of its content
  unit      the citable part of a document: a page for PDF and images, a
            section for EPUB, HTML, DOCX, Markdown, a block of lines for TXT
  passage   a piece of a unit's text (a few hundred words, with overlap),
            the thing that is searched and quoted in answers

The database uses the rollback journal (journal_mode=DELETE), not WAL: it
may live on a USB key formatted in exFAT and be unplugged, and WAL needs
shared memory files that do not suit that.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE documents (
    id          INTEGER PRIMARY KEY,
    sha256      TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,           -- original file name, shown to the user
    kind        TEXT NOT NULL,           -- pdf, image, docx, txt, md, html, epub
    size        INTEGER NOT NULL,
    source_path TEXT NOT NULL,           -- where the file was imported from
    stored_path TEXT,                    -- copy inside the library, NULL if referenced
    folder      TEXT NOT NULL DEFAULT '',-- parent folder name, for filtering
    added_at    REAL NOT NULL,
    status      TEXT NOT NULL,           -- extracting, indexed, error
    error       TEXT,
    meta        TEXT NOT NULL DEFAULT '{}'  -- JSON: title, author, page count...
);

CREATE TABLE units (
    id          INTEGER PRIMARY KEY,
    doc_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,        -- 0-based order in the document
    label       TEXT NOT NULL,           -- "p. 12", "chap. 3", "l. 120"
    locator     TEXT NOT NULL,           -- JSON: {"page": 12} or {"section": 3, ...}
    text        TEXT NOT NULL,           -- current text (user corrections included)
    text_extracted TEXT NOT NULL,        -- text as extracted, kept for comparison
    source      TEXT NOT NULL,           -- native, ocr
    edited      INTEGER NOT NULL DEFAULT 0,  -- 1 once the user corrected the text
    ocr_conf    REAL,                    -- mean Tesseract word confidence 0-100
    width       REAL,                    -- page size in points (PDF, images)
    height      REAL,
    boxes       TEXT,                    -- JSON [[x0,y0,x1,y1,word,conf], ...] in points
    UNIQUE (doc_id, ord)
);

CREATE TABLE passages (
    id          INTEGER PRIMARY KEY,
    doc_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    unit_id     INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,        -- order inside the unit
    text        TEXT NOT NULL,
    start       INTEGER NOT NULL,        -- character offsets in units.text
    end         INTEGER NOT NULL
);
CREATE INDEX passages_unit ON passages(unit_id);
CREATE INDEX passages_doc ON passages(doc_id);

-- Full-text index over passages. External content: the text is stored once,
-- in passages; triggers keep the index in step.
CREATE VIRTUAL TABLE passages_fts USING fts5(
    text,
    content='passages', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER passages_ai AFTER INSERT ON passages BEGIN
    INSERT INTO passages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER passages_ad AFTER DELETE ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER passages_au AFTER UPDATE ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO passages_fts(rowid, text) VALUES (new.id, new.text);
END;
"""


def connect(path: str | os.PathLike) -> sqlite3.Connection:
    """Open (and create or upgrade) the library database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA synchronous = FULL")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        with con:
            con.executescript(SCHEMA)
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version > SCHEMA_VERSION:
        con.close()
        raise RuntimeError(
            f"{path} was created by a newer IA-K (schema {version}); "
            f"this version reads schema {SCHEMA_VERSION}")
    return con


def backup(con: sqlite3.Connection, backup_dir: str | os.PathLike, keep: int = 5) -> Path:
    """Copy the database with SQLite's online backup; keep the last `keep` copies."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"iak-{stamp}.sqlite"
    n = 1
    while target.exists():
        n += 1
        target = backup_dir / f"iak-{stamp}-{n}.sqlite"
    dst = sqlite3.connect(target)
    try:
        con.backup(dst)
    finally:
        dst.close()
    old = sorted(backup_dir.glob("iak-*.sqlite"))[:-keep] if keep > 0 else []
    for f in old:
        try:
            f.unlink()
        except OSError:
            pass
    return target


def copy_into(src: Path, dst: Path) -> None:
    """Copy a file, never replacing an existing one."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise FileExistsError(dst)
    tmp = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
