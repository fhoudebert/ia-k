"""Reading pages again with the vision model, to correct or replace the text
Tesseract gave: difficult scans, old prints, tables, columns.

Two methods for a page:
- "transcribe": the model reads the image alone;
- "correct": the model gets the image AND the current text, and corrects
  that text where the image shows it is wrong. The model invents less, and
  the page keeps its line structure.

A reading is stored as an analysis (task transcribe or correct) with its
agreement with the page's text at the time (0-1, share of words in common): a low agreement
on a page whose OCR was good is a reason to look before using it. Nothing is
replaced until asked: "apply" makes a reading the page's text (reindexed,
the extracted text kept to go back to); a whole document can be read with
replacement, which never touches pages corrected by hand.
"""
from __future__ import annotations

import base64
import difflib
import json
import re
import time
from dataclasses import dataclass

from .analyze import SYSTEM, AnalysisError, _escape, _model_name, _save, render_unit
from .servers import Client

METHODS = ("transcribe", "correct")

COMMON = (
    "Keep the original spelling, punctuation, capitals and abbreviations exactly as "
    "printed, even if old or wrong: do not modernise, translate, summarise or complete "
    "the text. Keep the line breaks of the page and separate paragraphs with a blank "
    "line. Read columns one after the other, top to bottom. Write tables as Markdown "
    "tables. Keep headings, page numbers, notes and captions where they are. Mark a "
    "word you cannot read with [?]. Output only the text of the page, without any "
    "comment.")

PROMPTS = {
    "transcribe": "Transcribe all the text printed or written on this page, in reading "
                  "order. " + COMMON,
    "correct": "Below is the text of this page as read by OCR; it contains errors. "
               "Correct it by reading the image: wrong or missing letters and words, "
               "words joined or split by mistake, lines in the wrong order, text the OCR "
               "missed, tables. Change nothing that is already right. Give the corrected page once: a "
               "table appears only as the Markdown table, not also as the OCR's lines. " + COMMON,
}

# a dense page is about 4,000 characters, 1 token = about 3 characters
MAX_TOKENS = 3000
# a small vision model may repeat a line endlessly: the reading is capped
# from the length of the text it corrects, and repetition is discouraged
# (the setting Qwen recommends for Qwen3-VL)
SAMPLING = {"presence_penalty": 1.5, "top_p": 0.8, "top_k": 20}
DEFAULT_SIDE = 1600
LOW_CONFIDENCE = 80


@dataclass
class Reading:
    id: int
    unit_id: int
    method: str
    text: str
    score: float | None
    truncated: bool
    seconds: float


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def agreement(a: str, b: str) -> float | None:
    """Share of words in common (0-1), whatever their order: what matters is
    text invented or left out, not columns put back in reading order. None
    when a is empty."""
    from collections import Counter
    wa, wb = Counter(_words(a)), Counter(_words(b))
    if not wa:
        return None
    common = sum((wa & wb).values())
    return round(2 * common / (sum(wa.values()) + sum(wb.values())), 3)


def diff(old: str, new: str) -> list[list[str]]:
    """Word-level differences, as [op, text] with op "=", "-" (only in old) or
    "+" (only in new); whitespace kept with the words, so joining the "=" and
    "+" parts gives the new text."""
    ta = re.findall(r"\s+|[^\s]+", old)
    tb = re.findall(r"\s+|[^\s]+", new)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    out: list[list[str]] = []

    def add(op, toks):
        text = "".join(toks)
        if not text:
            return
        if out and out[-1][0] == op:
            out[-1][1] += text
        else:
            out.append([op, text])
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            add("=", ta[i1:i2])
        else:
            add("-", ta[i1:i2])
            add("+", tb[j1:j2])
    return out


