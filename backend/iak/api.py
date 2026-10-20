"""Local HTTP API for the desktop interface (Tauri), standard library only.

Security
  - listens on 127.0.0.1 only;
  - every /api request needs "Authorization: Bearer <token>"; the token is
    random per start and handed to the interface on stdout;
  - the Host header must be 127.0.0.1 or localhost (blocks DNS rebinding);
  - cross-origin requests are only answered for the interface's origins.

Long operations (import with OCR, embeddings, image analysis, summaries,
comparisons) run as jobs, one at a time, in a worker thread: the request
returns {"job": id} and the interface polls GET /api/jobs/<id>.
Answers to questions are synchronous (10-30 s with the 26B-A4B).

Each request opens its own SQLite connection; the vector index is shared.
"""
from __future__ import annotations

import itertools
import json
import mimetypes
import queue
import re
import secrets
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, analyze, answer, db, embed, ocr, search, servers, translate
from .library import Library

CONTENT_TYPES = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp",
    ".webp": "image/webp", ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8", ".markdown": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".xhtml": "application/xhtml+xml", ".epub": "application/epub+zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
TAURI_ORIGINS = {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
MAX_BODY = 4 << 20


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- state

@dataclass
class Job:
    id: int
    kind: str
    params: dict
    status: str = "queued"          # queued, running, done, error
    progress: dict = field(default_factory=dict)
    result: object = None
    error: str = ""
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "progress": self.progress, "result": self.result, "error": self.error,
                "created": self.created, "finished": self.finished}


class App:
    def __init__(self, library: str | Path, host: servers.Host, lang: str = "fr",
                 ocr_langs: str = "fra+eng", tesseract: str | None = None,
                 token: str | None = None, origins: set[str] | None = None,
                 config: str | Path | None = None):
        self.folder = Path(library)
        self.host = host
        self.config = Path(config) if config else None    # servers.toml written by settings
        self.lang = lang
        self.ocr_langs, self.tesseract = ocr_langs, tesseract
        self.token = token or secrets.token_urlsafe(24)
        self.origins = TAURI_ORIGINS | (origins or set())
        with Library(self.folder) as lib:                    # create or upgrade
            # preferences saved by the settings screen win over the defaults
            self.ocr_langs = db.get_setting(lib.con, "ocr_langs") or self.ocr_langs
            self.lang = db.get_setting(lib.con, "lang") or self.lang
        self.vectors = embed.VectorIndex(db.connect(self.folder / "iak.sqlite", shared=True))
        self.jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._queue: queue.Queue[Job] = queue.Queue()
        self._lock = threading.Lock()
        self._embedder = None
        threading.Thread(target=self._worker, daemon=True, name="iak-jobs").start()

    def library(self) -> Library:
        return Library(self.folder, self.ocr_langs, self.tesseract, vectors=self.vectors)

    # model servers -----------------------------------------------------
    def embedder(self, required: bool = False) -> embed.Embedder | None:
        if not self.host.embedding:
            if required:
                raise ApiError(409, "no embedding server configured")
            return None
        if self._embedder is None:
            self._embedder = embed.Embedder(servers.Client(self.host.embedding,
                                                           self.host.api_key, timeout=300))
        try:
            self._embedder.client.model()
        except servers.ServerError as exc:
            if required:
                raise ApiError(503, f"embedding server unavailable: {exc}")
            return None
        return self._embedder

    def chat(self) -> servers.Client:
        if not self.host.chat:
            raise ApiError(409, "no chat server configured")
        return servers.Client(self.host.chat, self.host.api_key)

    # jobs ------------------------------------------------------------------
    def submit(self, kind: str, params: dict) -> Job:
        job = Job(next(self._ids), kind, params)
        with self._lock:
            self.jobs[job.id] = job
            # keep the last 200 jobs
            for old in sorted(self.jobs)[:-200]:
                if self.jobs[old].status in ("done", "error"):
                    del self.jobs[old]
        self._queue.put(job)
        return job

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            job.status = "running"
            try:
                with self.library() as lib:
                    job.result = JOBS[job.kind](self, lib, job)
                job.status = "done"
            except ApiError as exc:
                job.status, job.error = "error", str(exc)
            except (analyze.AnalysisError, servers.ServerError, embed.EmbeddingMismatch,
                    translate.TranslateError, KeyError, OSError) as exc:
                job.status, job.error = "error", str(exc)
            except Exception as exc:  # report, keep the worker alive
                job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            job.finished = time.time()


