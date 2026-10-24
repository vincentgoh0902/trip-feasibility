"""Parse step 0: strip chat-interface debris from a pasted reply before extraction.

Users paste whole chatbot replies. Copied from a chat UI, these carry image links, source
citations, HTML entities, markdown escapes and the text of interactive widgets (map hints,
ratings, checklist counters), often more of it than itinerary. This removes what follows a
fixed pattern, in code; prose that isn't a schedule (tips, checklists) is left for
extraction to skip. The raw text is kept as received; only the cleaned text goes to the LLM.

CLEAN_VERSION changes whenever the rules change; eval runs record it with the prompt version.
"""

import html
import re

CLEAN_VERSION = "clean-v1"
# PRD FR-1. The raw limit only guards against abuse (scripts/save_reply.sh uses the same
# number); the cleaned limit is what extraction is sized for.
MAX_RAW_CHARS = 50_000
MAX_CLEAN_CHARS = 15_000

_IMAGE_URL = re.compile(
    r"images\.openai\.com|/s2/favicons|[?&]purpose=inline|\.(png|jpe?g|gif|webp|svg)(\?|$)",
    re.IGNORECASE,
)
_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)\)")
_LONE_LINK = re.compile(r"^\s*!?\[[^\]\n]*\]\(([^)\s]+)\)\s*$")
_FAVICON = re.compile(r"/s2/favicons", re.IGNORECASE)
_CITATION_COUNT = re.compile(r"^\s*\+\d+\s*$")  # "+1": more sources behind a citation
_MD_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
# a bare number or rating ("8", "4.7"), or a widget counter ("0 of 8")
_WIDGET_NUMBER = re.compile(r"^\s*(\d+([.,]\d+)?|\d+ of \d+)\s*$")
# widget text seen in copied replies, matched on the whole line, case-insensitively
_WIDGET_LINES = {
    "canvas",
    "use two fingers to move the map",
    "use ⌘ + scroll to zoom the map",
    "use ctrl + scroll to zoom the map",
}


def _is_image_link(line: str) -> bool:
    m = _LONE_LINK.match(line)
    return m is not None and _IMAGE_URL.search(m[1]) is not None


def _unlink(m: re.Match) -> str:
    """Markdown images go; links keep their text (a place name, often), not the URL."""
    return "" if m[1] or _IMAGE_URL.search(m[3]) else m[2]


def _drop_citations(lines: list[str]) -> list[str]:
    """A citation is a favicon link, a source title, then "+N". Drop all three; a favicon
    without a "+N" after the next line drops alone, as the title might be itinerary."""
    out, i = [], 0
    while i < len(lines):
        if not _FAVICON.search(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        nonblank = [j for j in range(i + 1, min(i + 6, len(lines))) if lines[j].strip()]
        if len(nonblank) >= 2 and _CITATION_COUNT.match(lines[nonblank[1]]):
            i = nonblank[1] + 1
        elif nonblank and _CITATION_COUNT.match(lines[nonblank[0]]):
            i = nonblank[0] + 1
        else:
            i += 1
    return out


def clean_text(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = _drop_citations(lines)
    out = []
    for line in lines:
        if _is_image_link(line) or _CITATION_COUNT.match(line):
            continue
        line = _LINK.sub(_unlink, line)
        line = html.unescape(_MD_ESCAPE.sub(r"\1", line)).replace("\xa0", " ").rstrip()
        if _WIDGET_NUMBER.match(line) or line.strip().lower() in _WIDGET_LINES:
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