def _clean(text: str) -> str:
    text = text.strip()
    fence = re.fullmatch(r"```[a-zA-Z]*\n(.*?)\n?```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return re.sub(r"^(?:here is|voici)[^\n]{0,80}:\s*\n", "", text, flags=re.I).strip()


def _drop_loop(text: str) -> str:
    """Remove a line repeated over and over at the end (a model in a loop)."""
    lines = text.split("\n")
    while len(lines) > 3 and lines[-1].strip() and \
            lines[-1].strip() == lines[-2].strip() == lines[-3].strip():
        lines.pop()
    return "\n".join(lines).strip()


def transcribe_png(client: Client, png: bytes, max_tokens: int = MAX_TOKENS) -> str:
    """The text of a picture, read by the vision model alone (not stored)."""
    content = [{"type": "text", "text": PROMPTS["transcribe"]},
               {"type": "image_url", "image_url": {
                   "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]
    text = client.chat([{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": content}],
                       max_tokens=max_tokens, temperature=0.1, extra=SAMPLING)
    return _drop_loop(_clean(text))


def read_page(lib, client: Client, unit_id: int, method: str = "transcribe",
              max_side: int = DEFAULT_SIDE, max_tokens: int = MAX_TOKENS) -> Reading:
    """Read one page or image again with the vision model; the reading is
    stored, not applied."""
    if method not in METHODS:
        raise AnalysisError(f"unknown method {method}: {', '.join(METHODS)}")
    unit = lib.con.execute("SELECT doc_id, text FROM units WHERE id=?", (unit_id,)).fetchone()
    if unit is None:
        raise AnalysisError(f"no unit {unit_id}")
    png = render_unit(lib, unit_id, max_side)
    prompt = PROMPTS[method]
    if method == "correct":
        if not unit["text"].strip():
            prompt = PROMPTS["transcribe"]          # nothing to correct: read it
            method = "transcribe"
        else:
            prompt += f"\n\n<ocr>\n{_escape(unit['text'], 'ocr')}\n</ocr>"
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {
                   "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]
    if method == "correct":
        # the corrected text is about as long as the OCR text, plus what it missed
        max_tokens = min(max_tokens, max(600, int(len(unit["text"]) / 2.5 * 1.5) + 300))
    info: dict = {}
    t0 = time.perf_counter()
    text = client.chat([{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": content}],
                       max_tokens=max_tokens, temperature=0.1, info=info, extra=SAMPLING)
    seconds = time.perf_counter() - t0
    text = _drop_loop(_clean(text))
    if not text:
        raise AnalysisError("the model returned an empty reading")
    truncated = info.get("finish_reason") == "length"
    score = agreement(unit["text"], text)
    rid = _save(lib.con, unit["doc_id"], unit_id, method, text, "", "", _model_name(client))
    with lib.con:
        lib.con.execute("UPDATE analyses SET score=?, sources=? WHERE id=?",
                        (score, json.dumps({"truncated": truncated}), rid))
    return Reading(rid, unit_id, method, text, score, truncated, seconds)


def apply(lib, analysis_id: int) -> int:
    """Make a reading the text of its page (reindexed); returns the unit id."""
    a = lib.con.execute("SELECT unit_id, task, text FROM analyses WHERE id=?",
                        (analysis_id,)).fetchone()
    if a is None or a["task"] not in METHODS or a["unit_id"] is None:
        raise AnalysisError(f"no page reading {analysis_id}")
    lib.set_unit_text(a["unit_id"], a["text"])
    with lib.con:
        lib.con.execute("UPDATE analyses SET applied=NULL WHERE unit_id=? AND task IN"
                        " ('transcribe', 'correct')", (a["unit_id"],))
        lib.con.execute("UPDATE analyses SET applied=? WHERE id=?", (time.time(), analysis_id))
    return a["unit_id"]


def restore(lib, unit_id: int) -> None:
    """Back to the text as extracted (OCR or the file's own text)."""
    u = lib.con.execute("SELECT doc_id, text_extracted FROM units WHERE id=?",
                        (unit_id,)).fetchone()
    if u is None:
        raise KeyError(f"no unit {unit_id}")
    with lib.con:
        lib.con.execute("UPDATE units SET text=text_extracted, edited=0 WHERE id=?", (unit_id,))
        lib.con.execute("UPDATE analyses SET applied=NULL WHERE unit_id=? AND task IN"
                        " ('transcribe', 'correct')", (unit_id,))
    lib.reindex(u["doc_id"], [unit_id])


def page_reader(lib, unit_id: int) -> dict | None:
    """The model whose reading is the page's text now, if any."""
    row = lib.con.execute(
        "SELECT a.id, a.model, a.task FROM analyses a JOIN units u ON u.id=a.unit_id"
        " WHERE a.unit_id=? AND a.applied IS NOT NULL AND a.text=u.text"
        " ORDER BY a.applied DESC LIMIT 1", (unit_id,)).fetchone()
    return dict(row) if row else None


def select_pages(lib, doc_id: int, pages: str = "low",
                 threshold: float = LOW_CONFIDENCE) -> list:
    """Units of a PDF or image document to read again: "low" (OCR pages below
    the confidence threshold, or nearly empty), "ocr" (every OCR page), "all"."""
    doc = lib.document(doc_id)
    if doc is None:
        raise KeyError(f"no document {doc_id}")
    if doc["kind"] not in ("pdf", "image"):
        raise AnalysisError(f"{doc['name']} is a {doc['kind']} document: only PDF pages "
                            "and images can be read again")
    units = lib.units(doc_id)
    if pages == "all":
        return list(units)
    ocr = [u for u in units if u["source"] == "ocr"]
    if pages == "ocr":
        return ocr
    if pages != "low":
        raise AnalysisError("pages is low, ocr or all")
    return [u for u in ocr if (u["ocr_conf"] is not None and u["ocr_conf"] < threshold)
            or len(u["text"].strip()) < 40]


def read_document(lib, client: Client, doc_id: int, method: str = "correct",
                  pages: str = "low", threshold: float = LOW_CONFIDENCE,
                  replace: bool = False, refresh: bool = False, progress=None,
                  cancelled=None, max_side: int = DEFAULT_SIDE) -> dict:
    """Read the chosen pages of a document again. Pages already read by this
    model with this method are skipped (unless refresh), so a cancelled run
    goes on where it stopped. With replace, each reading becomes the page's
    text, except on pages corrected by hand."""
    units = select_pages(lib, doc_id, pages, threshold)
    model = _model_name(client)
    out = {"pages": len(units), "read": 0, "skipped": 0, "replaced": 0, "kept_edited": 0,
           "truncated": 0, "cancelled": False, "readings": []}
    for i, u in enumerate(units, 1):
        if cancelled and cancelled():
            out["cancelled"] = True
            break
        if progress:
            progress(i, len(units))
        done = None
        if not refresh:
            done = lib.con.execute(
                "SELECT id FROM analyses WHERE unit_id=? AND task=? AND model=?"
                " ORDER BY id DESC LIMIT 1", (u["id"], method, model)).fetchone()
        if done:
            out["skipped"] += 1
            rid = done["id"]
        else:
            r = read_page(lib, client, u["id"], method, max_side)
            out["read"] += 1
            out["truncated"] += r.truncated
            rid = r.id
        out["readings"].append(rid)
        if replace:
            fresh = lib.con.execute("SELECT edited FROM units WHERE id=?", (u["id"],)).fetchone()
            by_vision = page_reader(lib, u["id"]) is not None
            if fresh["edited"] and not by_vision:
                out["kept_edited"] += 1            # a correction by hand wins
            else:
                apply(lib, rid)
                out["replaced"] += 1
    return out


def readers(lib, doc_id: int) -> dict[int, dict]:
    """{unit id: {"id", "model", "task"}} for the pages of a document whose text
    is a vision reading now."""
    rows = lib.con.execute(
        "SELECT a.unit_id, a.id, a.model, a.task FROM analyses a JOIN units u ON u.id=a.unit_id"
        " WHERE u.doc_id=? AND a.applied IS NOT NULL AND a.text=u.text ORDER BY a.applied",
        (doc_id,)).fetchall()
    return {r["unit_id"]: {"id": r["id"], "model": r["model"], "task": r["task"]} for r in rows}