# ---------------------------------------------------------------- jobs

def _progress(job: Job, label: str):
    def cb(done, total):
        job.progress = {"step": label, "done": done, "total": total}
    return cb


def _embed_if_possible(app: App, lib: Library, job: Job) -> dict:
    if not embed.pending(lib.con):
        return {"vectors": 0}
    emb = app.embedder()
    if emb is None:
        return {"vectors": 0, "warning": "no embedding server: semantic index not updated"}
    try:
        return {"vectors": lib.embed_pending(emb, _progress(job, "vectors"))}
    except embed.EmbeddingMismatch as exc:
        return {"vectors": 0, "warning": str(exc)}


def job_import(app: App, lib: Library, job: Job):
    paths = job.params["paths"]
    results = []
    for i, p in enumerate(paths, 1):
        job.progress = {"step": "import", "done": i - 1, "total": len(paths), "file": p}
        r = lib.import_file(p, copy=not job.params.get("reference", False))
        results.append({"path": p, "doc_id": r.doc_id, "status": r.status,
                        "name": r.name, "message": r.message})
    return {"files": results, **_embed_if_possible(app, lib, job)}


def job_embed(app: App, lib: Library, job: Job):
    emb = app.embedder(required=True)
    fn = lib.rebuild_vectors if job.params.get("rebuild") else lib.embed_pending
    return {"vectors": fn(emb, _progress(job, "vectors"))}


def job_reindex(app: App, lib: Library, job: Job):
    doc_id = job.params["doc_id"]
    if job.params.get("extract"):
        job.progress = {"step": "extract"}
        lib.reextract(doc_id)
    n = lib.reindex(doc_id)
    return {"passages": n, **_embed_if_possible(app, lib, job)}


def job_look(app: App, lib: Library, job: Job):
    p = job.params
    client = analyze.vision_client(app.host, app.host.api_key)
    task = "ask" if p.get("question") else p.get("task", "describe")
    r = analyze.look(lib, client, p["unit_id"], task, p.get("question", ""),
                     p.get("lang") or app.lang, use_ocr=p.get("ocr", True))
    return {"analysis": r.id, "text": r.text, "seconds": round(r.seconds, 1)}


def job_summarize(app: App, lib: Library, job: Job):
    p = job.params
    r = analyze.summarize(lib, app.chat(), p["doc_id"], p.get("lang") or app.lang,
                          p.get("refresh", False), _progress(job, "summary"))
    return {"analysis": r.id, "text": r.text, "seconds": round(r.seconds, 1)}


def job_compare(app: App, lib: Library, job: Job):
    p = job.params
    r = analyze.compare(lib, app.chat(), p["doc_ids"], p.get("question", ""),
                        p.get("lang") or app.lang, _progress(job, "summary"))
    return {"analysis": r.id, "text": r.text, "seconds": round(r.seconds, 1)}


