"""Collections: named groups of documents that limit search and questions."""
import sqlite3
import threading

import numpy as np
import pytest

from iak import api, collection, db, embed, search, servers
from iak.library import Library

from .test_analyze import FakeChat
from .test_api import TOKEN, call


@pytest.fixture
def lib(tmp_path, native_pdf, docx_file, txt_file):
    with Library(tmp_path / "lib") as library:
        ids = [library.import_file(p).doc_id for p in (native_pdf, docx_file, txt_file)]
        yield library, ids


def test_crud(lib):
    lib, (pdf, docx, txt) = lib
    c = collection.create(lib.con, "  Archives   1712 ")
    assert c["name"] == "Archives 1712" and c["doc_ids"] == []
    with pytest.raises(collection.CollectionError, match="exists"):
        collection.create(lib.con, "archives 1712")
    with pytest.raises(collection.CollectionError):
        collection.create(lib.con, "   ")
    assert collection.add(lib.con, c["id"], [pdf, docx]) == 2
    assert collection.add(lib.con, c["id"], [pdf]) == 0            # already in it
    with pytest.raises(KeyError):
        collection.add(lib.con, c["id"], [999])
    other = collection.create(lib.con, "Notes")
    collection.add(lib.con, other["id"], [docx, txt])
    assert collection.of_documents(lib.con) == {pdf: [c["id"]], docx: [c["id"], other["id"]],
                                                txt: [other["id"]]}
    assert [(x["name"], x["n_docs"]) for x in collection.all_(lib.con)] == [
        ("Archives 1712", 2), ("Notes", 2)]
    assert collection.by_name(lib.con, "NOTES")["id"] == other["id"]
    with pytest.raises(collection.CollectionError):
        collection.rename(lib.con, other["id"], "ARCHIVES 1712")
    assert collection.rename(lib.con, other["id"], "Carnets")["name"] == "Carnets"
    assert collection.remove(lib.con, c["id"], [docx, 999]) == 1
    lib.delete(pdf, backup=False)                                   # leaves its collections
    assert collection.get(lib.con, c["id"])["doc_ids"] == []
    collection.delete(lib.con, other["id"])                         # documents stay
    assert lib.document(docx) is not None and len(collection.all_(lib.con)) == 1


def test_filters(lib):
    lib, (pdf, docx, txt) = lib
    c = collection.create(lib.con, "Notes")
    collection.add(lib.con, c["id"], [docx, txt])
    all_hits, _ = lib.find("relieur", mode="kw")
    assert {h.doc_id for h in all_hits} >= {pdf}
    hits, _ = lib.find("relieur", mode="kw", filters=search.Filters(collections=[c["id"]]))
    assert hits and {h.doc_id for h in hits} <= {docx, txt}
    # a collection and a type: both apply
    hits, _ = lib.find("relieur", mode="kw",
                       filters=search.Filters(collections=[c["id"]], kinds=["pdf"]))
    assert hits == []


def test_semantic_scores_only_allowed_passages(tmp_path):
    con = db.connect(tmp_path / "x.sqlite", shared=True)
    con.execute("PRAGMA foreign_keys = OFF")          # vectors without their passages
    index = embed.VectorIndex(con)
    vecs = {1: [1, 0, 0], 2: [0.9, 0.1, 0], 3: [0, 0, 1]}
    with con:
        for pid, v in vecs.items():
            v = np.array(v, dtype=np.float32)
            con.execute("INSERT INTO vectors VALUES (?, ?)",
                        (pid, (v / np.linalg.norm(v)).tobytes()))
    q = np.array([1, 0, 0], dtype=np.float32)
    assert [p for p, _ in index.search(q, 1)] == [1]
    # passage 3 is far from the query, but the only one allowed: it is found
    assert [p for p, _ in index.search(q, 1, allow=[3])] == [3]
    assert index.search(q, 1, allow=[]) == []
    con.close()


def test_schema_5_is_upgraded(tmp_path):
    path = tmp_path / "old" / "iak.sqlite"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA[: db.SCHEMA.index("-- schema 6")])
    old.execute("PRAGMA user_version = 5")
    old.commit()
    old.close()
    con = db.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert con.execute("SELECT count(*) FROM collection_docs").fetchone()[0] == 0
    con.close()


