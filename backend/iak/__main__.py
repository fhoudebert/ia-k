"""Command line for the IA-K backend, until the desktop interface exists.

    python -m iak import FILE...          import documents (copied into the library)
    python -m iak list                    documents, type, state, pages, OCR pages
    python -m iak status                  counts, semantic index, servers
    python -m iak show ID                 units of a document with their text
    python -m iak search "words"          hybrid search (keywords + meaning)
    python -m iak ask "question"          sourced answer from the chat server
    python -m iak embed [--rebuild]       compute missing passage vectors
    python -m iak look UNIT_ID [--task T] read a page or image with the vision model
                                          (describe, transcribe, error, extract,
                                          or --ask "question")
    python -m iak summarize ID            summary of a document, with page labels
    python -m iak compare ID ID...        compare documents (from their summaries)
    python -m iak analyses [ID]           stored analyses
    python -m iak serve [--port N]        local API for the desktop interface
    python -m iak edit UNIT_ID FILE       replace a page's text (OCR correction)
    python -m iak reindex ID [--extract]  rebuild passages, or extract again
    python -m iak delete ID               remove a document (database backed up)

The library folder is --library, or IAK_LIBRARY, or ./data. Servers come
from config/servers.toml (--host to pick a section) unless given with
--server (chat), --embed-server and --vision-server. Answers are in --lang
(IAK_LANG, default fr).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import analyze, answer, embed, search, servers
from .library import Library

ROOT = Path(__file__).resolve().parents[2]


def _out(text: str = "") -> None:
    print(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="iak", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", default=os.environ.get("IAK_LIBRARY", "data"),
                    help="library folder (default: IAK_LIBRARY or ./data)")
    ap.add_argument("--ocr-langs", default=os.environ.get("IAK_OCR_LANGS", "fra+eng"))
    ap.add_argument("--tesseract", default=os.environ.get("IAK_TESSERACT"))
    ap.add_argument("--host", help="[host.NAME] section of config/servers.toml")
    ap.add_argument("--server", help="chat server URL")
    ap.add_argument("--embed-server", help="embedding server URL")
    ap.add_argument("--vision-server", help="vision server URL (default: the chat "
                    "server when it has vision)")
    ap.add_argument("--lang", default=os.environ.get("IAK_LANG", "fr"),
                    help="language of summaries and analyses (fr, en)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--reference", action="store_true",
                   help="do not copy the files, only record where they are")
    p.add_argument("--no-embed", action="store_true",
                   help="do not compute vectors now (see the embed command)")
    sub.add_parser("list")
    sub.add_parser("status")
    p = sub.add_parser("embed")
    p.add_argument("--rebuild", action="store_true",
                   help="drop all vectors and compute them again (new model)")
    p = sub.add_parser("show")
    p.add_argument("id", type=int)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--doc", type=int, action="append", help="limit to document ID")
    p.add_argument("--kind", action="append", help="limit to a type (pdf, image...)")
    p.add_argument("--mode", choices=["auto", "hybrid", "sem", "kw"], default="auto")
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--doc", type=int, action="append")
    p.add_argument("--kind", action="append")
    p.add_argument("--mode", choices=["auto", "hybrid", "sem", "kw"], default="auto")
    p.add_argument("--budget", type=int, default=answer.DEFAULT_BUDGET_CHARS,
                   help="characters of passages sent to the model")
    p = sub.add_parser("look")
    p.add_argument("unit_id", type=int, help="unit (page) id, see the show command")
    p.add_argument("--task", default="describe",
                   choices=[*analyze.TASKS, "ask"])
    p.add_argument("--ask", default="", help="free question about the page (task ask)")
    p.add_argument("--no-ocr", action="store_true", help="do not give the OCR text")
    p = sub.add_parser("summarize")
    p.add_argument("id", type=int)
    p.add_argument("--refresh", action="store_true", help="ignore a stored summary")
    p = sub.add_parser("compare")
    p.add_argument("ids", type=int, nargs="+")
    p.add_argument("--question", default="", help="aspect to focus on")
    p = sub.add_parser("analyses")
    p.add_argument("id", type=int, nargs="?")
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=int(os.environ.get("IAK_PORT", "0")),
                   help="port on 127.0.0.1 (default: a free one, announced on stdout)")
    p.add_argument("--token", default=os.environ.get("IAK_TOKEN"),
                   help="access token (default: random, announced on stdout)")
    p.add_argument("--allow-origin", action="append", default=[],
                   help="extra web origin allowed (e.g. a development server)")
    p.add_argument("--watch-stdin", action="store_true",
                   help="stop when stdin closes (used by the desktop application)")
    p = sub.add_parser("edit")
    p.add_argument("unit_id", type=int)
    p.add_argument("file", type=Path, help="UTF-8 text file with the corrected text")
    p = sub.add_parser("reindex")
    p.add_argument("id", type=int)
    p.add_argument("--extract", action="store_true", help="extract (and OCR) again")
    p = sub.add_parser("delete")
    p.add_argument("id", type=int)
    args = ap.parse_args(argv)
    host = servers.load_host(ROOT / "config" / "servers.toml", args.host)
    if args.server:
        host.chat = args.server
    if args.embed_server:
        host.embedding = args.embed_server
    if args.vision_server:
        host.vision = args.vision_server

    if args.cmd == "serve":
        from . import api
        app = api.App(args.library, host, args.lang, args.ocr_langs, args.tesseract,
                      token=args.token, origins=set(args.allow_origin))
        api.serve(app, args.port, watch_stdin=args.watch_stdin)
        return 0

    def embedder(required: bool = False) -> embed.Embedder | None:
        """The embedding server, or None when it does not answer."""
        url = args.embed_server or host.embedding
        if not url:
            if required:
                raise SystemExit("no embedding server configured "
                                 "(config/servers.toml or --embed-server)")
            return None
        emb = embed.Embedder(servers.Client(url, host.api_key, timeout=300))
        try:
            emb.client.model()
        except servers.ServerError as exc:
            if required:
                raise SystemExit(f"embedding server unavailable: {exc}")
            return None
        return emb

    def run_embed(lib: Library, emb: embed.Embedder, rebuild: bool = False) -> int:
        t0 = time.perf_counter()

        def progress(done, total):
            print(f"\r  vectors {done}/{total}", end="", flush=True)
        try:
            n = (lib.rebuild_vectors if rebuild else lib.embed_pending)(emb, progress)
        except embed.EmbeddingMismatch as exc:
            _out(f"semantic index not updated: {exc}")
            _out("  keyword search still works; 'embed --rebuild' recomputes every vector")
            return 1
        except servers.ServerError as exc:
            _out(f"\nembedding server error: {exc}")
            return 1
        if n:
            dt = time.perf_counter() - t0
            print(f"\r  vectors {n} computed in {dt:.1f} s ({n / max(dt, 1e-6):.1f}/s)")
        return 0

    with Library(args.library, args.ocr_langs, args.tesseract) as lib:
        if args.cmd == "import":
            failed = 0
            for f in args.files:
                t0 = time.perf_counter()
                r = lib.import_file(f, copy=not args.reference)
                dt = time.perf_counter() - t0
                _out(f"{r.status:9} #{r.doc_id:<4} {r.name}  ({dt:.1f} s) {r.message}")
                failed += r.status == "error"
            if not args.no_embed and embed.pending(lib.con):
                emb = embedder()
                if emb is None:
                    _out("no embedding server: run 'embed' later for semantic search")
                else:
                    run_embed(lib, emb)
            return 1 if failed else 0

        if args.cmd == "embed":
            return run_embed(lib, embedder(required=True), args.rebuild)

        if args.cmd == "status":
            c = lib.con
            n_docs = c.execute("SELECT count(*) FROM documents").fetchone()[0]
            n_pass = c.execute("SELECT count(*) FROM passages").fetchone()[0]
            n_vec = c.execute("SELECT count(*) FROM vectors").fetchone()[0]
            fp = embed.db.get_setting(c, "embedding")
            _out(f"library  : {lib.folder}")
            _out(f"documents: {n_docs}   passages: {n_pass}   vectors: {n_vec}")
            _out(f"embedding: {fp['model']} ({fp['dim']} dimensions)" if fp
                 else "embedding: none yet")
            _out(f"host     : [{host.name}] chat={args.server or host.chat} "
                 f"embedding={args.embed_server or host.embedding}")
            return 0

        if args.cmd == "list":
            for d in lib.documents():
                added = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["added_at"]))
                ocr = f" ocr={d['n_ocr']}" if d["n_ocr"] else ""
                err = f"  ERROR: {d['error']}" if d["error"] else ""
                _out(f"#{d['id']:<4} {d['kind']:5} {d['status']:9} {added}  "
                     f"units={d['n_units']}{ocr} passages={d['n_passages']}  {d['name']}{err}")
            return 0

        if args.cmd == "show":
            d = lib.document(args.id)
            if d is None:
                _out(f"no document {args.id}")
                return 1
            _out(f"#{d['id']} {d['name']} ({d['kind']}, {d['status']})")
            _out(f"original: {lib.original_path(args.id)}")
            for u in lib.units(args.id):
                conf = f" conf={u['ocr_conf']}" if u["ocr_conf"] is not None else ""
                edited = " edited" if u["edited"] else ""
                _out(f"\n--- unit {u['id']}  {u['label']}  [{u['source']}{conf}{edited}]")
                _out(u["text"])
            return 0

        if args.cmd == "search":
            filters = search.Filters(args.doc, args.kind)
            try:
                hits, used = lib.find(args.query, args.n, filters,
                                      embedder(args.mode in ("sem", "hybrid")), args.mode)
            except embed.EmbeddingMismatch as exc:
                _out(f"semantic search unavailable: {exc}")
                hits, used = lib.find(args.query, args.n, filters, None, "kw")
            if not hits:
                _out("no result")
            for h in hits:
                _out(f"{h.score:7.2f} {h.via:6} {h.doc_name}, {h.label}"
                     f"  (#{h.doc_id}, unit {h.unit_id})")
                _out("         " + " ".join(h.snippet.split()))
            _out(f"({used} search)")
            return 0

        if args.cmd == "ask":
            url = args.server or host.chat
            if not url:
                _out("no chat server configured (config/servers.toml or --server)")
                return 1
            if not servers.is_local(url):
                _out(f"note: passages are sent to {url}, outside this computer")
            filters = search.Filters(args.doc, args.kind)
            try:
                hits, used = lib.find(args.question, 20, filters,
                                      embedder(args.mode in ("sem", "hybrid")), args.mode)
            except embed.EmbeddingMismatch as exc:
                _out(f"semantic search unavailable: {exc}")
                hits, used = lib.find(args.question, 20, filters, None, "kw")
            client = servers.Client(url, host.api_key)
            t0 = time.perf_counter()
            try:
                res = answer.ask(args.question, hits, client, args.budget)
            except servers.ServerError as exc:
                _out(f"chat server error: {exc}")
                return 1
            dt = time.perf_counter() - t0
            if res.status == "no_answer":
                _out("The documents do not allow an answer to this question.")
            else:
                if res.status == "unsourced":
                    _out("(warning: the answer cites no passage)")
                _out(res.text)
                _out("\nSources:")
                for s in res.sources:
                    _out(f"  [{s.ref}] {s.doc_name}, {s.label}")
            _out(f"\n({len(res.sent)} passages sent, {used} search, {dt:.1f} s)")
            return 0

        if args.cmd in ("look", "summarize", "compare"):
            shown = []

            def progress(i, n):
                print(f"\r  part {i}/{n}", end="", flush=True)
                shown.append(i)
            try:
                if args.cmd == "look":
                    client = analyze.vision_client(host, host.api_key, args.vision_server,
                                                   args.server)
                    task = "ask" if args.ask else args.task
                    res = analyze.look(lib, client, args.unit_id, task, args.ask,
                                       args.lang, use_ocr=not args.no_ocr)
                else:
                    url = args.server or host.chat
                    if not url:
                        _out("no chat server configured (config/servers.toml or --server)")
                        return 1
                    client = servers.Client(url, host.api_key)
                    if args.cmd == "summarize":
                        res = analyze.summarize(lib, client, args.id, args.lang,
                                                args.refresh, progress)
                    else:
                        res = analyze.compare(lib, client, args.ids, args.question,
                                              args.lang, progress)
            except (analyze.AnalysisError, servers.ServerError) as exc:
                _out(f"\n{exc}")
                return 1
            if shown:
                print()
            _out(res.text)
            took = f"{res.seconds:.1f} s" if res.seconds else "stored earlier"
            _out(f"\n(analysis #{res.id}, {res.task}, {took})")
            return 0

        if args.cmd == "analyses":
            for a in analyze.analyses(lib.con, args.id):
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(a["created_at"]))
                where = (f"doc #{a['doc_id']}" if a["doc_id"] else
                         "docs " + ",".join(f"#{d}" for d in json.loads(a["sources"])))
                where += f" unit {a['unit_id']}" if a["unit_id"] else ""
                _out(f"--- #{a['id']} {a['task']} {where} [{a['lang']}] {when}")
                _out(a["text"])
            return 0

        if args.cmd == "edit":
            text = args.file.read_text(encoding="utf-8")
            lib.set_unit_text(args.unit_id, text)
            _out(f"unit {args.unit_id} updated and reindexed")
            return 0

        if args.cmd == "reindex":
            if args.extract:
                lib.reextract(args.id)
            n = lib.reindex(args.id)
            _out(f"document {args.id}: {n} passages")
            return 0

        if args.cmd == "delete":
            lib.delete(args.id)
            _out(f"document {args.id} deleted (database backed up in {lib.folder / 'backups'})")
            return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
