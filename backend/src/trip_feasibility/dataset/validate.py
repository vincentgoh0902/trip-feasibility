"""Loading and validating the dataset directory, and deriving the gold violations.

Layout (under data/dataset/, gitignored):
    hotels.yaml               the 16 test hotels and their red-flag labels
    itineraries/<id>.yaml     one file per itinerary: metadata and labels (see models.py)
    texts/<id>.md             its text, the reply exactly as copied (scripts/save_reply.sh)

`validate_dataset` returns every problem as a readable message. With `partial=True` (while
collecting and annotating) it skips the split counts and annotation completeness; every
other rule still applies. A drafted file is checked as soon as it's there, with "verify the
draft" listed as a problem until a human has. Stops and legs (dev only, for parser
debugging) are checked when present.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from trip_feasibility.dataset.models import AnnotatedStop, DatasetItinerary, Hotel
from trip_feasibility.dataset.split import split_errors

TEXTS = "texts"  # <root>/texts/<id>.md, one per itinerary

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
    """A true violation as scoring sees it: check, day and place in the text's words."""

    itinerary_id: str
    check: str
    severity: str
    day: int | None  # None for hotel_red_flags (the whole stay)
    place: str | None  # None for public_holidays and daily_load
    from_place: str | None = None
    confidence: str
    derived: bool  # from hotels.yaml rather than the file


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


def _consecutive_pairs(stops: list[AnnotatedStop]) -> list[tuple[AnnotatedStop, AnnotatedStop]]:
    return [(a, b) for a, b in zip(stops, stops[1:], strict=False) if a.day == b.day]


def itinerary_errors(itin: DatasetItinerary, hotels: dict[str, Hotel]) -> list[str]:
    """Problems inside one itinerary file (the split is checked across files). Each part is
    checked once it's there, so a file can be checked while in progress."""
    errors = [f"still to do: {t}" for t in itin.still_to_do]
    if itin.hotel is not None:
        hotel = hotels.get(itin.hotel)
        if hotel is None:
            errors.append(f"hotel {itin.hotel}: not in hotels.yaml")
        elif hotel.city != itin.city:
            errors.append(f"hotel {itin.hotel} is in {hotel.city}")
    errors += _violation_errors(itin)
    if itin.stops is not None:
        errors += _stop_errors(itin, hotels)
    return errors


def _quoted(itin: DatasetItinerary, what: str, value: str | None) -> list[str]:
    if value is None or value in itin.clean_text:
        return []
    return [f"{what} {value!r} isn't copied from the cleaned text"]


def _violation_errors(itin: DatasetItinerary) -> list[str]:
    errors = []
    seen = set()
    for i, v in enumerate(itin.violations or [], 1):
        where = f"violation {i} ({v.check}, day {v.day})"
        target = (v.check, v.day, v.place, v.from_place)
        if target in seen:
            errors.append(f"{where}: labelled twice")
        seen.add(target)
        errors += [f"{where}: {e}" for e in _quoted(itin, "place", v.place)]
        errors += [f"{where}: {e}" for e in _quoted(itin, "from_place", v.from_place)]
        errors += [f"{where}: {e}" for e in _quoted(itin, "time", v.time)]
    for i, u in enumerate(itin.unknowns, 1):
        errors += [f"unknown {i} ({u.check}): {e}" for e in _quoted(itin, "place", u.place)]
    return errors


def _stop_errors(itin: DatasetItinerary, hotels: dict[str, Hotel]) -> list[str]:
    """Gold stops and legs: dev only, for parser debugging."""
    assert itin.stops is not None
    if itin.split != "dev":
        return ["stops and legs are dev only (parser debugging)"]
    errors = []
    by_id: dict[str, AnnotatedStop] = {}
    for s in itin.stops:
        if s.id in by_id:
            errors.append(f"stop {s.id}: duplicate id")
        by_id[s.id] = s
        errors += [f"stop {s.id}: {e}" for e in _quoted(itin, "place_name", s.place_name)]
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
    seen = set()
    for leg in itin.legs or []:
        key = (leg.from_stop, leg.to_stop)
        if key not in pairs:
            errors.append(f"leg {leg.from_stop} → {leg.to_stop}: not consecutive stops on a day")
            continue
        if key in seen:
            errors.append(f"leg {leg.from_stop} → {leg.to_stop}: recorded twice")
        seen.add(key)
        a, b = pairs[key]
        if not (a.located and b.located):
            errors.append(f"leg {a.id} → {b.id}: one end has no location (located: false)")
    return errors


def gold_violations(itin: DatasetItinerary, hotels: dict[str, Hotel]) -> list[GoldViolation]:
    """The file's labels plus one hotel_red_flags label for a stay at a flagged hotel."""
    if not itin.annotated:
        return []
    assert itin.violations is not None
    out = [
        GoldViolation(
            itinerary_id=itin.id,
            check=v.check,
            severity=v.severity,
            day=v.day,
            place=v.place,
            from_place=v.from_place,
            confidence=v.confidence,
            derived=False,
        )
        for v in itin.violations
    ]
    hotel = hotels.get(itin.hotel or "")
    if hotel is not None and hotel.red_flags:
        out.append(
            GoldViolation(
                itinerary_id=itin.id,
                check="hotel_red_flags",
                severity="warning",
                day=None,
                place=hotel.name,
                confidence="verified",
                derived=True,
            )
        )
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
