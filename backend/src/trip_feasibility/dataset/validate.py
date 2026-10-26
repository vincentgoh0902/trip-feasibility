"""Loading and validating the dataset directory, and deriving the gold violations.

Layout (under data/dataset/, gitignored):
    hotels.yaml               the 16 test hotels and their red-flag labels
    itineraries/<id>.yaml     one file per itinerary: metadata and labels (see models.py)
    texts/<id>.md             its text, the reply exactly as copied (scripts/save_reply.sh)

`validate_dataset` returns every problem as a readable message. With `partial=True` (while
collecting and annotating) it skips the split counts and annotation completeness; every
other rule still applies.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from trip_feasibility.dataset.models import AnnotatedStop, DatasetItinerary, Hotel
from trip_feasibility.dataset.split import split_errors

# A leg with less than this between one stop's end and the next one's start needs its real
# travel time recorded, so travel-time labels can be derived from it.
TEXTS = "texts"  # <root>/texts/<id>.md, one per itinerary
TIGHT_GAP_MIN = 60
# The travel-time check's default buffer; gold labels assume the default config.
BUFFER_MIN = 10
# A journey the itinerary times itself ("allow 30–40 minutes") is a warning when the real
# time is more than this over its figure (journey planners vary by a few minutes).
CLAIM_MARGIN_MIN = 10

_INT = "tag:yaml.org,2002:int"
_FLOAT = "tag:yaml.org,2002:float"


class _Loader(yaml.SafeLoader):
    """YAML 1.1 reads 10:30 as the number 630 (base 60); here it stays the text "10:30"."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag not in (_INT, _FLOAT)]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(_INT, re.compile(r"^[-+]?[0-9]+$"), list("-+0123456789"))
