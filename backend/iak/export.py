"""Markdown exports of answers, conversations and analyses, with their sources.

An answer keeps its citation marks [S1]; a "Sources" list under it says
what each one is: document, page or section, and the passage quoted. A
summary lists the pages it cites; a comparison says which document D1, D2...
are. Headings follow the interface language (fr, en).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

LABELS = {
    "fr": {"sources": "Sources", "question": "Question", "no_answer":
           "Les documents ne permettent pas de répondre à cette question.",
           "unsourced": "Réponse sans source vérifiée : à contrôler.",
           "scope": "Limité à", "exported": "Exporté d'IA-K le {date}",
           "made": "Produit par IA-K le {date}",
           "conversation": "Questions sur la bibliothèque", "summary": "Résumé",
           "compare": "Comparaison", "documents": "Documents comparés", "focus": "Aspect",
           "pages": "Pages citées", "document": "Document", "model": "Modèle",
           "page": "Page", "agreement": "concordance avec le texte de la page : {pct} %",
           "describe": "Description de la page", "transcribe": "Transcription de la page",
           "correct": "Texte OCR corrigé", "error": "Analyse d'erreur",
           "extract": "Informations extraites", "ask": "Question sur la page"},
    "en": {"sources": "Sources", "question": "Question", "no_answer":
           "The documents do not allow an answer to this question.",
           "unsourced": "Answer without a verified source: to be checked.",
           "scope": "Limited to", "exported": "Exported from IA-K on {date}",
           "made": "Made by IA-K on {date}",
           "conversation": "Questions about the library", "summary": "Summary",
           "compare": "Comparison", "documents": "Documents compared", "focus": "Aspect",
           "pages": "Pages cited", "document": "Document", "model": "Model",
           "page": "Page", "agreement": "agreement with the page's text: {pct} %",
           "describe": "Description of the page", "transcribe": "Transcription of the page",
           "correct": "Corrected OCR text", "error": "Error analysis",
           "extract": "Extracted information", "ask": "Question about the page"},
}

EXCERPT = 300


def _l(lang: str) -> dict:
    return LABELS.get(lang, LABELS["en"])


def _sep(lang: str) -> str:
    return " : " if lang == "fr" else ": "


def _date(ts: float | None = None, lang: str = "fr") -> str:
    t = time.localtime(ts or time.time())
    return time.strftime("%d/%m/%Y %H:%M" if lang == "fr" else "%Y-%m-%d %H:%M", t)


def _inline(text: str) -> str:
    """One line of plain text, safe inside Markdown (no heading or list marks)."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"([*_`\[\]<>])", r"\\\1", text)


def excerpt(text: str, n: int = EXCERPT) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= n:
        return text
    cut = text.rfind(" ", 0, n)
    return text[:cut if cut > n // 2 else n].rstrip(" ,;:") + " …"


def source_lines(sources: list[dict], quote: bool = True) -> list[str]:
    out = []
    for s in sources:
        line = f"- **[{s.get('ref', '?')}]** *{_inline(s.get('doc_name'))}*, {_inline(s.get('label'))}"
        if quote and s.get("text"):
            line += f"\n  > {_inline(excerpt(s['text']))}"
        out.append(line)
    return out


def answer_md(turn: dict, lang: str = "fr", level: int = 2, quote: bool = True) -> str:
    """turn: {question, text, status, sources: [{ref, doc_name, label, text}], scope}."""
    L = _l(lang)
    h = "#" * level
    lines = [f"{h} {_inline(turn.get('question'))}", ""]
    if turn.get("scope"):
        lines += [f"*{L['scope']}{_sep(lang)}{_inline(turn['scope'])}*", ""]
    status = turn.get("status", "answered")
    if status == "no_answer":
        lines += [L["no_answer"], ""]
        return "\n".join(lines)
    if status == "unsourced":
        lines += [f"> {L['unsourced']}", ""]
    lines += [str(turn.get("text") or "").strip(), ""]
    sources = turn.get("sources") or []
    cited = set(re.findall(r"\[(S\d+)\]", str(turn.get("text") or "")))
    sources = [s for s in sources if not cited or s.get("ref") in cited]
    if sources:
        lines += [f"**{L['sources']}**", ""] + source_lines(sources, quote) + [""]
    return "\n".join(lines)


def conversation_md(turns: list[dict], lang: str = "fr", title: str | None = None,
                    quote: bool = True) -> str:
    L = _l(lang)
    lines = [f"# {_inline(title or L['conversation'])}", "",
             f"*{L['exported'].format(date=_date(lang=lang))}*", ""]
    for turn in turns:
        lines.append(answer_md(turn, lang, 2, quote))
    return "\n".join(lines).rstrip() + "\n"


def _pages_cited(text: str) -> list[str]:
    """[p. 3], [p. 3-4], [chap. 2], [D1, p. 5]: the labels, in order of appearance."""
    seen: list[str] = []
    for m in re.finditer(r"\[([^\[\]]{1,40})\]", text):
        for part in re.split(r"\s*[;]\s*", m.group(1)):
            if re.match(r"^(D\d+,\s*)?(p\.|pp\.|l\.|chap\.|sect\.|§)\s*\S", part.strip()):
                label = part.strip()
                if label not in seen:
                    seen.append(label)
    return seen


def analysis_md(lib, analysis_id: int, lang: str = "fr") -> tuple[str, str]:
    """(file name suggestion, Markdown) for a stored analysis."""
    a = lib.con.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
    if a is None:
        raise KeyError(f"no analysis {analysis_id}")
    L = _l(lang)
    task = a["task"]
    doc = lib.document(a["doc_id"]) if a["doc_id"] else None
    unit = (lib.con.execute("SELECT label FROM units WHERE id=?", (a["unit_id"],)).fetchone()
            if a["unit_id"] else None)
    sep = _sep(lang)
    info = L["made"].format(date=_date(a["created_at"], lang))
    if a["model"]:
        info += f" · {L['model']}{sep}{_inline(a['model'])}"
    meta = [f"*{info}*"]
    text = str(a["text"] or "").strip()

    if task == "compare":
        ids = json.loads(a["sources"] or "[]")
        if isinstance(ids, dict):
            ids = []
        names = []
        for i, d in enumerate(ids, 1):
            row = lib.document(d)
            names.append((f"D{i}", row["name"] if row else f"#{d}"))
        title = f"{L['compare']} — " + ", ".join(n for _, n in names)
        lines = [f"# {_inline(title)}", "", *meta, ""]
        if a["question"]:
            lines += [f"**{L['focus']}**{sep}{_inline(a['question'])}", ""]
        lines += [f"**{L['documents']}**", ""]
        lines += [f"- **{ref}** *{_inline(name)}*" for ref, name in names] + [""]
        lines += [text, ""]
        stem = "comparaison" if lang == "fr" else "comparison"
        return f"{stem} - " + " - ".join(Path(n).stem for _, n in names)[:80] + ".md", \
            "\n".join(lines).rstrip() + "\n"

    name = doc["name"] if doc else "?"
    head = L["summary"] if task == "summary" else L.get(task, task)
    title = f"{head} — {name}" + (f", {unit['label']}" if unit else "")
    lines = [f"# {_inline(title)}", "", *meta, ""]
    if task == "ask" and a["question"]:
        lines += [f"**{L['question']}**{sep}{_inline(a['question'])}", ""]
    if task in ("correct", "transcribe") and a["score"] is not None:
        lines += [f"*{L['agreement'].format(pct=round(a['score'] * 100))}*", ""]
    lines += [text, ""]
    lines += [f"**{L['sources']}**", "", f"- {L['document']}{sep}*{_inline(name)}*"]
    if unit:
        lines.append(f"- {L['page']}{sep}{_inline(unit['label'])}")
    pages = _pages_cited(text) if task == "summary" else []
    if pages:
        lines.append(f"- {L['pages']}{sep}" + ", ".join(_inline(p) for p in pages))
    stem = Path(name).stem + (f" - {unit['label']}" if unit else "")
    return f"{head} - {stem}"[:100] + ".md", "\n".join(lines).rstrip() + "\n"


def write(path: str | os.PathLike, text: str) -> Path:
    """Write text to path through a temporary file (no half-written export)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def safe_name(name: str) -> str:
    """A file name valid on Windows, macOS and Linux (and exFAT)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    return re.sub(r"\s+", " ", name).strip(" .") or "export.md"
