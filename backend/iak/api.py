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
import os
import json
import mimetypes
import queue
import re
import secrets
import shutil
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (__version__, analyze, answer, book, collection, db, embed, export, folders,
               ocr, search, servers, transcription, translate)
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
    status: str = "queued"          # queued, running, done, error, cancelled
    cancel: bool = False            # asked to stop (jobs that can stop say so)
    progress: dict = field(default_factory=dict)
    result: object = None
    error: str = ""
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "cancel": self.cancel,
                "progress": self.progress, "result": self.result, "error": self.error,
                "created": self.created, "finished": self.finished}


class App:
    def __init__(self, library: str | Path, host: servers.Host, lang: str = "fr",
                 ocr_langs: str = "fra+eng", tesseract: str | None = None,
                 token: str | None = None, origins: set[str] | None = None,
                 config: str | Path | None = None, watch_seconds: float | None = None):
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
        # whole books: their own thread, they take hours
        self.books = book.Books(self.folder, lambda: translate.translator_for(
            self.host, self.host.api_key))
        # watched folders: looked at every watch_seconds (IAK_WATCH_SECONDS, 0 = off)
        if watch_seconds is None:
            watch_seconds = float(os.environ.get("IAK_WATCH_SECONDS", "60"))
        self.watch_seconds = watch_seconds
        self._signatures: dict[int, tuple] = {}
        self._rescan: set[int] = set()
        if watch_seconds > 0:
            threading.Thread(target=self._watch, daemon=True, name="iak-watch").start()

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

    def scan_active(self, folder_id: int) -> bool:
        return any(j.kind == "scan" and j.params.get("folder_id") == folder_id
                   and j.status in ("queued", "running") for j in list(self.jobs.values()))

    def watch_once(self) -> list[int]:
        """Queue a scan for each watched folder whose files changed (or that
        has files still being written); returns their ids."""
        queued = []
        with self.library() as lib:
            rows = lib.con.execute("SELECT id FROM folders WHERE watch=1").fetchall()
            for (fid,) in rows:
                sig = folders.signature(lib, fid)
                if sig is None or self.scan_active(fid):
                    continue
                if sig != self._signatures.get(fid) or fid in self._rescan:
                    self._signatures[fid] = sig
                    self._rescan.discard(fid)
                    self.submit("scan", {"folder_id": fid, "auto": True})
                    queued.append(fid)
        return queued

    def _watch(self) -> None:
        time.sleep(min(5.0, self.watch_seconds))     # let the interface start first
        while True:
            try:
                self.watch_once()
            except Exception:  # a folder on a key pulled out, a locked file...
                traceback.print_exc()
            time.sleep(self.watch_seconds)

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            if job.cancel:
                job.status, job.finished = "cancelled", time.time()
                continue
            job.status = "running"
            try:
                with self.library() as lib:
                    job.result = JOBS[job.kind](self, lib, job)
                job.status = "cancelled" if job.cancel else "done"
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
    results, followed = [], []
    for i, p in enumerate(paths, 1):
        job.progress = {"step": "import", "done": i - 1, "total": len(paths), "file": p}
        if Path(p).is_dir():
            # a folder: all its documents, in a collection named after it
            folder = _follow(lib, p, copy=not job.params.get("reference", False), watch=False)
            res = folders.scan(lib, folder["id"], _progress(job, "import"), lambda: job.cancel)
            followed.append({"path": p, "folder": folder["id"],
                             "collection": folder["collection"], **_scan_counts(res)})
            continue
        r = lib.import_file(p, copy=not job.params.get("reference", False))
        results.append({"path": p, "doc_id": r.doc_id, "status": r.status,
                        "name": r.name, "message": r.message})
    return {"files": results, "folders": followed, **_embed_if_possible(app, lib, job)}


def _follow(lib: Library, path: str, **opts) -> dict:
    """The folder followed at path, added if needed."""
    stored = folders.store_path(path)
    row = lib.con.execute("SELECT id FROM folders WHERE path=?", (stored,)).fetchone()
    if row:
        return folders.public(lib.con, folders._row(lib.con, row[0]))
    return folders.add(lib, path, **opts)


