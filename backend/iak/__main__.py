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
    python -m iak reread ID [--pages low|ocr|all] [--method correct|transcribe] [--replace]
                                          read pages again with the vision model
    python -m iak summarize ID            summary of a document, with page labels
    python -m iak compare ID ID...        compare documents (from their summaries)
    python -m iak analyses [ID]           stored analyses
    python -m iak collection [list|create NAME|add NAME ID...|remove NAME ID...|
                              rename NAME NEW|delete NAME|show NAME]
                                          named groups of documents (--collection
                                          NAME limits search and ask to one)
    python -m iak export ANALYSIS [-o F]  an analysis (summary, comparison...) as Markdown
    python -m iak translate ID --to en    translate a document page by page (kept),
                                          or --text "..." / --file F.txt; --out FILE
                                          writes the translated document
    python -m iak book FILE|--doc ID --to fr  translate a whole book, layout kept
                                          (EPUB, PDF, TXT, MD, HTML, DOCX); resumable
    python -m iak serve [--port N]        local API for the desktop interface
    python -m iak edit UNIT_ID FILE       replace a page's text (OCR correction)
    python -m iak reindex ID [--extract]  rebuild passages, or extract again
    python -m iak delete ID               remove a document (database backed up)

The library folder is --library, or IAK_LIBRARY, or ./data. Servers come
from config/servers.toml (--host to pick a section) unless given with
--server (chat), --embed-server, --vision-server and --translate-server. Answers are in --lang
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
    ap.add_argument("--translate-server", help="translation server URL (default: "
                    "the chat server translates)")
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
    p.add_argument("--collection", action="append", help="limit to a collection (name)")
    p.add_argument("--mode", choices=["auto", "hybrid", "sem", "kw"], default="auto")
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--doc", type=int, action="append")
    p.add_argument("--kind", action="append")
    p.add_argument("--mode", choices=["auto", "hybrid", "sem", "kw"], default="auto")
    p.add_argument("--budget", type=int, default=answer.DEFAULT_BUDGET_CHARS,
                   help="characters of passages sent to the model")
    p.add_argument("--md", type=Path, help="also write the answer and its sources as Markdown")
    p.add_argument("--collection", action="append", help="limit to a collection (name)")
    p = sub.add_parser("look")
    p.add_argument("unit_id", type=int, help="unit (page) id, see the show command")
    p.add_argument("--task", default="describe",
                   choices=[*analyze.TASKS, "ask"])
    p.add_argument("--ask", default="", help="free question about the page (task ask)")
    p.add_argument("--no-ocr", action="store_true", help="do not give the OCR text")
    p = sub.add_parser("reread")
    p.add_argument("id", type=int, help="document id (PDF or image)")
    p.add_argument("--pages", choices=("low", "ocr", "all"), default="low",
                   help="low: OCR pages below --threshold (default); ocr: every OCR page")
    p.add_argument("--threshold", type=float, default=80, help="OCR confidence, 0-100")
    p.add_argument("--method", choices=("correct", "transcribe"), default="correct",
                   help="correct the OCR text from the image (default), or read the image alone")
    p.add_argument("--replace", action="store_true",
                   help="make each reading the page's text (pages corrected by hand are kept)")
    p.add_argument("--refresh", action="store_true", help="read pages already read again")
    p = sub.add_parser("summarize")
    p.add_argument("id", type=int)
    p.add_argument("--refresh", action="store_true", help="ignore a stored summary")
    p = sub.add_parser("compare")
    p.add_argument("ids", type=int, nargs="+")
    p.add_argument("--question", default="", help="aspect to focus on")
    p = sub.add_parser("collection")
    p.add_argument("action", nargs="?", default="list",
                   choices=("list", "show", "create", "add", "remove", "rename", "delete"))
    p.add_argument("name", nargs="?", help="collection name")
    p.add_argument("args", nargs="*", help="document ids (add, remove) or the new name (rename)")
    p = sub.add_parser("export")
    p.add_argument("analysis", type=int, help="analysis id (see analyses)")
    p.add_argument("-o", "--out", type=Path, help="file to write (default: printed)")
    p = sub.add_parser("analyses")
    p.add_argument("id", type=int, nargs="?")
    p = sub.add_parser("translate")
    p.add_argument("id", type=int, nargs="?", help="document id (see list)")
    p.add_argument("--to", required=True, help="target language code: fr, en, la...")
    p.add_argument("--from", dest="src", help="source language (default: detected)")
    p.add_argument("--text", help="translate this text instead of a document")
    p.add_argument("--file", type=Path, help="translate this UTF-8 text file")
    p.add_argument("--unit", type=int, help="only this page or section (unit id)")
    p.add_argument("--refresh", action="store_true", help="ignore stored translations")
    p.add_argument("--out", type=Path, help="write the translated document (.md or .txt)")
    p.add_argument("--bilingual", action="store_true", help="--out with the original text")
    p = sub.add_parser("book")
    p.add_argument("file", type=Path, nargs="?", help="EPUB, PDF, TXT, MD, HTML or DOCX")
    p.add_argument("--doc", type=int, help="a library document instead of a file")
    p.add_argument("--to", required=True, help="target language code")
    p.add_argument("--from", dest="src", default="auto", help="source language (default: detected)")
    p.add_argument("--passes", type=int, choices=(1, 2, 3), default=2,
                   help="1 translation, 2 + revision (default), 3 + final proofreading")
    p.add_argument("--glossary", type=Path, help="text file of 'term = translation' lines")
    p.add_argument("--limit", type=int, default=0, help="trial: only the first N units")
    p.add_argument("--fresh", action="store_true", help="forget earlier results")
    p.add_argument("-o", "--out", type=Path, help="copy the translated book here")
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
    if args.translate_server:
        host.translate = args.translate_server

    if args.cmd == "serve":
        from . import api
        app = api.App(args.library, host, args.lang, args.ocr_langs, args.tesseract,
                      token=args.token, origins=set(args.allow_origin),
                      config=ROOT / "config" / "servers.toml")
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

        def collection_ids(names):
            from . import collection
            try:
                return [collection.by_name(lib.con, n)["id"] for n in names] if names else None
            except KeyError as exc:
                raise SystemExit(str(exc).strip("'"))

        if args.cmd == "collection":
            from . import collection
            try:
                if args.action == "list":
                    for c in collection.all_(lib.con):
                        _out(f"{c['name']}  ({c['n_docs']} document(s))")
                    return 0
                if not args.name:
                    _out("give the collection's name")
                    return 1
                if args.action == "create":
                    c = collection.create(lib.con, args.name)
                    if args.args:
                        collection.add(lib.con, c["id"], [int(x) for x in args.args])
                    _out(f"collection {c['name']} created")
                    return 0
                c = collection.by_name(lib.con, args.name)
                if args.action == "show":
                    for d in c["doc_ids"]:
                        row = lib.document(d)
                        _out(f"#{d:<5} {row['name']}")
                elif args.action == "add":
                    n = collection.add(lib.con, c["id"], [int(x) for x in args.args])
                    _out(f"{n} document(s) added to {c['name']}")
                elif args.action == "remove":
                    n = collection.remove(lib.con, c["id"], [int(x) for x in args.args])
                    _out(f"{n} document(s) removed from {c['name']}")
                elif args.action == "rename":
                    if len(args.args) != 1:
                        _out("give the new name")
                        return 1
                    _out(f"renamed: {collection.rename(lib.con, c['id'], args.args[0])['name']}")
                elif args.action == "delete":
                    collection.delete(lib.con, c["id"])
                    _out(f"collection {c['name']} deleted (its documents stay in the library)")
            except (collection.CollectionError, KeyError, ValueError) as exc:
                _out(str(exc).strip("'"))
                return 1
            return 0

        if args.cmd == "search":
            filters = search.Filters(args.doc, args.kind,
                                     collections=collection_ids(args.collection))
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
            filters = search.Filters(args.doc, args.kind,
                                     collections=collection_ids(args.collection))
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
            if args.md:
                from . import export
                turn = {"question": args.question, "text": res.text, "status": res.status,
                        "sources": [{"ref": s.ref, "doc_name": s.doc_name, "label": s.label,
                                     "text": s.text} for s in res.sources]}
                _out(f"written: {export.write(args.md, export.answer_md(turn, args.lang, 1))}")
            return 0

        if args.cmd == "export":
            from . import export
            try:
                name, md = export.analysis_md(lib, args.analysis, args.lang)
            except KeyError as exc:
                _out(str(exc))
                return 1
            if args.out:
                _out(f"written: {export.write(args.out, md)}")
            else:
                print(md, end="")
            return 0

        if args.cmd == "reread":
            from . import transcription
            try:
                client = analyze.vision_client(host, host.api_key, args.vision_server,
                                               args.server)
                r = transcription.read_document(
                    lib, client, args.id, args.method, args.pages, args.threshold,
                    args.replace, args.refresh,
                    lambda i, n: print(f"\r  page {i}/{n}", end="", flush=True))
            except (analyze.AnalysisError, servers.ServerError, KeyError) as exc:
                _out(f"\n{exc}")
                return 1
            print()
            for rid in r["readings"]:
                a = lib.con.execute("SELECT a.score, u.label FROM analyses a JOIN units u"
                                    " ON u.id=a.unit_id WHERE a.id=?", (rid,)).fetchone()
                score = "-" if a["score"] is None else f"{a['score']:.0%}"
                _out(f"  {a['label']:<10} reading #{rid}  agreement with the text: {score}")
            _out(f"{r['pages']} page(s): {r['read']} read, {r['skipped']} already read, "
                 f"{r['replaced']} replaced, {r['kept_edited']} kept (corrected by hand)"
                 + (f", {r['truncated']} cut short" if r["truncated"] else ""))
            if r["replaced"]:
                emb = embedder()
                if emb:
                    lib.embed_pending(emb)
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

        if args.cmd == "translate":
            from . import translate
            try:
                tr = translate.translator_for(host, host.api_key)
                print(f"({tr.model}, {tr.format})", file=sys.stderr)
                progress = lambda i, n: print(f"\r  {i}/{n}", end="", file=sys.stderr,
                                              flush=True)
                if args.text is not None or args.file:
                    text = args.text if args.text is not None else \
                        args.file.read_text(encoding="utf-8")
                    src = args.src or translate.detect_language(text)
                    if not src:
                        _out("the language of the text could not be detected: use --from")
                        return 1
                    out = tr.translate(text, src, args.to, progress)
                    print(file=sys.stderr)
                    _out(out)
                    return 0
                if args.unit:
                    u = lib.con.execute("SELECT doc_id FROM units WHERE id=?",
                                        (args.unit,)).fetchone()
                    if u is None:
                        _out(f"no unit {args.unit}")
                        return 1
                    src = args.src or translate.document_language(lib, u["doc_id"])
                    if not src:
                        _out("the language of the document could not be detected: use --from")
                        return 1
                    text, _ = translate.translate_unit(lib, tr, args.unit, src, args.to,
                                                       args.refresh)
                    _out(text)
                    return 0
                if args.id is None:
                    _out("give a document id, --unit, --text or --file")
                    return 1
                r = translate.translate_document(lib, tr, args.id, args.src, args.to,
                                                 args.refresh, progress)
                print(file=sys.stderr)
                _out(f"document {args.id} ({r['src']} -> {r['tgt']}): {r['translated']} "
                     f"translated, {r['cached']} already done")
                if args.out:
                    fmt = "txt" if args.out.suffix.lower() == ".txt" else "md"
                    _out(f"written: {translate.export(lib, args.id, args.to, args.out, fmt, args.bilingual)}")
            except (translate.TranslateError, servers.ServerError, KeyError) as exc:
                _out(f"\n{exc}")
                return 1
            return 0

        if args.cmd == "book":
            from . import api, book, translate
            try:
                if args.doc is not None:
                    info = api._book_source(lib, args.doc)
                elif args.file:
                    info = api._file_source(str(args.file))
                else:
                    _out("give a FILE or --doc ID")
                    return 1
            except api.ApiError as exc:
                _out(str(exc))
                return 1
            src = args.src
            if src == "auto" and info.get("doc_language") in translate.ENGLISH_NAMES:
                src = info["doc_language"]
            info.pop("doc_language", None)
            glossary = args.glossary.read_text(encoding="utf-8-sig") if args.glossary else ""
            job = book.prepare(lib.folder, {**info, "src": src, "tgt": args.to,
                                            "passes": args.passes, "glossary": glossary,
                                            "limit": args.limit, "fresh": args.fresh})
            job.echo = _out
            try:
                tr = translate.translator_for(host, host.api_key)
                status = book.run_job(job, tr, lambda: False)
            except (book.BookError, translate.TranslateError, servers.ServerError) as exc:
                job.save(status="error", error=str(exc))
                _out(f"stopped: {exc}  (what is translated is kept: run again to resume)")
                return 1
            except KeyboardInterrupt:
                job.save(status="interrupted")
                _out("interrupted: run the same command again to resume")
                return 130
            job.save(status=status)
            if args.out and job.output:
                import shutil
                shutil.copyfile(job.output, args.out)
                _out(f"written: {args.out}")
            else:
                _out(f"translated book: {job.output}")
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
