"""One standard layout for itinerary YAML files (scripts/format_dataset.py).

Metadata first, then the annotation: stops in block style (one field per line), legs and
violations one per line in flow style, times always quoted, keys in a fixed order. Default
preferences are written out in full rather than left implicit. Otherwise layout only:
`format_itinerary` refuses a file it can't rewrite without changing the data, or one with
comments of its own (rewriting would drop them).
"""

import json
import re
import textwrap
from datetime import date
from typing import Any

import yaml

from trip_feasibility.dataset.validate import _Loader

WIDTH = 100
TOP_KEYS = ["id", "city", "split", "parent_id", "source", "model", "prompt", "trip_start"]
PREF_KEYS = ["pace", "max_walk_km", "transport_modes"]
ANNOTATION_KEYS = ["stops", "legs", "violations", "notes"]
STOP_KEYS = [
    "id", "day", "travel_start", "travel_min", "travel_max", "start_time", "end_time",
    "time_of_day", "duration_min", "duration_max", "place_name", "type", "located", "hotel",
    "osm",
]  # fmt: skip
LEG_KEYS = ["from", "to", "mode", "minutes", "source"]
VIOLATION_KEYS = ["check", "severity", "stop", "day", "note", "source"]

# itinerary.TravelPreferences' defaults, written out when a file leaves them implicit
DEFAULT_PREFS = {"pace": "moderate", "max_walk_km": 10, "transport_modes": ["walk", "transit"]}
ANNOTATION_HEADER = "# --- annotation: add these three together (use [] when there are none) ---"
SKELETON = """\
# stops:
#   - id: s1
#     day: 1
#     start_time: "09:00"
#     end_time: "12:00"
#     place_name: ...
#     type: venue
# legs:
#   - { from: s1, to: s2, mode: transit, minutes: 20, source: Google Maps }
# violations:
#   - { check: opening_hours, severity: blocker, stop: s1, note: ..., source: ... }
# notes:"""
# comment lines the skeletons used to have, so files written before still format
_OLD_COMMENTS = {
    "# preferences: defaults (moderate, 10 km, walk + transit)",
    '#   - { id: s1, day: 1, start_time: "09:00", end_time: "12:00", '
    "place_name: ..., type: venue }",
}
_TIME = re.compile(r"^\d{1,2}:\d{2}$")


class LayoutError(Exception):
    """The file can't be rewritten safely; the message never quotes its content."""


def _load(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)


def _reads_back(fragment: str, value: Any, flow: bool) -> bool:
    try:
        loaded = _load(f"{{k: {fragment}}}" if flow else f"k: {fragment}")
    except yaml.YAMLError:
        return False
    return loaded == {"k": value}


def _scalar(value: Any, flow: bool = False) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, date)):
        return str(value) if not isinstance(value, date) else value.isoformat()
    if not isinstance(value, str):
        raise LayoutError(f"unexpected {type(value).__name__} value")
    if not _TIME.match(value) and value == value.strip() and _reads_back(value, value, flow):
        return value
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a YAML double-quoted one


def _flow(item: dict, order: list[str]) -> str:
    keys = [k for k in order if k in item] + [k for k in item if k not in order]
    parts = []
    for k in keys:
        v = item[k]
        if isinstance(v, dict):
            inner = ", ".join(f"{ik}: {_scalar(iv, flow=True)}" for ik, iv in v.items())
            parts.append(f"{k}: {{ {inner} }}")
        elif isinstance(v, list):
            parts.append(f"{k}: [{', '.join(_scalar(x, flow=True) for x in v)}]")
        else:
            parts.append(f"{k}: {_scalar(v, flow=True)}")
    return "{ " + ", ".join(parts) + " }"


def _text_field(key: str, value: Any) -> str:
    """A top-level field; long text folds onto indented lines when that reads back the same."""
    if value is None:
        return f"{key}:"
    line = f"{key}: {_scalar(value)}"
    if not isinstance(value, str) or len(line) <= WIDTH:
        return line
    if "\n" not in value:
        lines = textwrap.wrap(value, WIDTH - 2, break_long_words=False, break_on_hyphens=False)
        candidate = f"{key}: >-\n" + "\n".join(f"  {x}" for x in lines)
    else:
        candidate = f"{key}: |-\n" + "\n".join(f"  {x}" if x else "" for x in value.split("\n"))
    try:
        if _load(candidate) == {key: value}:
            return candidate
    except yaml.YAMLError:
        pass
    return line


def _stop_block(stop: dict) -> list[str]:
    keys = [k for k in STOP_KEYS if k in stop] + [k for k in stop if k not in STOP_KEYS]
    out = []
    for i, k in enumerate(keys):
        v = stop[k]
        rendered = _flow(v, list(v)) if isinstance(v, dict) else _scalar(v)
        out.append(f"{'  - ' if i == 0 else '    '}{k}: {rendered}")
    return out


def _own_comments(text: str, itinerary_id: str) -> bool:
    known = {
        *_OLD_COMMENTS,
        ANNOTATION_HEADER,
        "# --- annotation ---",
        f"# text: texts/{itinerary_id}.md",
    }
    known |= set(SKELETON.splitlines())
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            if line not in known:
                return True
        elif re.search(r"\s#", line):  # an inline comment (or a quoted " #": skipped too)
            return True
    return False


def format_itinerary(text: str) -> str:
    """The same itinerary in the standard layout. Raises LayoutError if that isn't safe."""
    try:
        data = _load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise LayoutError(
            f"not valid YAML (line {mark.line + 1})" if mark else "not valid YAML"
        ) from None
    if not isinstance(data, dict) or "id" not in data:
        raise LayoutError("not an itinerary file")
    if _own_comments(text, str(data["id"])):
        raise LayoutError("has comments of its own; format it by hand")

    known = set(TOP_KEYS) | {"preferences"} | set(ANNOTATION_KEYS)
    if unknown := [k for k in data if k not in known]:
        raise LayoutError(f"unknown keys: {', '.join(map(str, unknown))}")

    if "preferences" not in data:
        data = {**data, "preferences": DEFAULT_PREFS}
    out = [_text_field(k, data[k]) for k in TOP_KEYS if k in data]
    prefs = data["preferences"]
    if isinstance(prefs, dict):
        out.append("preferences:")
        for k in [k for k in PREF_KEYS if k in prefs] + [k for k in prefs if k not in PREF_KEYS]:
            v = prefs[k]
            rendered = (
                f"[{', '.join(_scalar(x, True) for x in v)}]" if isinstance(v, list) else _scalar(v)
            )
            out.append(f"  {k}: {rendered}")
    else:
        out.append(_text_field("preferences", prefs))
    out.append(f"# text: texts/{data['id']}.md")
    out.append("")

    if not any(k in data for k in ANNOTATION_KEYS):
        out += [ANNOTATION_HEADER, SKELETON]
    else:
        out.append("# --- annotation ---")
        for key, render in (("stops", _stop_block), ("legs", None), ("violations", None)):
            if key not in data:
                continue
            items = data[key]
            if not items:
                out.append(f"{key}:" if items is None else f"{key}: []")
                continue
            if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
                raise LayoutError(f"{key} isn't a list of entries")
            out.append(f"{key}:")
            for item in items:
                if render:
                    out += render(item)
                else:
                    out.append(f"  - {_flow(item, LEG_KEYS if key == 'legs' else VIOLATION_KEYS)}")
        if "notes" in data:
            out.append(_text_field("notes", data["notes"]))
    result = "\n".join(out) + "\n"

    if _load(result) != data:
        raise LayoutError("the standard layout wouldn't read back the same; format it by hand")
    return result
