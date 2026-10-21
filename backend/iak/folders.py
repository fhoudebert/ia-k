"""Folders followed by the library: all their documents imported (copied or
only referenced) into a collection named after the folder, and, when the
folder is watched, the files added or changed later imported by themselves.

A scan imports what is new or changed since the last one (size or date),
which keeps it quick on a big folder. A changed file comes in as a new
document; the old version is removed when it came from this folder and was
not corrected by hand. A file deleted from the folder leaves its document
in the library (with its corrections and analyses); only the folder forgets
it. Files still being written (changed in the last seconds), hidden files
and temporary ones (~$..., .part, AppleDouble ._...) are left for later.

Paths: a folder on the drive that holds IA-K (the key) is stored relative to
the IA-K folder, so that it is found again whatever the drive's letter or
mount point on another computer.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import collection, extract

ROOT = Path(__file__).resolve().parents[2]        # the IA-K folder
SETTLE = 5.0                  # seconds without change before a file is imported
SKIP_DIRS = {"$recycle.bin", "system volume information", "__macosx", ".git", ".trash",
             ".trashes", ".spotlight-v100", ".fseventsd", "node_modules", "__pycache__"}
TEMP_SUFFIXES = (".part", ".tmp", ".crdownload", ".download", ".partial", ".swp")


class FolderError(ValueError):
    pass


def store_path(path: str | Path, root: Path = ROOT) -> str:
    """The path to keep: relative to the IA-K folder when on the same drive or
    key (the IA-K folder's parent), else absolute."""
    p = Path(path).expanduser().resolve()
    try:
        p.relative_to(root.parent)
        return os.path.relpath(p, root)
    except ValueError:
        return str(p)


def resolve(stored: str, root: Path = ROOT) -> Path:
    p = Path(stored)
    return p if p.is_absolute() else (root / p).resolve()


def _row(con, folder_id: int):
    row = con.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise KeyError(f"no folder {folder_id}")
    return row


def public(con, row, root: Path = ROOT) -> dict:
    d = dict(row)
    d["last_result"] = json.loads(d.get("last_result") or "{}")
    path = resolve(row["path"], root)
    d["full_path"] = str(path)
    d["available"] = path.is_dir()
    d["files"] = con.execute("SELECT count(*) FROM folder_files WHERE folder_id=?",
                             (row["id"],)).fetchone()[0]
    c = con.execute("SELECT name FROM collections WHERE id=?",
                    (row["collection_id"],)).fetchone() if row["collection_id"] else None
    d["collection"] = c[0] if c else None
    for k in ("copy", "recursive", "watch"):
        d[k] = bool(d[k])
    return d


def all_(con, root: Path = ROOT) -> list[dict]:
    return [public(con, r, root) for r in
            con.execute("SELECT * FROM folders ORDER BY created_at").fetchall()]


def add(lib, path: str | Path, copy: bool = False, recursive: bool = True,
        watch: bool = True, name: str | None = None, root: Path = ROOT) -> dict:
    """Follow a folder; its collection is made (or the existing one of that
    name is used). The first scan is the caller's: scan()."""
    full = Path(path).expanduser()
    if not full.is_dir():
        raise FolderError(f"not a folder: {full}")
    full = full.resolve()
    if full == lib.folder.resolve() or lib.folder.resolve() in full.parents:
        raise FolderError("this is the library's own folder")
    stored = store_path(full, root)
    if lib.con.execute("SELECT 1 FROM folders WHERE path=?", (stored,)).fetchone():
        raise FolderError(f"this folder is already followed: {full}")
    name = (name or full.name or str(full)).strip()
    try:
        coll = collection.by_name(lib.con, name)
    except KeyError:
        coll = collection.create(lib.con, name)
    with lib.con:
        cur = lib.con.execute(
            "INSERT INTO folders (path, collection_id, copy, recursive, watch, created_at)"
            " VALUES (?,?,?,?,?,?)", (stored, coll["id"], int(copy), int(recursive),
                                      int(watch), time.time()))
    return public(lib.con, _row(lib.con, cur.lastrowid), root)


def update(lib, folder_id: int, root: Path = ROOT, **fields) -> dict:
    _row(lib.con, folder_id)
    sets = {k: int(bool(v)) for k, v in fields.items()
            if k in ("copy", "recursive", "watch") and v is not None}
    if sets:
        with lib.con:
            lib.con.execute(f"UPDATE folders SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                            (*sets.values(), folder_id))
    return public(lib.con, _row(lib.con, folder_id), root)


def remove(lib, folder_id: int) -> None:
    """Stop following a folder; its documents and collection stay."""
    _row(lib.con, folder_id)
    with lib.con:
        lib.con.execute("DELETE FROM folders WHERE id=?", (folder_id,))


def _skip_name(name: str) -> bool:
    low = name.lower()
    return (name.startswith((".", "~$")) or low.endswith(TEMP_SUFFIXES)
            or low in ("thumbs.db", "desktop.ini"))


def walk(path: Path, recursive: bool = True):
    """(relative path with "/", absolute path, size, mtime) of the files IA-K
    can import."""
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in sorted(entries, key=lambda e: e.name):
            if _skip_name(e.name):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if recursive and e.name.lower() not in SKIP_DIRS:
                        stack.append(Path(e.path))
                    continue
                if not e.is_file() or extract.kind_of(e.name) is None:
                    continue
                st = e.stat()
            except OSError:
                continue
            p = Path(e.path)
            yield p.relative_to(path).as_posix(), p, st.st_size, st.st_mtime


def signature(lib, folder_id: int, root: Path = ROOT) -> tuple | None:
    """A cheap fingerprint of the folder's files (None when absent): the
    watcher scans only when it changes."""
    row = _row(lib.con, folder_id)
    path = resolve(row["path"], root)
    if not path.is_dir():
        return None
    n, size, newest = 0, 0, 0.0
    for _, _, s, m in walk(path, bool(row["recursive"])):
        n, size, newest = n + 1, size + s, max(newest, m)
    return n, size, newest


def _edited(lib, doc_id: int) -> bool:
    return bool(lib.con.execute("SELECT 1 FROM units WHERE doc_id=? AND edited=1 LIMIT 1",
                                (doc_id,)).fetchone())


def scan(lib, folder_id: int, progress=None, cancelled=None, settle: float = SETTLE,
         root: Path = ROOT) -> dict:
    """Import what is new or changed in a folder."""
    row = _row(lib.con, folder_id)
    path = resolve(row["path"], root)
    out = {"folder": folder_id, "path": str(path), "found": 0, "imported": 0,
           "duplicates": 0, "unchanged": 0, "changed": 0, "replaced": 0, "errors": 0,
           "waiting": 0, "gone": 0, "cancelled": False, "available": path.is_dir(),
           "messages": []}
    if not out["available"]:
        return out
    known = {r["rel_path"]: r for r in lib.con.execute(
        "SELECT * FROM folder_files WHERE folder_id=?", (folder_id,))}
    files = list(walk(path, bool(row["recursive"])))
    out["found"] = len(files)
    seen = set()
    now = time.time()
    coll = row["collection_id"]
    backed_up = False
    for i, (rel, full, size, mtime) in enumerate(files, 1):
        if cancelled and cancelled():
            out["cancelled"] = True
            break
        if progress:
            progress(i, len(files))
        seen.add(rel)
        rec = known.get(rel)
        doc_alive = rec is not None and rec["doc_id"] is not None and \
            lib.document(rec["doc_id"]) is not None
        if rec is not None and rec["size"] == size and abs(rec["mtime"] - mtime) < 1 \
                and (doc_alive or rec["status"] == "error"):
            out["unchanged"] += 1
            if doc_alive and coll:
                collection.add(lib.con, coll, [rec["doc_id"]])
            continue
        if now - mtime < settle:
            out["waiting"] += 1                  # still being written: next time
            continue
        r = lib.import_file(full, copy=bool(row["copy"]))
        if r.status == "error":
            out["errors"] += 1
            out["messages"].append(f"{rel}: {r.message}")
        elif r.status == "duplicate":
            out["duplicates"] += 1
        else:
            out["imported"] += 1
        if rec is not None and doc_alive:
            out["changed"] += 1
            old = rec["doc_id"]
            # the old version goes when it came from this file and nobody
            # corrected it; otherwise it stays, with its corrections
            if old != r.doc_id and r.status != "error":
                d = lib.document(old)
                if d and Path(d["source_path"]) == full.resolve() and not _edited(lib, old):
                    if not backed_up:
                        lib.backup()
                        backed_up = True
                    lib.delete(old, backup=False)
                    out["replaced"] += 1
        if r.doc_id and coll and r.status != "error":
            collection.add(lib.con, coll, [r.doc_id])
        with lib.con:
            lib.con.execute(
                "INSERT INTO folder_files (folder_id, rel_path, size, mtime, doc_id, status)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(folder_id, rel_path) DO UPDATE SET"
                " size=excluded.size, mtime=excluded.mtime, doc_id=excluded.doc_id,"
                " status=excluded.status",
                (folder_id, rel, size, mtime, r.doc_id or None, r.status))
    if not out["cancelled"]:
        gone = [rel for rel in known if rel not in seen]
        out["gone"] = len(gone)
        with lib.con:
            lib.con.executemany("DELETE FROM folder_files WHERE folder_id=? AND rel_path=?",
                                [(folder_id, rel) for rel in gone])
    out["messages"] = out["messages"][:20]
    with lib.con:
        lib.con.execute("UPDATE folders SET last_scan=?, last_result=? WHERE id=?",
                        (time.time(), json.dumps({k: v for k, v in out.items()
                                                  if k not in ("path",)}), folder_id))
    return out


def original_for(con, doc_id: int, root: Path = ROOT) -> Path | None:
    """Where a referenced document's file is now, through the folder it came
    from (the folder may be on another drive letter or mount point)."""
    for r in con.execute(
            "SELECT f.path, ff.rel_path FROM folder_files ff JOIN folders f ON f.id = ff.folder_id"
            " WHERE ff.doc_id=?", (doc_id,)):
        p = resolve(r["path"], root) / r["rel_path"]
        if p.is_file():
            return p
    return None
