#!/usr/bin/env python3
"""Fetch the French lexicon used for the doubtful OCR words and their
correction: build/words/fr.txt, one word per line.

    python3 scripts/fetch_words.py

The list is "an-array-of-french-words" (about 336,000 forms, MIT licence,
https://github.com/words/an-array-of-french-words), the one Prescel uses,
taken at a fixed commit and checked against its SHA-256. IA-K finds it in
build/words/ (iak.doubt.lexicon); make_portable.py puts it on the key.
Run it again safely: a complete list is kept.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "words"
COMMIT = "24a6b29562cd0f8c4090262dbe303413f68aafe9"
URL = f"https://raw.githubusercontent.com/words/an-array-of-french-words/{COMMIT}/index.json"
SHA256 = "75e81515ed0714c8d98d6aa0cc71ba3094834653de005f9bdbb874257639e92d"
LICENCE = """an-array-of-french-words, (The MIT License)
Copyright (c) 2016 Zeke Sikelianos <zeke@sikelianos.com>
https://github.com/words/an-array-of-french-words
"""


def main() -> int:
    target = OUT / "fr.txt"
    if target.is_file() and target.stat().st_size > 1_000_000:
        print(f"{target}: already there")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("checksum mismatch, list refused")
    words = sorted({w.strip().lower() for w in json.loads(data.decode("utf-8")) if w.strip()})
    part = target.with_suffix(".part")
    part.write_text("\n".join(words) + "\n", encoding="utf-8")
    part.replace(target)
    (OUT / "fr.LICENSE").write_text(LICENCE, encoding="utf-8")
    print(f"{target}: {len(words)} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
