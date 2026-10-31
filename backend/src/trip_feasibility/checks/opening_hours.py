"""Opening hours check (OSM data via Nominatim): flags stops outside a venue's opening
hours, or at a venue that no longer exists.

- blocker: closed for the whole visit (or all day when the stop has no time), or OSM marks
  the place as disused/demolished
- warning: open for only part of the visit, or the OSM element no longer exists
Stops with no usable opening hours are reported as unknown, not as violations, so missing
OSM data never counts against the venue. Hotels and transfers aren't checked.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from opening_hours import OpeningHours, ParserError, State

from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.checks.models import CheckResult, UnknownStop, Violation
from trip_feasibility.itinerary import Stop
from trip_feasibility.sources.nominatim import fetch_osm_tags, tags_key

CHECK_NAME = "opening_hours"
CHECKED_TYPES = {"venue", "meal"}
_PRIMARY_KEYS = {"amenity", "shop", "tourism", "leisure", "craft", "office", "historic"}
_LIFECYCLE_PREFIXES = ("disused:", "abandoned:", "demolished:", "razed:", "removed:", "was:")


def _is_defunct(tags: dict[str, Any]) -> bool:
    has_lifecycle = any(k.startswith(_LIFECYCLE_PREFIXES) for k in tags)
    return has_lifecycle and not (_PRIMARY_KEYS & tags.keys())


def _visit_window(stop: Stop) -> tuple[datetime, datetime] | None:
    if stop.start_time is None:
        return None
    start = datetime.combine(stop.date, stop.start_time)
    if stop.end_time is None:
        return start, start + timedelta(minutes=1)
    end = datetime.combine(stop.date, stop.end_time)
    if end <= start:  # runs past midnight
        end += timedelta(days=1)
    return start, end


def _open_windows(oh: OpeningHours, day: date) -> list[str]:
    start = datetime.combine(day, time())
    return [
        f"{a:%H:%M}-{b:%H:%M}" if b.date() == day else f"{a:%H:%M}-{b:%H:%M} (+1 day)"
        for a, b, state, _ in oh.intervals(start, start + timedelta(days=1))
        if state == State.OPEN
    ]


def _evaluate(stop: Stop, oh: OpeningHours) -> tuple[str, str] | None:
    """(severity, message) when the stop clashes with the hours; severity "unknown" when
    OSM marks the hours as unknown for that time; None when the visit fits."""
    window = _visit_window(stop)
    whole_day = window is None
    if window is None:
        day_start = datetime.combine(stop.date, time())
        window = (day_start, day_start + timedelta(days=1))
    when = f"{stop.date:%a %d %b %Y}"
    states = {state for _, _, state, _ in oh.intervals(*window)}
    if states == {State.CLOSED}:
        if whole_day:
            return "blocker", f"{stop.place_name} is closed all day on {when}."
        return "blocker", (
            f"{stop.place_name} is closed during the visit "
            f"({window[0]:%H:%M}-{window[1]:%H:%M} on {when})."
        )
    if not whole_day and State.CLOSED in states and State.OPEN in states:
        return "warning", (
            f"{stop.place_name} is open for only part of the visit "
            f"({window[0]:%H:%M}-{window[1]:%H:%M} on {when})."
        )
    if State.UNKNOWN in states and State.OPEN not in states:
        return "unknown", "OSM marks the opening hours as unknown for this time"
    return None


async def check_opening_hours(
    deps: CheckDeps, stops: list[Stop], country_code: str | None = None
) -> CheckResult:
    """country_code (ISO 3166-1, e.g. "FR") lets "PH off" rules use the local holiday calendar."""
    candidates = [s for s in stops if s.type in CHECKED_TYPES]
    unknown = [
        UnknownStop(stop_id=s.id, reason="not resolved to an OpenStreetMap place")
        for s in candidates
        if s.osm is None
    ]
    located = [s for s in candidates if s.osm is not None]
    try:
        tags_by_key = await fetch_osm_tags(deps, [s.osm for s in located]) if located else {}
    except Unavailable as e:
        return CheckResult.unavailable(CHECK_NAME, str(e))

    violations = []
    for stop in located:
        assert stop.osm is not None
        tags = tags_by_key.get(tags_key(stop.osm))
        base_evidence = {"osm_type": stop.osm.type, "osm_id": stop.osm.id}
        if tags is None:
            violations.append(
                Violation(
                    check_name=CHECK_NAME,
                    severity="warning",
                    stop_ids=[stop.id],
                    message=(
                        f"{stop.place_name} was not found in OpenStreetMap; it may have closed."
                    ),
                    evidence={**base_evidence, "found": False},
                    source=stop.osm.url,
                )
            )
            continue
        if _is_defunct(tags):
            violations.append(
                Violation(
                    check_name=CHECK_NAME,
                    severity="blocker",
                    stop_ids=[stop.id],
                    message=f"OpenStreetMap marks {stop.place_name} as closed down.",
                    evidence={**base_evidence, "tags": tags},
                    source=stop.osm.url,
                )
            )
            continue
        raw = tags.get("opening_hours")
        if not raw:
            unknown.append(UnknownStop(stop_id=stop.id, reason="no opening_hours in OSM"))
            continue
        # coordinates place sunrise/sunset ("sunset-00:30"); without them the library
        # assumes a fixed 07:00/19:00
        coords = (stop.lat, stop.lon) if stop.lat is not None and stop.lon is not None else None
        try:
            oh = OpeningHours(
                raw, country=country_code.upper() if country_code else None, coords=coords
            )
        except (ParserError, SyntaxError, ValueError):
            unknown.append(
                UnknownStop(stop_id=stop.id, reason=f"unparseable opening_hours: {raw!r}")
            )
            continue
        clash = _evaluate(stop, oh)
        if clash is None:
            continue
        severity, message = clash
        if severity == "unknown":
            unknown.append(UnknownStop(stop_id=stop.id, reason=message))
            continue
        violations.append(
            Violation(
                check_name=CHECK_NAME,
                severity=severity,
                stop_ids=[stop.id],
                message=message,
                evidence={
                    **base_evidence,
                    "opening_hours": raw,
                    "open_that_day": _open_windows(oh, stop.date),
                    "visit_start": stop.start_time.isoformat() if stop.start_time else None,
                    "visit_end": stop.end_time.isoformat() if stop.end_time else None,
                },
                source=stop.osm.url,
            )
        )
    unknown_ids = {u.stop_id for u in unknown}
    return CheckResult(
        check_name=CHECK_NAME,
        status="ok",
        violations=violations,
        unknown=unknown,
        checked_stop_ids=[s.id for s in candidates if s.id not in unknown_ids],
    )