def test_api(tmp_path, native_pdf, docx_file):
    chat = FakeChat(reply=lambda req: "Chez le relieur [S1].")
    app = api.App(tmp_path / "lib", servers.Host("t", chat=chat.url), token=TOKEN)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        with Library(tmp_path / "lib") as l:
            pdf = l.import_file(native_pdf).doc_id
            docx = l.import_file(docx_file).doc_id
        assert call(port, "POST", "/api/collections", {"name": ""})[0] == 400
        s, c, _ = call(port, "POST", "/api/collections", {"name": "Registres", "doc_ids": [pdf]})
        assert s == 201 and c["doc_ids"] == [pdf]
        assert call(port, "POST", "/api/collections", {"name": "registres"})[0] == 409
        assert call(port, "POST", "/api/collections", {"name": "X", "doc_ids": [99]})[0] == 404
        s, lst, _ = call(port, "GET", "/api/collections")
        assert [(x["name"], x["n_docs"]) for x in lst] == [("Registres", 1), ("X", 0)]
        s, r, _ = call(port, "POST", f"/api/collections/{c['id']}/documents",
                       {"add": [docx], "remove": [pdf]})
        assert s == 200 and r["doc_ids"] == [docx] and (r["added"], r["removed"]) == (1, 1)
        _, docs, _ = call(port, "GET", "/api/documents")
        assert {d["id"]: d["collections"] for d in docs} == {pdf: [], docx: [c["id"]]}
        s, hits, _ = call(port, "GET", f"/api/search?q=relieur&mode=kw&collection={c['id']}")
        assert s == 200 and {h["doc_id"] for h in hits["hits"]} <= {docx}
        s, a, _ = call(port, "POST", "/api/ask", {"question": "atlas registre",
                                                  "collections": [c["id"]], "mode": "kw"})
        assert s == 200 and all(x["doc_id"] == docx for x in a["sent"])
        assert call(port, "POST", "/api/ask", {"question": "x", "collections": "a"})[0] == 400
        s, r, _ = call(port, "PUT", f"/api/collections/{c['id']}", {"name": "Carnets"})
        assert s == 200 and r["name"] == "Carnets"
        assert call(port, "PUT", f"/api/collections/{c['id']}", {"name": "x"})[0] == 409
        assert call(port, "GET", "/api/collections/999")[0] == 404
        assert call(port, "DELETE", f"/api/collections/{c['id']}")[0] == 200
        assert call(port, "DELETE", f"/api/collections/{c['id']}")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
        chat.close()


def test_move_and_document_collections(tmp_path, native_pdf, docx_file):
    app = api.App(tmp_path / "lib", servers.Host("t"), token=TOKEN)
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        with Library(tmp_path / "lib") as l:
            pdf = l.import_file(native_pdf).doc_id
            docx = l.import_file(docx_file).doc_id
            a = collection.create(l.con, "A")["id"]
            b = collection.create(l.con, "B")["id"]
            collection.add(l.con, a, [pdf, docx])
        # moved: out of A, into B; a document not in A is left alone
        s, r, _ = call(port, "POST", f"/api/collections/{a}/move", {"to": b, "doc_ids": [pdf]})
        assert s == 200 and r["moved"] == 1
        assert call(port, "POST", f"/api/collections/{a}/move", {"to": a, "doc_ids": [docx]})[0] \
            == 400
        assert call(port, "POST", f"/api/collections/{a}/move", {"to": 99, "doc_ids": [docx]})[0] \
            == 404
        _, docs, _ = call(port, "GET", "/api/documents")
        assert {d["id"]: d["collections"] for d in docs} == {pdf: [b], docx: [a]}
        # all the collections of one document at once
        s, r, _ = call(port, "PUT", f"/api/documents/{docx}/collections", {"collections": [b]})
        assert s == 200 and r["collections"] == [b]
        s, r, _ = call(port, "PUT", f"/api/documents/{docx}/collections", {"collections": [a, b]})
        assert r["collections"] == [a, b]
        assert call(port, "PUT", f"/api/documents/{docx}/collections", {"collections": []})[1] \
            == {"collections": []}
        assert call(port, "PUT", f"/api/documents/{docx}/collections", {"collections": [99]})[0] \
            == 404
        assert call(port, "PUT", "/api/documents/999/collections", {"collections": []})[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
