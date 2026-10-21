"""Followed and watched folders."""
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from iak import api, collection, folders, servers
from iak.library import Library

from .test_api import TOKEN, call, wait

OLD = time.time() - 3600              # files written an hour ago: settled


def write(path: Path, text: str, mtime: float | None = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def tree(tmp_path):
    top = tmp_path / "Archives"
    write(top / "registre.txt", "Le registre mentionne l'achat d'un atlas.\n")
    write(top / "notes.md", "# Notes\n\nLe relieur répare l'atlas.\n")
    write(top / "sous" / "journal.txt", "Le relieur a rendu l'atlas au printemps.\n")
    write(top / ".cache.txt", "caché\n")
    write(top / "~$brouillon.txt", "temporaire\n")
    write(top / "photo.xyz", "pas un document\n")
    write(top / "__MACOSX" / "x.txt", "métadonnées\n")
    return top


@pytest.fixture
def lib(tmp_path):
    with Library(tmp_path / "lib") as library:
        yield library


def test_paths(tmp_path):
    root = tmp_path / "key" / "ia-k"
    root.mkdir(parents=True)
    docs = tmp_path / "key" / "docs"
    docs.mkdir()
    # on the key: relative to the IA-K folder, found again wherever the key is
    assert folders.store_path(docs, root) == os.path.join("..", "docs")
    assert folders.store_path(root / "data2", root) == "data2"
    assert folders.resolve("../docs", root) == docs.resolve()
    elsewhere = tmp_path.parent / "elsewhere"
    assert folders.store_path(elsewhere, root) == str(elsewhere.resolve())


def test_add_and_scan(lib, tree):
    f = folders.add(lib, tree)
    assert f["collection"] == "Archives" and f["watch"] and not f["copy"] and f["available"]
    with pytest.raises(folders.FolderError, match="already"):
        folders.add(lib, tree)
    with pytest.raises(folders.FolderError):
        folders.add(lib, tree / "registre.txt")
    with pytest.raises(folders.FolderError):
        folders.add(lib, lib.folder)
    r = folders.scan(lib, f["id"])
    assert (r["found"], r["imported"], r["errors"]) == (3, 3, 0)
    docs = lib.documents()
    assert sorted(d["name"] for d in docs) == ["journal.txt", "notes.md", "registre.txt"]
    assert all(d["stored_path"] is None for d in docs)        # referenced, not copied
    coll = collection.by_name(lib.con, "Archives")
    assert len(coll["doc_ids"]) == 3
    # again: nothing to do
    r = folders.scan(lib, f["id"])
    assert (r["imported"], r["unchanged"]) == (0, 3)


def test_changes(lib, tree):
    f = folders.add(lib, tree, recursive=False, copy=True)
    assert folders.scan(lib, f["id"])["found"] == 2                 # not the subfolder
    old = {d["name"]: d["id"] for d in lib.documents()}
    # a new file still being written waits; a changed file replaces its document
    write(tree / "nouveau.txt", "Un texte tout neuf.\n", mtime=None)
    write(tree / "registre.txt", "Le registre mentionne deux atlas.\n", mtime=OLD + 60)
    r = folders.scan(lib, f["id"])
    assert (r["waiting"], r["changed"], r["replaced"], r["imported"]) == (1, 1, 1, 1)
    assert lib.document(old["registre.txt"]) is None
    assert [h.doc_id for h in lib.find("deux", mode="kw")[0]]
    # a changed file whose document was corrected by hand: the correction stays
    unit = lib.units(old["notes.md"])[0]["id"]
    lib.set_unit_text(unit, "Corrigé à la main.")
    write(tree / "notes.md", "# Notes\n\nUne autre version.\n", mtime=OLD + 120)
    r = folders.scan(lib, f["id"], settle=0)
    assert r["changed"] == 1 and r["replaced"] == 0 and r["imported"] == 2   # + nouveau
    assert lib.document(old["notes.md"]) is not None
    # a deleted file: forgotten by the folder, its document stays
    (tree / "nouveau.txt").unlink()
    r = folders.scan(lib, f["id"])
    assert r["gone"] == 1
    assert any(d["name"] == "nouveau.txt" for d in lib.documents())


def test_absent_folder_and_moved_files(lib, tmp_path, tree):
    root = tmp_path / "ia-k"
    root.mkdir()
    f = folders.add(lib, tree, root=root)
    assert lib.con.execute("SELECT path FROM folders").fetchone()[0] == \
        os.path.join("..", "Archives")
    folders.scan(lib, f["id"], root=root)
    doc = next(d for d in lib.documents() if d["name"] == "journal.txt")
    # the key on another computer: the folder elsewhere, the stored paths stale
    moved = tmp_path / "ailleurs"
    shutil.move(str(tree), str(moved))
    assert folders.scan(lib, f["id"], root=root)["available"] is False
    with lib.con:
        lib.con.execute("UPDATE folders SET path=?", (os.path.join("..", "ailleurs"),))
    assert folders.original_for(lib.con, doc["id"], root) == moved / "sous" / "journal.txt"
    folders.remove(lib, f["id"])
    assert lib.document(doc["id"]) is not None and collection.by_name(lib.con, "Archives")


def test_api_and_watcher(tmp_path, tree):
    app = api.App(tmp_path / "lib", servers.Host("t"), token=TOKEN, watch_seconds=0)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        assert call(port, "POST", "/api/folders", {"path": ""})[0] == 400
        assert call(port, "POST", "/api/folders", {"path": str(tmp_path / "none")})[0] == 400
        s, f, _ = call(port, "POST", "/api/folders", {"path": str(tree), "name": "Fonds 1712"})
        assert s == 201 and f["collection"] == "Fonds 1712"
        j = wait(port, f["job"])
        assert j["status"] == "done" and j["result"]["imported"] == 3
        assert call(port, "POST", "/api/folders", {"path": str(tree)})[0] == 409
        _, lst, _ = call(port, "GET", "/api/folders")
        assert lst[0]["files"] == 3 and lst[0]["last_result"]["imported"] == 3
        # the watcher: nothing changed, nothing queued; a new file, one scan
        assert app.watch_once() == []
        write(tree / "ajout.txt", "Ajouté plus tard.\n")
        assert app.watch_once() == [f["id"]]
        for _ in range(200):
            if not app.scan_active(f["id"]):
                break
            time.sleep(0.05)
        _, docs, _ = call(port, "GET", "/api/documents")
        assert any(d["name"] == "ajout.txt" for d in docs)
        s, f2, _ = call(port, "PUT", f"/api/folders/{f['id']}", {"watch": False})
        assert s == 200 and f2["watch"] is False
        write(tree / "encore.txt", "Encore un.\n")
        assert app.watch_once() == []                      # not watched any more
        s, r, _ = call(port, "POST", f"/api/folders/{f['id']}/scan")
        assert s == 202 and wait(port, r["job"])["result"]["imported"] == 1
        assert call(port, "DELETE", f"/api/folders/{f['id']}")[0] == 200
        assert call(port, "DELETE", f"/api/folders/{f['id']}")[0] == 404
        # a folder dropped on the window (the import of a directory)
        other = tmp_path / "Carnets"
        write(other / "a.txt", "Premier carnet.\n")
        _, r, _ = call(port, "POST", "/api/import", {"paths": [str(other)], "reference": True})
        j = wait(port, r["job"])
        assert j["result"]["folders"][0]["collection"] == "Carnets"
        assert j["result"]["folders"][0]["imported"] == 1
        _, lst, _ = call(port, "GET", "/api/folders")
        assert [x["watch"] for x in lst] == [False]
    finally:
        srv.shutdown()
        srv.server_close()


def test_schema_6_is_upgraded(tmp_path):
    import sqlite3
    from iak import db
    path = tmp_path / "old" / "iak.sqlite"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA[: db.SCHEMA.index("-- schema 7")])
    old.execute("PRAGMA user_version = 6")
    old.commit()
    old.close()
    con = db.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert con.execute("SELECT count(*) FROM folder_files").fetchone()[0] == 0
    con.close()
