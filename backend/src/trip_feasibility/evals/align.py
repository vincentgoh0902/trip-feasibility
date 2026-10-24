"""Matching parsed stops to gold stops, for scoring end-to-end runs.

Stops are matched one to one within the same day, by place name (place_name is copied
verbatim from the text, so most match exactly), with order as the tie-breaker. A parsed stop
with no match is extra; a gold stop with no match was missed.
"""

import difflib
import re
import unicodedata
from typing import Protocol

MIN_NAME_SCORE = 0.6


class _Named(Protocol):
    day: int
    place_name: str


def _norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name).split())


def name_score(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    contained = 0.9 if a in b or b in a else 0.0
    return max(contained, difflib.SequenceMatcher(None, a, b).ratio())


def align(parsed: list[_Named], gold: list[_Named]) -> dict[int, int]:
    """parsed index -> gold index."""
    candidates = []
    for i, p in enumerate(parsed):
        for j, g in enumerate(gold):
            if p.day != g.day:
                continue
            score = name_score(p.place_name, g.place_name)
            if score >= MIN_NAME_SCORE:
                candidates.append((-score, abs(i - j), i, j))
    pairs: dict[int, int] = {}
    used: set[int] = set()
    for _, _, i, j in sorted(candidates):
        if i not in pairs and j not in used:
            pairs[i] = j
            used.add(j)
    return pairs
