"""Semantic and hybrid search against a fake embedding server.

The fake server embeds a text as a bag of hashed words (after the EmbeddingGemma
prefix), so shared words mean close vectors: enough to test storage, the
model check, filters, fusion and cache invalidation without a real model.
"""
import hashlib
import json
import math
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from iak import db, embed, search, servers
from iak.library import Library

DIM = 1024


def fake_vector(text: str, seed: str) -> list[float]:
    body = text.split("query: ", 1)[-1].split("text: ", 1)[-1]
    v = [0.0] * DIM
    for w in body.lower().replace("'", " ").split():
        w = w.strip(".,;:!?«»")
        if len(w) > 2:
            h = int(hashlib.md5((seed + w).encode()).hexdigest(), 16)
            v[h % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeEmbeddingServer:
    def __init__(self, seed="m1", model_path="/models/embeddinggemma.gguf"):
        self.seed, self.model_path, self.calls = seed, model_path, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/props":
                    self._send({"model_path": outer.model_path,
                                "default_generation_settings": {"n_ctx": 2048}})
                else:
                    self._send({"data": [{"id": outer.model_path}]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls += 1
                self._send({"data": [{"index": i, "embedding": fake_vector(t, outer.seed)}
                                     for i, t in enumerate(payload["input"])]})

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def embedder(self) -> embed.Embedder:
        return embed.Embedder(servers.Client(self.url))

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def server():
    s = FakeEmbeddingServer()
    yield s
    s.close()


@pytest.fixture
def lib(tmp_path):
    with Library(tmp_path / "lib") as library:
        yield library


@pytest.fixture
def notes(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("# Atelier\n\nLe relieur répare l'atlas abîmé.\n\n"
                 "# Cuisine\n\nLa recette demande deux œufs et du beurre.\n\n"
                 "# Voyage\n\nLe navire quitte Marseille pour Alexandrie.\n",
                 encoding="utf-8")
    return p


def test_embed_store_and_search(lib, server, notes):
    lib.import_file(notes)
    emb = server.embedder()
    assert embed.pending(lib.con) == 3
    assert lib.embed_pending(emb) == 3
    assert embed.pending(lib.con) == 0 and len(lib.vectors) == 3
    fp = db.get_setting(lib.con, "embedding")
    assert fp["model"] == "embeddinggemma.gguf" and fp["dim"] == DIM
    hits, used = lib.find("navire Alexandrie", 5, embedder=emb, mode="sem")
    assert used == "sem" and hits[0].label == "Voyage" and hits[0].via == "sem"
    assert len(hits) == 1                       # unrelated sections cut off
    # vectors are stored normalised, as float32
    raw = lib.con.execute("SELECT vec FROM vectors LIMIT 1").fetchone()[0]
    assert len(raw) == DIM * 4
    assert abs(np.linalg.norm(np.frombuffer(raw, np.float32)) - 1) < 1e-5


def test_prefixes_sent(lib, server, notes, monkeypatch):
    seen = []
    real = embed.Embedder.embed

    def spy(self, texts):
        seen.extend(texts)
        return real(self, texts)
    monkeypatch.setattr(embed.Embedder, "embed", spy)
    lib.import_file(notes)
    emb = server.embedder()
    lib.embed_pending(emb)
    lib.find("relieur", embedder=emb, mode="sem")
    assert any(t.startswith("title: notes | text: ") for t in seen)
    assert seen[-1] == "task: search result | query: relieur"


def test_hybrid_fuses_both_lists(lib, server, notes, native_pdf):
    lib.import_file(notes)
    lib.import_file(native_pdf)
    emb = server.embedder()
    lib.embed_pending(emb)
    hits, used = lib.find("relieur atlas", 10, embedder=emb)
    assert used == "hybrid"
    assert hits[0].via == "kw+sem"
    assert {h.via for h in hits} <= {"kw", "sem", "kw+sem"}
    assert hits == sorted(hits, key=lambda h: -h.score)


def test_auto_falls_back_to_keywords(lib, notes):
    lib.import_file(notes)
    hits, used = lib.find("relieur", embedder=None)
    assert used == "kw" and hits


def test_filters_apply_to_semantic(lib, server, notes, native_pdf):
    a = lib.import_file(notes).doc_id
    b = lib.import_file(native_pdf).doc_id
    emb = server.embedder()
    lib.embed_pending(emb)
    hits, _ = lib.find("relieur atlas", 10, search.Filters(doc_ids=[b]), emb, "sem")
    assert hits and {h.doc_id for h in hits} == {b}
    hits, _ = lib.find("relieur atlas", 10, search.Filters(kinds=["md"]), emb, "hybrid")
    assert {h.doc_id for h in hits} == {a}


def test_other_model_is_refused(lib, server, notes):
    lib.import_file(notes)
    lib.embed_pending(server.embedder())
    other = FakeEmbeddingServer(seed="another-model", model_path="/m/other.gguf")
    try:
        emb2 = other.embedder()
        with pytest.raises(embed.EmbeddingMismatch):
            lib.find("relieur", embedder=emb2, mode="hybrid")
        with pytest.raises(embed.EmbeddingMismatch):
            lib.embed_pending(emb2)
        # rebuilding with the new model is the way out
        assert lib.rebuild_vectors(emb2) == 3
        hits, used = lib.find("navire", embedder=emb2, mode="sem")
        assert used == "sem" and hits[0].label == "Voyage"
        assert db.get_setting(lib.con, "embedding")["model"] == "other.gguf"
    finally:
        other.close()


def test_same_model_other_file_is_accepted(lib, server, notes):
    lib.import_file(notes)
    lib.embed_pending(server.embedder())
    twin = FakeEmbeddingServer(seed="m1", model_path="/elsewhere/eg-q4.gguf")
    try:
        assert embed.check_model(lib.con, twin.embedder()) == "same"
    finally:
        twin.close()


def test_edits_and_deletes_keep_vectors_in_step(lib, server, notes):
    r = lib.import_file(notes)
    emb = server.embedder()
    lib.embed_pending(emb)
    unit = lib.units(r.doc_id)[1]
    lib.set_unit_text(unit["id"], "Le cartographe dessine une carte des côtes.")
    assert embed.pending(lib.con) == 1          # only the edited unit
    lib.embed_pending(emb)
    hits, _ = lib.find("cartographe côtes", embedder=emb, mode="sem")
    assert hits[0].unit_id == unit["id"]
    lib.delete(r.doc_id)
    assert lib.con.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0
    assert len(lib.vectors) == 0


def test_vector_cache_reloads_after_rebuild(lib, server, notes):
    lib.import_file(notes)
    lib.embed_pending(server.embedder())
    assert len(lib.vectors) == 3
    first = lib.vectors._mat.copy()
    other = FakeEmbeddingServer(seed="x")
    try:
        lib.rebuild_vectors(other.embedder())
        lib.vectors.search(np.ones(DIM, np.float32) / 8, 1)
        assert not np.allclose(first, lib.vectors._mat)
    finally:
        other.close()


def test_schema_1_is_upgraded(tmp_path):
    path = tmp_path / "old" / "iak.sqlite"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    # schema 1 = everything before the semantic tables
    v1 = db.SCHEMA[: db.SCHEMA.index("-- schema 2")]
    old.executescript(v1)
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()
    con = db.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {"vectors", "settings"} <= names
    assert list((path.parent / "backups").glob("iak-*.sqlite"))    # backed up first
    con.close()