def _scan_counts(res: dict) -> dict:
    return {k: res[k] for k in ("found", "imported", "duplicates", "unchanged", "changed",
                                "replaced", "errors", "waiting", "gone", "available")}


def job_scan(app: App, lib: Library, job: Job):
    fid = job.params["folder_id"]
    res = folders.scan(lib, fid, _progress(job, "scan"), lambda: job.cancel)
    if res["waiting"]:
        app._rescan.add(fid)                   # files still being written
    with app._lock:
        sig = folders.signature(lib, fid)
        if sig is not None:
            app._signatures[fid] = sig
    return {"folder": fid, **_scan_counts(res), "messages": res["messages"],
            **_embed_if_possible(app, lib, job)}


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


def job_vectors(app: App, lib: Library, job: Job):
    return _embed_if_possible(app, lib, job)


def job_read(app: App, lib: Library, job: Job):
    """Read pages again with the vision model: one page, or a document."""
    p = job.params
    client = analyze.vision_client(app.host, app.host.api_key)
    t0 = time.perf_counter()
    if p.get("unit_id"):
        job.progress = {"step": "read", "done": 0, "total": 1}
        r = transcription.read_page(lib, client, p["unit_id"], p.get("method", "correct"),
                                    p.get("max_side", transcription.DEFAULT_SIDE))
        out = {"analysis": r.id, "unit_id": r.unit_id, "method": r.method, "text": r.text,
               "score": r.score, "truncated": r.truncated}
        if p.get("replace"):
            transcription.apply(lib, r.id)
            out["replaced"] = 1
    else:
        out = transcription.read_document(
            lib, client, p["doc_id"], p.get("method", "correct"), p.get("pages", "low"),
            p.get("threshold", transcription.LOW_CONFIDENCE), bool(p.get("replace")),
            bool(p.get("refresh")), _progress(job, "read"), lambda: job.cancel,
            p.get("max_side", transcription.DEFAULT_SIDE))
        out["doc_id"] = p["doc_id"]
    out["seconds"] = round(time.perf_counter() - t0, 1)
    if out.get("replaced"):
        out.update(_embed_if_possible(app, lib, job))
    return out


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
        "translate": job_translate, "translate_document": job_translate_document,
        "read": job_read, "vectors": job_vectors, "scan": job_scan}


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
                          folder=(q.get("folder") or [None])[0],
                          collections=ints("collection"))


def _book_source(lib: Library, doc_id: int) -> dict:
    """What to translate for a library document: its original file, with the
    pages read by OCR or corrected taken from the library; the library's text
    alone when the original is gone or is an image."""
    d = lib.document(doc_id)
    if d is None:
        raise ApiError(404, "no such document")
    units = lib.units(doc_id)
    original = lib.original_path(doc_id)
    page_texts, kind = {}, d["kind"]
    if kind == "pdf" and original.exists():
        for u in units:
            page = json.loads(u["locator"] or "{}").get("page")
            if page and (u["source"] == "ocr" or u["edited"]):
                page_texts[str(page)] = u["text"]
    elif kind in ("epub", "html", "docx", "txt", "md") and original.exists():
        if any(u["edited"] for u in units):
            # corrections in the library would be lost from the original file:
            # translate the library's text (layout simplified to Markdown)
            kind = "units"
    else:
        kind = "units"
    if kind == "units":
        page_texts = {str(i): u["text"] for i, u in enumerate(units, 1)}
    meta = json.loads(d["meta"] or "{}")
    return {"name": d["name"], "kind": kind,
            "source": str(original) if kind != "units" else None,
            "sha256": d["sha256"], "page_texts": page_texts, "pages": len(units),
            "doc_id": doc_id, "doc_language": (meta.get("language") or "").split("-")[0]}