def _languages(p: dict, src_required: bool = True) -> tuple[str | None, str]:
    src, tgt = (p.get("src") or "auto").strip(), (p.get("tgt") or "").strip()
    if not re.match(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$", tgt):
        raise ApiError(400, "choose the target language")
    return (None if src == "auto" else src), tgt


def job_translate(app: App, lib: Library, job: Job):
    p = job.params
    src, tgt = _languages(p)
    t0 = time.perf_counter()
    src = src or translate.detect_language(p["text"])
    if not src:
        raise translate.TranslateError("the language of the text could not be detected: choose it")
    tr = translate.translator_for(app.host, app.host.api_key)
    text = tr.translate(p["text"], src, tgt, _progress(job, "translate"))
    return {"text": text, "src": src, "tgt": tgt, "model": tr.model, "format": tr.format,
            "seconds": round(time.perf_counter() - t0, 1)}


def job_translate_document(app: App, lib: Library, job: Job):
    p = job.params
    src, tgt = _languages(p)
    t0 = time.perf_counter()
    tr = translate.translator_for(app.host, app.host.api_key)
    if p.get("unit_id"):
        u = lib.con.execute("SELECT doc_id FROM units WHERE id=?", (p["unit_id"],)).fetchone()
        if u is None:
            raise KeyError(f"no unit {p['unit_id']}")
        src = src or translate.document_language(lib, u["doc_id"])
        if not src:
            raise translate.TranslateError("the language of the document could not be "
                                           "detected: choose it")
        job.progress = {"step": "translate", "done": 0, "total": 1}
        text, cached = translate.translate_unit(lib, tr, p["unit_id"], src, tgt,
                                                p.get("refresh", False))
        out = {"unit_id": p["unit_id"], "text": text, "cached": cached, "src": src, "tgt": tgt}
    else:
        out = translate.translate_document(lib, tr, p["doc_id"], src, tgt,
                                           p.get("refresh", False), _progress(job, "translate"))
        out["doc_id"] = p["doc_id"]
    return {**out, "model": tr.model, "format": tr.format,
            "seconds": round(time.perf_counter() - t0, 1)}


JOBS = {"import": job_import, "embed": job_embed, "reindex": job_reindex,
        "look": job_look, "summarize": job_summarize, "compare": job_compare,
        "translate": job_translate, "translate_document": job_translate_document}


# ---------------------------------------------------------------- views

def _row(r) -> dict:
    d = dict(r)
    for k in ("meta", "locator", "sources"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    if "boxes" in d and isinstance(d["boxes"], str):
        d["boxes"] = json.loads(d["boxes"])
    return d


def _hit(h) -> dict:
    return {"passage_id": h.passage_id, "doc_id": h.doc_id, "unit_id": h.unit_id,
            "doc_name": h.doc_name, "label": h.label, "snippet": h.snippet,
            "text": h.text, "score": h.score, "via": h.via}


def _filters(q: dict) -> search.Filters:
    def ints(key):
        v = q.get(key)
        return [int(x) for x in v] if v else None
    return search.Filters(doc_ids=ints("doc"), kinds=q.get("kind") or None,
                          folder=(q.get("folder") or [None])[0])


ROUTES: list[tuple[str, re.Pattern, str]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn.__name__))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    app: App = None                       # set by make_server
    server_version = f"iak/{__version__}"
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------- plumbing
    def log_message(self, fmt, *args):     # quiet; errors are reported in responses
        pass

    def _origin_ok(self) -> str | None:
        origin = self.headers.get("Origin")
        return origin if origin in self.app.origins else None

    def _send(self, status: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self._origin_ok()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ApiError(413, "request too large")
        if not n:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            raise ApiError(400, "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "body must be a JSON object")
        return data

    def _check(self) -> None:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ApiError(403, "bad Host header")
        origin = self.headers.get("Origin")
        if origin and origin not in self.app.origins:
            raise ApiError(403, "origin not allowed")
        auth = self.headers.get("Authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {self.app.token}"):
            raise ApiError(401, "missing or wrong token")

    def do_OPTIONS(self):
        origin = self._origin_ok()
        if not origin:
            self._send(403, b'{"error":"origin not allowed"}')
            return
        self._send(204, b"", extra={
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Max-Age": "600"})

    def _dispatch(self):
        url = urlparse(self.path)
        try:
            self._check()
            for method, pattern, name in ROUTES:
                m = pattern.match(url.path)
                if m and method == self.command:
                    query = parse_qs(url.query)
                    return getattr(self, name)(*m.groups(), query=query)
            if any(p.match(url.path) for _, p, _ in ROUTES):
                raise ApiError(405, "method not allowed")
            raise ApiError(404, "not found")
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (analyze.AnalysisError, embed.EmbeddingMismatch) as exc:
            self._json(409, {"error": str(exc)})
        except servers.ServerError as exc:
            self._json(502, {"error": str(exc)})
        except (KeyError, ValueError, TypeError) as exc:
            self._json(400, {"error": f"bad request: {exc}"})
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    do_GET = do_POST = do_PUT = do_DELETE = _dispatch

    # -------------------------------------------------- general
    @route("GET", "/api/health")
    def health(self, query):
        self._json(200, {"ok": True, "version": __version__})

    @route("GET", "/api/status")
    def status(self, query):
        with self.app.library() as lib:
            c = lib.con
            fp = db.get_setting(c, "embedding")
            counts = {k: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for k, t in
                      (("documents", "documents"), ("passages", "passages"),
                       ("vectors", "vectors"), ("analyses", "analyses"))}
        self._json(200, {"library": str(self.app.folder), "lang": self.app.lang,
                         **counts, "embedding_model": fp["model"] if fp else None,
                         "busy": sum(j.status in ("queued", "running")
                                     for j in self.app.jobs.values())})

    @route("GET", "/api/servers")
    def servers_info(self, query):
        out = {"host": self.app.host.name}
        for role in ("chat", "embedding", "vision", "translate"):
            url = getattr(self.app.host, role)
            info = {"url": url, "local": servers.is_local(url) if url else None}
            if url:
                client = servers.Client(url, self.app.host.api_key, timeout=5)
                try:
                    info["model"] = client.model()
                    info["props"] = client.props()
                    info["ok"] = True
                except servers.ServerError as exc:
                    info["ok"], info["error"] = False, str(exc)
            out[role] = info
        self._json(200, out)

    # -------------------------------------------------- documents
    @route("GET", "/api/documents")
    def documents(self, query):
        with self.app.library() as lib:
            rows = lib.documents((query.get("kind") or [None])[0])
        self._json(200, [_row(r) for r in rows])

    @route("GET", r"/api/documents/(\d+)")
    def document(self, doc_id, query):
        with self.app.library() as lib:
            d = lib.document(int(doc_id))
            if d is None:
                raise ApiError(404, "no such document")
            out = _row(d)
            out["original_available"] = lib.original_path(int(doc_id)).exists()
        self._json(200, out)

    @route("DELETE", r"/api/documents/(\d+)")
    def delete_document(self, doc_id, query):
        with self.app.library() as lib:
            if lib.document(int(doc_id)) is None:
                raise ApiError(404, "no such document")
            lib.delete(int(doc_id))
        self._json(200, {"deleted": int(doc_id)})

    @route("GET", r"/api/documents/(\d+)/units")
    def units(self, doc_id, query):
        boxes = (query.get("boxes") or ["0"])[0] == "1"
        with self.app.library() as lib:
            rows = [_row(u) for u in lib.units(int(doc_id))]
        for r in rows:
            if not boxes:
                r.pop("boxes", None)
        self._json(200, rows)

    @route("GET", r"/api/documents/(\d+)/original")
    def original(self, doc_id, query):
        with self.app.library() as lib:
            d = lib.document(int(doc_id))
            if d is None:
                raise ApiError(404, "no such document")
            path = lib.original_path(int(doc_id))
        if not path.exists():
            raise ApiError(404, f"original file not found: {path}")
        # fixed types for the supported formats: on Windows, mimetypes reads
        # the registry, which can say image/x-png or nothing at all
        ctype = CONTENT_TYPES.get(Path(d["name"]).suffix.lower()) \
            or mimetypes.guess_type(d["name"])[0] or "application/octet-stream"
        name = d["name"].replace('"', "")
        self._send(200, path.read_bytes(), ctype,
                   {"Content-Disposition": f'inline; filename="{name}"'})

    @route("POST", r"/api/documents/(\d+)/reindex")
    def reindex(self, doc_id, query):
        body = self._body()
        job = self.app.submit("reindex", {"doc_id": int(doc_id),
                                          "extract": bool(body.get("extract"))})
        self._json(202, {"job": job.id})

    @route("POST", "/api/import")
    def import_files(self, query):
        body = self._body()
        paths = body.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            raise ApiError(400, "paths must be a non-empty list of file paths")
        missing = [p for p in paths if not Path(p).is_file()]
        if missing:
            raise ApiError(400, f"not found: {', '.join(missing[:5])}")
        job = self.app.submit("import", {"paths": paths,
                                         "reference": bool(body.get("reference"))})
        self._json(202, {"job": job.id})

    # -------------------------------------------------- units
    @route("GET", r"/api/units/(\d+)")
    def unit(self, unit_id, query):
        with self.app.library() as lib:
            u = lib.con.execute("SELECT * FROM units WHERE id=?", (int(unit_id),)).fetchone()
        if u is None:
            raise ApiError(404, "no such unit")
        self._json(200, _row(u))

    @route("PUT", r"/api/units/(\d+)/text")
    def unit_text(self, unit_id, query):
        text = self._body().get("text")
        if not isinstance(text, str):
            raise ApiError(400, "text must be a string")
        with self.app.library() as lib:
            lib.set_unit_text(int(unit_id), text)
            doc_id = lib.con.execute("SELECT doc_id FROM units WHERE id=?",
                                     (int(unit_id),)).fetchone()[0]
        # vectors of the corrected passages: in the background
        job = self.app.submit("reindex", {"doc_id": doc_id, "extract": False})
        self._json(200, {"unit": int(unit_id), "job": job.id})

    @route("GET", r"/api/units/(\d+)/image")
    def unit_image(self, unit_id, query):
        size = int((query.get("max") or ["1600"])[0])
        with self.app.library() as lib:
            png = analyze.render_unit(lib, int(unit_id), max(200, min(size, 4000)))
        self._send(200, png, "image/png")

    # -------------------------------------------------- search and answers
    @route("GET", "/api/search")
    def search_(self, query):
        q = (query.get("q") or [""])[0]
        if not q.strip():
            raise ApiError(400, "q is required")
        mode = (query.get("mode") or ["auto"])[0]
        n = int((query.get("n") or ["20"])[0])
        with self.app.library() as lib:
            emb = self.app.embedder(required=mode in ("sem", "hybrid"))
            hits, used = lib.find(q, n, _filters(query), emb, mode)
        self._json(200, {"mode": used, "hits": [_hit(h) for h in hits]})

    def _retrieve(self, body: dict):
        """Passages for a question (and its follow-up context), with filters."""
        question = body.get("question", "")
        if not isinstance(question, str) or not question.strip():
            raise ApiError(400, "question is required")
        mode = body.get("mode", "auto")
        doc_ids = body.get("doc_ids") or None
        kinds = body.get("kinds") or None
        if doc_ids is not None and not all(isinstance(x, int) for x in doc_ids):
            raise ApiError(400, "doc_ids must be a list of numbers")
        filters = search.Filters(doc_ids=doc_ids, kinds=kinds)
        history = answer.clean_history(body.get("history"))
        with self.app.library() as lib:
            emb = self.app.embedder(required=mode in ("sem", "hybrid"))
            hits, used = lib.find(question, 20, filters, emb, mode)
            follow = answer.retrieval_query(question, history)
            if follow:
                more, _ = lib.find(follow, 20, filters, emb, mode)
                hits = answer.fuse(hits, more)
        return question, history, hits, used

    @route("POST", "/api/ask")
    def ask(self, query):
        body = self._body()
        t0 = time.perf_counter()
        question, history, hits, used = self._retrieve(body)
        res = answer.ask(question, hits, self.app.chat(),
                         int(body.get("budget", answer.DEFAULT_BUDGET_CHARS)), history=history)
        src = lambda s: {"ref": s.ref, "passage_id": s.passage_id, "doc_id": s.doc_id,
                         "unit_id": s.unit_id, "doc_name": s.doc_name, "label": s.label,
                         "text": s.text}
        self._json(200, {"status": res.status, "text": res.text, "search": used,
                         "sources": [src(s) for s in res.sources],
                         "sent": [src(s) for s in res.sent],
                         "remote": not servers.is_local(self.app.host.chat),
                         "seconds": round(time.perf_counter() - t0, 1)})

    # -------------------------------------------------- analyses
    @route("POST", "/api/look")
    def look(self, query):
        body = self._body()
        job = self.app.submit("look", {"unit_id": int(body["unit_id"]),
                                       "task": body.get("task", "describe"),
                                       "question": body.get("question", ""),
                                       "lang": body.get("lang"),
                                       "ocr": body.get("ocr", True)})
        self._json(202, {"job": job.id})

    @route("POST", "/api/summarize")
    def summarize(self, query):
        body = self._body()
        job = self.app.submit("summarize", {"doc_id": int(body["doc_id"]),
                                            "refresh": bool(body.get("refresh")),
                                            "lang": body.get("lang")})
        self._json(202, {"job": job.id})

    @route("POST", "/api/compare")
    def compare(self, query):
        body = self._body()
        ids = [int(x) for x in body["doc_ids"]]
        if len(ids) < 2:
            raise ApiError(400, "give at least two documents")
        job = self.app.submit("compare", {"doc_ids": ids, "question": body.get("question", ""),
                                          "lang": body.get("lang")})
        self._json(202, {"job": job.id})

    @route("GET", "/api/analyses")
    def analyses(self, query):
        doc = query.get("doc")
        with self.app.library() as lib:
            rows = analyze.analyses(lib.con, int(doc[0]) if doc else None)
        self._json(200, [_row(r) for r in rows])

    # -------------------------------------------------- translation
    @route("GET", "/api/languages")
    def languages(self, query):
        self._json(200, [{"code": k, "name": v} for k, v in translate.LANGUAGES.items()])

    @route("POST", "/api/translate")
    def translate_(self, query):
        body = self._body()
        text = str(body.get("text") or "")
        if not text.strip():
            raise ApiError(400, "nothing to translate")
        if len(text) > 200_000:
            raise ApiError(400, "text too long: import it as a document")
        _languages(body)
        job = self.app.submit("translate", {"text": text, "src": body.get("src"),
                                            "tgt": body.get("tgt")})
        self._json(202, {"job": job.id})

    @route("POST", "/api/translate/document")
    def translate_document(self, query):
        body = self._body()
        _languages(body)
        params = {"src": body.get("src"), "tgt": body.get("tgt"),
                  "refresh": bool(body.get("refresh"))}
        with self.app.library() as lib:
            if body.get("unit_id"):
                params["unit_id"] = int(body["unit_id"])
                if lib.con.execute("SELECT 1 FROM units WHERE id=?",
                                   (params["unit_id"],)).fetchone() is None:
                    raise ApiError(404, "no such page")
            else:
                params["doc_id"] = int(body["doc_id"])
                if lib.document(params["doc_id"]) is None:
                    raise ApiError(404, "no such document")
        job = self.app.submit("translate_document", params)
        self._json(202, {"job": job.id})

    @route("GET", r"/api/documents/(\d+)/translations")
    def document_translations(self, doc_id, query):
        tgt = (query.get("tgt") or [""])[0]
        with self.app.library() as lib:
            if lib.document(int(doc_id)) is None:
                raise ApiError(404, "no such document")
            rows = translate.translations(lib, int(doc_id), tgt)
            langs = [r[0] for r in lib.con.execute(
                "SELECT DISTINCT tgt FROM translations WHERE doc_id=?", (int(doc_id),))]
            src = translate.document_language(lib, int(doc_id))
        self._json(200, {"tgt": tgt, "src": src, "languages": langs, "units": rows})

    @route("POST", r"/api/documents/(\d+)/translations/export")
    def export_translation(self, doc_id, query):
        body = self._body()
        path, fmt = str(body.get("path") or ""), body.get("format", "md")
        if not path:
            raise ApiError(400, "choose where to save the file")
        if fmt not in ("md", "txt"):
            raise ApiError(400, "format is md or txt")
        with self.app.library() as lib:
            if lib.document(int(doc_id)) is None:
                raise ApiError(404, "no such document")
            try:
                out = translate.export(lib, int(doc_id), str(body.get("tgt") or ""), path,
                                       fmt, bool(body.get("bilingual")))
            except translate.TranslateError as exc:
                raise ApiError(409, str(exc))
            except OSError as exc:
                raise ApiError(400, f"cannot write {path}: {exc}")
        self._json(200, {"path": str(out)})

    @route("POST", "/api/embed")
    def embed_(self, query):
        body = self._body()
        self.app.embedder(required=True)
        job = self.app.submit("embed", {"rebuild": bool(body.get("rebuild"))})
        self._json(202, {"job": job.id})

    # -------------------------------------------------- settings
    @route("GET", "/api/settings")
    def settings(self, query):
        try:
            tesseract = ocr.find_tesseract(self.app.tesseract)
            available = ocr.available_langs(tesseract)
        except Exception as exc:  # Tesseract missing: say so, settings still work
            available, tesseract = [], f"not found ({exc})"
        h = self.app.host
        self._json(200, {
            "computer": servers.computer_name(),
            "host": {"name": h.name, "chat": h.chat or "", "embedding": h.embedding or "",
                     "vision": h.vision or "", "translate": h.translate or "",
                     "api_key_set": bool(h.api_key)},
            "config": str(self.app.config) if self.app.config else None,
            "lang": self.app.lang,
            "ocr": {"langs": self.app.ocr_langs, "available": available,
                    "tesseract": tesseract},
            "library": str(self.app.folder)})

    @route("PUT", "/api/settings")
    def save_settings(self, query):
        body = self._body()
        url_ok = lambda u: not u or re.match(r"^https?://[^\s/]+(/\S*)?$", u)
        if "servers" in body:
            srv = body["servers"] or {}
            urls = {k: (srv.get(k) or "").strip()
                    for k in ("chat", "embedding", "vision", "translate")}
            bad = [k for k, u in urls.items() if not url_ok(u)]
            if bad:
                raise ApiError(400, f"not an http(s) URL: {', '.join(bad)}")
            if self.app.config is None:
                raise ApiError(409, "no configuration file for the servers")
            key = srv.get("api_key")          # None keeps the stored key
            self.app.host = servers.save_host(self.app.config, urls["chat"],
                                              urls["embedding"], urls["vision"], key,
                                              translate=urls["translate"])
            self.app._embedder = None
        with self.app.library() as lib:
            if "ocr_langs" in body:
                langs = str(body["ocr_langs"]).strip()
                if not re.match(r"^[A-Za-z_]+(\+[A-Za-z_]+)*$", langs):
                    raise ApiError(400, "OCR languages look like fra+eng")
                db.set_setting(lib.con, "ocr_langs", langs)
                self.app.ocr_langs = langs
            if "lang" in body:
                if body["lang"] not in ("fr", "en"):
                    raise ApiError(400, "lang is fr or en")
                db.set_setting(lib.con, "lang", body["lang"])
                self.app.lang = body["lang"]
        return self.settings(query)

    # -------------------------------------------------- highlights
    @route("GET", r"/api/passages/(\d+)/boxes")
    def passage_boxes(self, passage_id, query):
        with self.app.library() as lib:
            try:
                self._json(200, lib.passage_boxes(int(passage_id)))
            except KeyError:
                raise ApiError(404, "no such passage")

    # -------------------------------------------------- streamed answers
    @route("POST", "/api/ask/stream")
    def ask_stream(self, query):
        """Server-sent events: sources, token..., done (or error)."""
        body = self._body()
        t0 = time.perf_counter()
        question, history, hits, used = self._retrieve(body)
        chat = self.app.chat()
        # headers now: errors after this point are sent as an "error" event
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        origin = self._origin_ok()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.close_connection = True

        def emit(event: str, data) -> None:
            payload = json.dumps(data, ensure_ascii=False, default=str)
            self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        src = lambda s: {"ref": s.ref, "passage_id": s.passage_id, "doc_id": s.doc_id,
                         "unit_id": s.unit_id, "doc_name": s.doc_name, "label": s.label,
                         "text": s.text}
        try:
            for kind, value in answer.ask_stream(
                    question, hits, chat, int(body.get("budget", answer.DEFAULT_BUDGET_CHARS)),
                    history=history):
                if kind == "sources":
                    emit("sources", {"search": used, "sent": [src(x) for x in value],
                                     "remote": not servers.is_local(self.app.host.chat)})
                elif kind == "token":
                    emit("token", {"text": value})
                else:
                    emit("done", {"status": value.status, "text": value.text, "search": used,
                                  "sources": [src(x) for x in value.sources],
                                  "sent": [src(x) for x in value.sent],
                                  "seconds": round(time.perf_counter() - t0, 1)})
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # the interface went away
        except Exception as exc:  # report in the stream
            try:
                emit("error", {"error": str(exc)})
            except OSError:
                pass

    # -------------------------------------------------- jobs
    @route("GET", "/api/jobs")
    def jobs(self, query):
        self._json(200, [j.public() for j in sorted(self.app.jobs.values(),
                                                     key=lambda j: -j.id)])

    @route("GET", r"/api/jobs/(\d+)")
    def job(self, job_id, query):
        j = self.app.jobs.get(int(job_id))
        if j is None:
            raise ApiError(404, "no such job")
        self._json(200, j.public())


def make_server(app: App, port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    return srv


def serve(app: App, port: int = 0, announce=sys.stdout, watch_stdin: bool = False) -> None:
    """Run until interrupted. The first stdout line, for the interface, is
    IAK_API {"url": ..., "token": ...}. With watch_stdin, stop when stdin
    closes: the desktop application keeps it open, so the backend ends with
    the application even if that one is killed or crashes."""
    srv = make_server(app, port)
    if watch_stdin:
        def watch():
            try:
                while sys.stdin.buffer.read(4096):
                    pass
            except (OSError, ValueError):
                pass
            srv.shutdown()
        threading.Thread(target=watch, daemon=True, name="iak-stdin").start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print("IAK_API " + json.dumps({"url": url, "token": app.token,
                                   "version": __version__}), file=announce, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
