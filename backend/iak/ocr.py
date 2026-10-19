"""Tesseract OCR through its command-line program.

The command line is used rather than a Python binding so that the same code
works with the system Tesseract on Linux and with a copy carried on the key
on Windows (set its path in the preferences).
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

DEFAULT_LANGS = "fra+eng"


class OcrError(RuntimeError):
    pass


@dataclass
class OcrResult:
    text: str
    conf: float | None                       # mean word confidence, 0-100
    boxes: list[list] = field(default_factory=list)  # [x0, y0, x1, y1, word, conf] in pixels


def find_tesseract(explicit: str | None = None) -> str:
    # a copy carried on the key (build/tesseract) comes first after explicit choices
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    exe = "tesseract.exe" if os.name == "nt" else "tesseract"
    candidates = [explicit, os.environ.get("IAK_TESSERACT"),
                  os.path.join(root, "build", "tesseract", exe), shutil.which("tesseract")]
    if os.name == "nt":
        candidates += [r"C:\Program Files\Tesseract-OCR\tesseract.exe"]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise OcrError("Tesseract not found: install it (Linux: tesseract-ocr and "
                   "tesseract-ocr-fra packages) or set its path (IAK_TESSERACT)")


def available_langs(tesseract: str | None = None) -> list[str]:
    exe = find_tesseract(tesseract)
    out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    lines = (out.stdout or out.stderr).splitlines()
    return [ln.strip() for ln in lines[1:] if ln.strip()]


def parse_tsv(tsv: str) -> OcrResult:
    """Rebuild text (lines and paragraphs) and word boxes from Tesseract TSV."""
    rows = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    lines: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    boxes: list[list] = []
    confs: list[float] = []
    for r in rows:
        if r.get("level") != "5":
            continue
        word = (r.get("text") or "").strip()
        if not word:
            continue
        try:
            conf = float(r["conf"])
            left, top = int(r["left"]), int(r["top"])
            width, height = int(r["width"]), int(r["height"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (int(r["block_num"]), int(r["par_num"]), int(r["line_num"]))
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)
        boxes.append([left, top, left + width, top + height, word, round(conf, 1)])
        if conf >= 0:
            confs.append(conf)
    out: list[str] = []
    prev_par = None
    for key in order:
        par = key[:2]
        if prev_par is not None and par != prev_par:
            out.append("")           # blank line between paragraphs
        out.append(" ".join(lines[key]))
        prev_par = par
    conf = round(sum(confs) / len(confs), 1) if confs else None
    return OcrResult(text="\n".join(out).strip(), conf=conf, boxes=boxes)


def ocr_image_bytes(png: bytes, langs: str = DEFAULT_LANGS,
                    tesseract: str | None = None, timeout: int = 300) -> OcrResult:
    """OCR an image given as PNG bytes. Coordinates are in image pixels."""
    exe = find_tesseract(tesseract)
    fd, path = tempfile.mkstemp(suffix=".png", prefix="iak-ocr-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(png)
        proc = subprocess.run([exe, path, "stdout", "-l", langs, "tsv"],
                              capture_output=True, timeout=timeout)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()
        raise OcrError(f"Tesseract failed ({proc.returncode}): {msg[:300]}")
    return parse_tsv(proc.stdout.decode("utf-8", "replace"))