def _file_source(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise ApiError(404, f"no such file: {path}")
    kind = book.FORMATS.get(p.suffix.lower())
    if kind is None:
        raise ApiError(400, "books are EPUB, PDF, TXT, Markdown, HTML or DOCX files")
    return {"name": p.name, "kind": kind, "source": str(p.resolve()),
            "sha256": book.file_sha256(p), "page_texts": {}, "pages": 0, "doc_id": None}


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

    def _read_body(self) -> None:
        """Read the request body before anything else, even when the route
        ignores it or the request is refused: left in the socket, it would be
        taken for the start of the next request on a kept-alive connection."""
        self._raw = b""
        self._too_large = False
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            self._too_large = True
            self.close_connection = True       # the rest is not read
        elif n > 0:
            self._raw = self.rfile.read(n)

    def _body(self) -> dict:
        if getattr(self, "_too_large", False):
            raise ApiError(413, "request too large")
        raw = getattr(self, "_raw", b"")
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
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
            self._read_body()
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
        except (analyze.AnalysisError, embed.EmbeddingMismatch, book.BookError) as exc:
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
                                     for j in self.app.jobs.values()),
                         "books_busy": self.app.books.busy()})

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
            colls = collection.of_documents(lib.con)
        out = [_row(r) for r in rows]
        for d in out:
            d["collections"] = colls.get(d["id"], [])
        self._json(200, out)

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
            readers = transcription.readers(lib, int(doc_id))
        for r in rows:
            if not boxes:
                r.pop("boxes", None)
            r["reader"] = readers.get(r["id"])
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
        # a folder is followed: its documents, in a collection named after it
        missing = [p for p in paths if not (Path(p).is_file() or Path(p).is_dir())]
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
        colls = body.get("collections") or None
        for name, v in (("doc_ids", doc_ids), ("collections", colls)):
            if v is not None and not (isinstance(v, list) and all(isinstance(x, int) for x in v)):
                raise ApiError(400, f"{name} must be a list of numbers")
        filters = search.Filters(doc_ids=doc_ids, kinds=kinds, collections=colls)
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

    # -------------------------------------------------- followed folders
    @route("GET", "/api/folders")
    def folders_list(self, query):
        with self.app.library() as lib:
            self._json(200, folders.all_(lib.con))

    @route("POST", "/api/folders")
    def folder_add(self, query):
        """{"path", "copy": false, "recursive": true, "watch": true, "name"}: follow a
        folder and import its documents (a job)."""
        body = self._body()
        path = str(body.get("path") or "")
        if not path:
            raise ApiError(400, "choose a folder")
        with self.app.library() as lib:
            try:
                f = folders.add(lib, path, bool(body.get("copy")), body.get("recursive", True)
                                is not False, body.get("watch", True) is not False,
                                body.get("name") or None)
            except folders.FolderError as exc:
                raise ApiError(409 if "already" in str(exc) else 400, str(exc))
            except collection.CollectionError as exc:
                raise ApiError(400, str(exc))
        job = self.app.submit("scan", {"folder_id": f["id"]})
        self._json(201, {**f, "job": job.id})

    @route("PUT", r"/api/folders/(\d+)")
    def folder_update(self, folder_id, query):
        body = self._body()
        with self.app.library() as lib:
            try:
                f = folders.update(lib, int(folder_id), copy=body.get("copy"),
                                   recursive=body.get("recursive"), watch=body.get("watch"))
            except KeyError:
                raise ApiError(404, "no such folder")
        self.app._signatures.pop(int(folder_id), None)
        self._json(200, f)

    @route("POST", r"/api/folders/(\d+)/scan")
    def folder_scan(self, folder_id, query):
        with self.app.library() as lib:
            try:
                f = folders.public(lib.con, folders._row(lib.con, int(folder_id)))
            except KeyError:
                raise ApiError(404, "no such folder")
        if not f["available"]:
            raise ApiError(409, f"folder not found on this computer: {f['full_path']}")
        if self.app.scan_active(int(folder_id)):
            raise ApiError(409, "this folder is being looked at already")
        job = self.app.submit("scan", {"folder_id": int(folder_id)})
        self._json(202, {"job": job.id})

    @route("DELETE", r"/api/folders/(\d+)")
    def folder_remove(self, folder_id, query):
        with self.app.library() as lib:
            try:
                folders.remove(lib, int(folder_id))
            except KeyError:
                raise ApiError(404, "no such folder")
        self.app._signatures.pop(int(folder_id), None)
        self._json(200, {"removed": int(folder_id)})

    # -------------------------------------------------- collections
    def _collection(self, lib, cid):
        try:
            return collection.get(lib.con, int(cid))
        except KeyError:
            raise ApiError(404, "no such collection")

    @route("GET", "/api/collections")
    def collections(self, query):
        with self.app.library() as lib:
            self._json(200, collection.all_(lib.con))

    @route("POST", "/api/collections")
    def collection_create(self, query):
        body = self._body()
        with self.app.library() as lib:
            try:
                c = collection.create(lib.con, body.get("name"))
                if body.get("doc_ids"):
                    collection.add(lib.con, c["id"], [int(x) for x in body["doc_ids"]])
                    c = collection.get(lib.con, c["id"])
            except collection.CollectionError as exc:
                raise ApiError(409 if "exists" in str(exc) else 400, str(exc))
            except KeyError as exc:
                raise ApiError(404, str(exc).strip("'"))
        self._json(201, c)

    @route("GET", r"/api/collections/(\d+)")
    def collection_info(self, cid, query):
        with self.app.library() as lib:
            self._json(200, self._collection(lib, cid))

    @route("PUT", r"/api/collections/(\d+)")
    def collection_rename(self, cid, query):
        body = self._body()
        with self.app.library() as lib:
            self._collection(lib, cid)
            try:
                self._json(200, collection.rename(lib.con, int(cid), body.get("name")))
            except collection.CollectionError as exc:
                raise ApiError(409 if "exists" in str(exc) else 400, str(exc))

    @route("DELETE", r"/api/collections/(\d+)")
    def collection_delete(self, cid, query):
        with self.app.library() as lib:
            self._collection(lib, cid)
            collection.delete(lib.con, int(cid))
        self._json(200, {"deleted": int(cid)})

    @route("POST", r"/api/collections/(\d+)/documents")
    def collection_documents(self, cid, query):
        """{"add": [document ids], "remove": [document ids]}"""
        body = self._body()
        with self.app.library() as lib:
            self._collection(lib, cid)
            try:
                added = collection.add(lib.con, int(cid), [int(x) for x in body.get("add") or []])
            except KeyError as exc:
                raise ApiError(404, str(exc).strip("'"))
            removed = collection.remove(lib.con, int(cid),
                                        [int(x) for x in body.get("remove") or []])
            out = self._collection(lib, cid)
        self._json(200, {**out, "added": added, "removed": removed})

    # -------------------------------------------------- Markdown exports
    def _lang(self, value) -> str:
        return value if value in export.LABELS else self.app.lang

    def _deliver(self, name: str, markdown: str, path) -> None:
        """Write to path when given, else hand the Markdown back (copy)."""
        if not path:
            self._json(200, {"name": export.safe_name(name), "markdown": markdown})
            return
        try:
            out = export.write(str(path), markdown)
        except OSError as exc:
            raise ApiError(400, f"cannot write {path}: {exc}")
        self._json(200, {"path": str(out)})

    @route("POST", "/api/export/conversation")
    def export_conversation(self, query):
        body = self._body()
        turns = body.get("turns")
        if not isinstance(turns, list) or not turns or len(turns) > 500:
            raise ApiError(400, "turns: a list of questions and answers")
        clean = []
        for t in turns:
            if not isinstance(t, dict):
                raise ApiError(400, "each turn is an object")
            sources = t.get("sources") if isinstance(t.get("sources"), list) else []
            clean.append({
                "question": str(t.get("question") or ""), "text": str(t.get("text") or ""),
                "status": str(t.get("status") or "answered"), "scope": str(t.get("scope") or ""),
                "sources": [{k: str(src.get(k) or "") for k in ("ref", "doc_name", "label", "text")}
                            for src in sources[:100] if isinstance(src, dict)]})
        lang = self._lang(body.get("lang"))
        md = (export.answer_md(clean[0], lang, 1, bool(body.get("quote", True)))
              if body.get("single") else
              export.conversation_md(clean, lang, body.get("title"), bool(body.get("quote", True))))
        stem = "IA-K - " + (clean[0]["question"][:60] if body.get("single") else
                            export.LABELS[lang]["conversation"])
        self._deliver(f"{stem} - {time.strftime('%Y-%m-%d')}.md", md, body.get("path"))

    @route("GET", r"/api/analyses/(\d+)/markdown")
    def analysis_markdown(self, analysis_id, query):
        lang = self._lang((query.get("lang") or [None])[0])
        with self.app.library() as lib:
            try:
                name, md = export.analysis_md(lib, int(analysis_id), lang)
            except KeyError:
                raise ApiError(404, "no such analysis")
        self._deliver(name, md, None)

    @route("POST", r"/api/analyses/(\d+)/export")
    def analysis_export(self, analysis_id, query):
        body = self._body()
        if not body.get("path"):
            raise ApiError(400, "choose where to save the file")
        with self.app.library() as lib:
            try:
                name, md = export.analysis_md(lib, int(analysis_id), self._lang(body.get("lang")))
            except KeyError:
                raise ApiError(404, "no such analysis")
        self._deliver(name, md, body["path"])

    # -------------------------------------------------- page readings (vision)
    @route("POST", "/api/read")
    def read_pages(self, query):
        body = self._body()
        method = body.get("method", "correct")
        if method not in transcription.METHODS:
            raise ApiError(400, "method is transcribe or correct")
        params = {"method": method, "replace": bool(body.get("replace")),
                  "refresh": bool(body.get("refresh"))}
        if body.get("max_side"):
            params["max_side"] = max(512, min(int(body["max_side"]), 3000))
        with self.app.library() as lib:
            if body.get("unit_id"):
                params["unit_id"] = int(body["unit_id"])
                if lib.con.execute("SELECT 1 FROM units WHERE id=?",
                                   (params["unit_id"],)).fetchone() is None:
                    raise ApiError(404, "no such page")
            else:
                params["doc_id"] = int(body["doc_id"])
                params["pages"] = body.get("pages", "low")
                params["threshold"] = float(body.get("threshold") or
                                            transcription.LOW_CONFIDENCE)
                try:
                    n = len(transcription.select_pages(lib, params["doc_id"], params["pages"],
                                                       params["threshold"]))
                except KeyError:
                    raise ApiError(404, "no such document")
                except analyze.AnalysisError as exc:
                    raise ApiError(409, str(exc))
                if not n:
                    raise ApiError(409, "no page to read with these settings")
        job = self.app.submit("read", params)
        self._json(202, {"job": job.id})

    @route("POST", r"/api/analyses/(\d+)/apply")
    def apply_reading(self, analysis_id, query):
        with self.app.library() as lib:
            unit = transcription.apply(lib, int(analysis_id))
        job = self.app.submit("vectors", {})
        self._json(200, {"unit": unit, "job": job.id})

    @route("GET", r"/api/analyses/(\d+)/diff")
    def reading_diff(self, analysis_id, query):
        """Differences between a page's text now and a reading of it."""
        with self.app.library() as lib:
            a = lib.con.execute("SELECT a.text, u.text AS current FROM analyses a JOIN units u"
                                " ON u.id=a.unit_id WHERE a.id=?",
                                (int(analysis_id),)).fetchone()
        if a is None:
            raise ApiError(404, "no such page reading")
        self._json(200, {"ops": transcription.diff(a["current"], a["text"]),
                         "score": transcription.agreement(a["current"], a["text"]),
                         "same": a["current"] == a["text"]})

    @route("POST", r"/api/units/(\d+)/restore")
    def restore_unit(self, unit_id, query):
        with self.app.library() as lib:
            transcription.restore(lib, int(unit_id))
        job = self.app.submit("vectors", {})
        self._json(200, {"unit": int(unit_id), "job": job.id})

    @route("POST", r"/api/jobs/(\d+)/cancel")
    def cancel_job(self, job_id, query):
        j = self.app.jobs.get(int(job_id))
        if j is None:
            raise ApiError(404, "no such job")
        if j.status in ("queued", "running"):
            j.cancel = True
        self._json(200, j.public())

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

    # -------------------------------------------------- books
    @route("GET", "/api/books")
    def books(self, query):
        self._json(200, self.app.books.list())

    @route("POST", "/api/books")
    def book_start(self, query):
        body = self._body()
        _, tgt = _languages(body)
        passes = int(body.get("passes") or 2)
        if passes not in (1, 2, 3):
            raise ApiError(400, "passes is 1, 2 or 3")
        glossary = str(body.get("glossary") or "")
        if len(glossary) > 50_000:
            raise ApiError(400, "glossary too long")
        if body.get("doc_id") is not None:
            with self.app.library() as lib:
                src_info = _book_source(lib, int(body["doc_id"]))
        elif body.get("path"):
            src_info = _file_source(str(body["path"]))
        else:
            raise ApiError(400, "give doc_id or path")
        src = (body.get("src") or "auto").strip()
        if src == "auto" and src_info.get("doc_language") in translate.ENGLISH_NAMES:
            src = src_info["doc_language"]
        src_info.pop("doc_language", None)
        fmt = str(body.get("format") or "auto")
        try:
            book.output_format(src_info["kind"], fmt)
        except book.BookError as exc:
            raise ApiError(400, str(exc))
        job = self.app.books.create({**src_info, "src": src, "tgt": tgt, "passes": passes,
                                     "format": fmt,
                                     "glossary": glossary,
                                     "limit": max(0, int(body.get("limit") or 0)),
                                     "fresh": bool(body.get("fresh"))})
        self._json(202, job.public())

    def _book(self, book_id):
        try:
            return self.app.books.get(book_id)
        except KeyError:
            raise ApiError(404, "no such book")

    @route("GET", r"/api/books/([\w.-]+)")
    def book_info(self, book_id, query):
        job = self._book(book_id)
        n = int((query.get("log") or ["40"])[0])
        self._json(200, {**job.public(), "log": job.log_tail(min(max(n, 0), 500))})

    @route("POST", r"/api/books/([\w.-]+)/pause")
    def book_pause(self, book_id, query):
        self._json(200, self.app.books.pause(self._book(book_id).id).public())

    @route("POST", r"/api/books/([\w.-]+)/resume")
    def book_resume(self, book_id, query):
        body = self._body()
        job = self._book(book_id)
        if job.data.get("doc_id") is not None:
            # pages corrected since are translated from their new text
            with self.app.library() as lib:
                if lib.document(job.data["doc_id"]) is not None:
                    info = _book_source(lib, job.data["doc_id"])
                    job.save(page_texts=info["page_texts"], kind=info["kind"],
                             source=info["source"], pages=info["pages"])
        self._json(200, self.app.books.resume(job.id, bool(body.get("fresh"))).public())

    @route("DELETE", r"/api/books/([\w.-]+)")
    def book_delete(self, book_id, query):
        self.app.books.delete(self._book(book_id).id)
        self._json(200, {"deleted": book_id})

    @route("POST", r"/api/books/([\w.-]+)/save")
    def book_save(self, book_id, query):
        body = self._body()
        job = self._book(book_id)
        src = job.partial if body.get("partial") else job.output
        if not src or not src.exists():
            raise ApiError(409, "nothing to save yet")
        dest = Path(str(body.get("path") or ""))
        if not body.get("path"):
            raise ApiError(400, "choose where to save the file")
        fmt = body.get("format")
        try:
            if fmt and fmt != src.suffix.lstrip("."):
                # another format: written again from the translations kept
                book.render(job, fmt, dest)
            else:
                shutil.copyfile(src, dest)
        except book.BookError as exc:
            raise ApiError(400, str(exc))
        except OSError as exc:
            raise ApiError(400, f"cannot write {dest}: {exc}")
        self._json(200, {"path": str(dest)})

    @route("POST", r"/api/books/([\w.-]+)/import")
    def book_import(self, book_id, query):
        job = self._book(book_id)
        if not (job.output and job.output.exists() and job.data.get("status") == "done"):
            raise ApiError(409, "the translation is not finished")
        j = self.app.submit("import", {"paths": [str(job.output)], "reference": False})
        self._json(202, {"job": j.id})

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
