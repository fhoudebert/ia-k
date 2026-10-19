"""Command line for the IA-K backend, until the desktop interface exists.

    python -m iak import FILE...          import documents (copied into the library)
    python -m iak list                    documents, type, state, pages, OCR pages
    python -m iak show ID                 units of a document with their text
    python -m iak search "words"          keyword search, with citations
    python -m iak ask "question"          sourced answer from the chat server
    python -m iak edit UNIT_ID FILE       replace a page's text (OCR correction)
    python -m iak reindex ID [--extract]  rebuild passages, or extract again
    python -m iak delete ID               remove a document (database backed up)

The library folder is --library, or IAK_LIBRARY, or ./data.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import answer, search, servers
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
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--reference", action="store_true",
                   help="do not copy the files, only record where they are")
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("id", type=int)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--doc", type=int, action="append", help="limit to document ID")
    p.add_argument("--kind", action="append", help="limit to a type (pdf, image...)")
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--doc", type=int, action="append")
    p.add_argument("--server", help="chat server URL (default: config/servers.toml)")
    p.add_argument("--host", help="[host.NAME] section of config/servers.toml")
    p.add_argument("--budget", type=int, default=answer.DEFAULT_BUDGET_CHARS,
                   help="characters of passages sent to the model")
    p = sub.add_parser("edit")
    p.add_argument("unit_id", type=int)
    p.add_argument("file", type=Path, help="UTF-8 text file with the corrected text")
    p = sub.add_parser("reindex")
    p.add_argument("id", type=int)
    p.add_argument("--extract", action="store_true", help="extract (and OCR) again")
    p = sub.add_parser("delete")
    p.add_argument("id", type=int)
    args = ap.parse_args(argv)

    with Library(args.library, args.ocr_langs, args.tesseract) as lib:
        if args.cmd == "import":
            failed = 0
            for f in args.files:
                t0 = time.perf_counter()
                r = lib.import_file(f, copy=not args.reference)
                dt = time.perf_counter() - t0
                _out(f"{r.status:9} #{r.doc_id:<4} {r.name}  ({dt:.1f} s) {r.message}")
                failed += r.status == "error"
            return 1 if failed else 0

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
            hits = search.search(lib.con, args.query, args.n, args.doc, args.kind)
            if not hits:
                _out("no result")
            for h in hits:
                _out(f"{h.score:7.2f}  {h.doc_name}, {h.label}  (#{h.doc_id}, unit {h.unit_id})")
                _out("         " + " ".join(h.snippet.split()))
            return 0

        if args.cmd == "ask":
            host = servers.load_host(ROOT / "config" / "servers.toml", args.host)
            url = args.server or host.chat
            if not url:
                _out("no chat server configured (config/servers.toml or --server)")
                return 1
            if not servers.is_local(url):
                _out(f"note: passages are sent to {url}, outside this computer")
            hits = search.search(lib.con, args.question, 20, args.doc)
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
            _out(f"\n({len(res.sent)} passages sent, {dt:.1f} s)")
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
