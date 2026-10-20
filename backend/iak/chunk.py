"""Cut a unit's text into overlapping passages.

Passages stay inside one unit, so every passage has exactly one citation
(document + page or section). Cuts fall on paragraph, then sentence, then
word boundaries; consecutive passages overlap so that an idea split across a
cut is still found whole in one of them.

Sizes are in characters: about 4 characters per token for French and English.
The default (1,200 characters, about 300 tokens) lets 6 to 8 passages fit in
the 2,000-3,000-token budget measured as reasonable on the target laptop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TARGET = 1200
OVERLAP = 200
MIN_TAIL = 300


@dataclass
class Piece:
    start: int
    end: int
    text: str


def _cut_point(text: str, lo: int, hi: int) -> int:
    """Best place to end a passage in text[lo:hi]: the latest boundary."""
    window = text[lo:hi]
    for pattern in (r"\n\s*\n", r"(?<=[.!?…»])\s+", r"\n", r"\s+"):
        last = None
        for m in re.finditer(pattern, window):
            last = m
        if last is not None and last.end() > (hi - lo) // 3:
            return lo + last.end()
    return hi


def split(text: str, target: int = TARGET, overlap: int = OVERLAP) -> list[Piece]:
    text = text or ""
    n = len(text)
    if not text.strip():
        return []
    if n <= target + MIN_TAIL:
        s = len(text) - len(text.lstrip())
        return [Piece(s, len(text.rstrip()), text.strip())]
    pieces: list[Piece] = []
    start = 0
    while start < n:
        # skip leading whitespace
        while start < n and text[start].isspace():
            start += 1
        if start >= n:
            break
        if n - start <= target + MIN_TAIL:
            end = n
        else:
            end = _cut_point(text, start + target // 2, start + target)
        chunk = text[start:end].strip()
        if chunk:
            pieces.append(Piece(start, start + len(text[start:end].rstrip()), chunk))
        if end >= n:
            break
        # next passage starts `overlap` characters back, on a word boundary
        back = max(start + 1, end - overlap)
        m = re.search(r"\s", text[back:end])
        start = back + m.end() if m else end
    return pieces