_Loader.add_implicit_resolver(
    _FLOAT,
    re.compile(r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$"),
    list("-+.0123456789"),
)


def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


class GoldViolation(BaseModel):
    """A true violation, in the same terms the checks report: a stop, a leg or a day."""

    itinerary_id: str
    check: str
    severity: str
    # ("stop", id) | ("leg", from_id, to_id) | ("day", "2") | ("hotel", key)
    target: tuple[str, ...]
    derived: bool


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    hotels: list[Hotel] = field(default_factory=list)
    itineraries: list[DatasetItinerary] = field(default_factory=list)
    version: str = ""


def _messages(where: str, e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"])
        out.append(f"{where}: {loc + ': ' if loc else ''}{err['msg']}")
    return out


def dataset_version(root: Path) -> str:
    """A content hash of every dataset file: eval runs store it as `dataset_version`."""
    digest = hashlib.sha256()
    files = [root / "hotels.yaml", *(root / "itineraries").glob("*.yaml")]
    for path in sorted(files + list((root / TEXTS).glob("*.md"))):
        if path.exists():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return f"sha256:{digest.hexdigest()[:12]}"


def _gap_min(a: AnnotatedStop, b: AnnotatedStop) -> float | None:
    """Minutes from leaving a to b's stated start (the gap travel-time labels use): the
    stated departure (b.travel_start) if there is one, else a's stated end."""
    if a.day != b.day or b.start_time is None:
        return None
    if b.travel_start is not None:
        end = datetime.combine(datetime.min, b.travel_start)
    elif a.end_time is not None:
        end = datetime.combine(datetime.min, a.end_time)
        if a.start_time is not None and a.end_time < a.start_time:
            end += timedelta(days=1)  # ends after midnight
    else:
        return None
    start = datetime.combine(datetime.min, b.start_time)
    return (start - end).total_seconds() / 60


def _duration_gap_min(a: AnnotatedStop, b: AnnotatedStop) -> float | None:
    """The gap when a has no stated end but a stated duration: its end counted as start +
    duration_max (or duration_min). Only decides which legs to record, never a label."""
    duration = a.duration_max or a.duration_min
    if a.day != b.day or a.end_time is not None or a.start_time is None or duration is None:
        return None
    if b.start_time is None or b.travel_start is not None:
        return None
    end = datetime.combine(datetime.min, a.start_time) + timedelta(minutes=duration)
    start = datetime.combine(datetime.min, b.start_time)
    return (start - end).total_seconds() / 60


def _consecutive_pairs(stops: list[AnnotatedStop]) -> list[tuple[AnnotatedStop, AnnotatedStop]]:
    return [(a, b) for a, b in zip(stops, stops[1:], strict=False) if a.day == b.day]


def itinerary_errors(itin: DatasetItinerary, hotels: dict[str, Hotel]) -> list[str]:
    """Problems inside one itinerary file (the split is checked across files)."""
    if not itin.annotated:
        return []
    assert itin.stops is not None and itin.legs is not None and itin.violations is not None
    errors = []
    by_id: dict[str, AnnotatedStop] = {}
    for s in itin.stops:
        if s.id in by_id:
            errors.append(f"stop {s.id}: duplicate id")
        by_id[s.id] = s
        if s.place_name not in itin.clean_text:
            errors.append(
                f"stop {s.id}: place_name {s.place_name!r} isn't copied from the cleaned text"
            )
        if s.type == "hotel":
            hotel = hotels.get(s.hotel or "")
            if hotel is None:
                errors.append(f"stop {s.id}: hotel stops need a `hotel` key from hotels.yaml")
            elif hotel.city != itin.city:
                errors.append(f"stop {s.id}: hotel {hotel.key} is in {hotel.city}")
        elif s.hotel is not None:
            errors.append(f"stop {s.id}: only hotel stops have a `hotel` key")

    for a, b in zip(itin.stops, itin.stops[1:], strict=False):
        if b.day < a.day:
            errors.append(f"stop {b.id}: day {b.day} comes after day {a.day}; keep stops in order")
        elif b.day == a.day and a.start_time and b.start_time and b.start_time < a.start_time:
            errors.append(f"stop {b.id}: starts before stop {a.id}; keep stops in order")

    pairs = {(a.id, b.id): (a, b) for a, b in _consecutive_pairs(itin.stops)}
    legs = {}
    for leg in itin.legs:
        key = (leg.from_stop, leg.to_stop)
        if key not in pairs:
            errors.append(f"leg {leg.from_stop} → {leg.to_stop}: not consecutive stops on a day")
        elif key in legs:
            errors.append(f"leg {leg.from_stop} → {leg.to_stop}: recorded twice")
        legs[key] = leg
    for key, (a, b) in pairs.items():
        if not (a.located and b.located):
            if key in legs:
                errors.append(f"leg {a.id} → {b.id}: one end has no location (located: false)")
            continue
        if key in legs:
            continue
        gap = _gap_min(a, b)
        duration_gap = _duration_gap_min(a, b)
        if gap is not None and 0 <= gap < TIGHT_GAP_MIN:
            errors.append(
                f"leg {a.id} → {b.id}: only {gap:.0f} min apart, so record its real travel time"
            )
        elif duration_gap is not None and duration_gap < TIGHT_GAP_MIN:
            errors.append(
                f"leg {a.id} → {b.id}: only {duration_gap:.0f} min apart counting {a.id}'s "
                "duration, so record its real travel time"
            )
        elif b.travel_min is not None:
            errors.append(
                f"leg {a.id} → {b.id}: the itinerary says how long this journey takes, "
                "so record its real travel time"
            )

    days = {s.day for s in itin.stops}
    seen = set()
    for v in itin.violations:
        target = (v.check, v.stop or v.day)
        if target in seen:
            errors.append(f"violation {v.check} on {target[1]}: labelled twice")
        seen.add(target)
        if v.stop is not None and v.stop not in by_id:
            errors.append(f"violation {v.check}: stop {v.stop} doesn't exist")
        if v.day is not None and v.day not in days:
            errors.append(f"violation {v.check}: day {v.day} has no stops")
    return errors


def gold_violations(itin: DatasetItinerary, hotels: dict[str, Hotel]) -> list[GoldViolation]:
    """Hand labels plus the labels derived from real travel times and hotel red flags."""
    if not itin.annotated:
        return []
    assert itin.stops is not None and itin.legs is not None and itin.violations is not None

    def gold(check: str, severity: str, target: tuple, derived: bool) -> GoldViolation:
        target = tuple(str(t) for t in target)
        return GoldViolation(
            itinerary_id=itin.id, check=check, severity=severity, target=target, derived=derived
        )

    out = [
        gold(v.check, v.severity, ("stop", v.stop) if v.stop else ("day", v.day), False)
        for v in itin.violations
    ]
    legs = {(leg.from_stop, leg.to_stop): leg for leg in itin.legs}
    for a, b in _consecutive_pairs(itin.stops):
        gap = _gap_min(a, b)
        leg = legs.get((a.id, b.id))
        severity = None
        if gap is not None:
            if gap < 0 or (leg is not None and gap < leg.minutes):
                severity = "blocker"
            elif leg is not None and leg.minutes > 0 and gap < leg.minutes + BUFFER_MIN:
                severity = "warning"
        # the itinerary's own journey time is well short of the real one
        claimed = b.travel_max if b.travel_max is not None else b.travel_min
        over = leg is not None and claimed is not None and leg.minutes > claimed + CLAIM_MARGIN_MIN
        if severity is None and over:
            severity = "warning"
        if severity is not None:
            out.append(gold("travel_time", severity, ("leg", a.id, b.id), True))
    # one label per hotel, however many stops ("check in", "back to the hotel") name it
    for key in dict.fromkeys(s.hotel for s in itin.stops if s.hotel):
        hotel = hotels.get(key)
        if hotel is not None and hotel.red_flags:
            out.append(gold("hotel_red_flags", "warning", ("hotel", key), True))
    return out


def text_path(root: Path, itin_id: str) -> Path:
    """An itinerary's text: the reply exactly as copied (scripts/save_reply.sh writes it)."""
    return root / TEXTS / f"{itin_id}.md"


def load_dataset(root: Path) -> Report:
    report = Report(version=dataset_version(root))
    hotels_path = root / "hotels.yaml"
    if not hotels_path.exists():
        report.errors.append(f"{hotels_path}: missing")
    else:
        for i, raw in enumerate(load_yaml(hotels_path) or []):
            try:
                report.hotels.append(Hotel.model_validate(raw))
            except ValidationError as e:
                report.errors += _messages(f"hotels.yaml[{i}]", e)
    for path in sorted((root / "itineraries").glob("*.yaml")):
        where = f"itineraries/{path.name}"
        try:
            raw = load_yaml(path)
        except yaml.YAMLError as e:
            report.errors.append(f"{where}: not valid YAML: {e}")
            continue
        if not isinstance(raw, dict):
            report.errors.append(f"{where}: expected a mapping of fields")
            continue
        if "text" in raw:
            report.errors.append(f"{where}: no `text` here; the reply lives in {TEXTS}/<id>.md")
            continue
        text = text_path(root, path.stem)
        if not text.exists():
            report.errors.append(
                f"{where}: no text at {TEXTS}/{path.stem}.md (scripts/save_reply.sh {path.stem})"
            )
            continue
        try:
            itin = DatasetItinerary.model_validate(
                raw | {"text": text.read_text(encoding="utf-8").rstrip()}
            )
        except ValidationError as e:
            report.errors += _messages(where, e)
            continue
        if itin.id != path.stem:
            report.errors.append(f"{where}: id {itin.id!r} doesn't match the file name")
        report.itineraries.append(itin)
    return report


def validate_dataset(root: Path, *, partial: bool = False) -> Report:
    report = load_dataset(root)
    hotels: dict[str, Hotel] = {}
    for h in report.hotels:
        if h.key in hotels:
            report.errors.append(f"hotels.yaml: duplicate key {h.key}")
        hotels[h.key] = h
    for itin in report.itineraries:
        where = f"itineraries/{itin.id}.yaml"
        report.errors += [f"{where}: {e}" for e in itinerary_errors(itin, hotels)]

    report.errors += split_errors(report.itineraries, complete=not partial)
    if not partial:
        report.errors += [
            f"itineraries/{i.id}.yaml: not annotated yet"
            for i in report.itineraries
            if not i.annotated
        ]
        report.errors += [
            f"hotels.yaml: {h.key} has no red_flags label yet"
            for h in report.hotels
            if h.red_flags is None
        ]
        ids = {i.id for i in report.itineraries}
        report.errors += [
            f"{TEXTS}/{p.name}: no itineraries/{p.stem}.yaml"
            for p in sorted((root / TEXTS).glob("*.md"))
            if p.stem not in ids
        ]
    return report
