"""Travel time check: flags consecutive stops whose gap is shorter than the travel time.

- blocker: the gap is shorter than the travel time itself (or the stops overlap). For
  estimated times (transit, taxi) only when the gap is more than ESTIMATE_MARGIN short,
  since the estimate itself could be that far off
- warning: it fits, but with less slack than the buffer (strictness: 0, 10 or 20 minutes),
  or an estimated time is only slightly over the gap
Travel time is the fastest of the traveller's own transport modes (see checks/legs.py for
how each is estimated). Legs with a missing time or an unlocated stop are reported unknown.
"""

from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.checks.legs import Leg, plan_legs
from trip_feasibility.checks.models import CheckResult, Severity, UnknownStop, Violation
from trip_feasibility.itinerary import Stop, TravelPreferences
from trip_feasibility.sources.routing import SOURCE_URL

CHECK_NAME = "travel_time"
DEFAULT_BUFFER_MIN = 10
ESTIMATE_MARGIN = 0.25
_HOW = {
    "walk": "on foot",
    "bike": "by bike",
    "car": "by taxi (estimated)",
    "transit": "by public transport (estimated)",
}


def _evidence(leg: Leg, buffer_min: int) -> dict:
    return {
        "gap_min": round(leg.gap_min, 1) if leg.gap_min is not None else None,
        "travel_min": round(leg.travel_min, 1) if leg.travel_min is not None else None,
        "buffer_min": buffer_min,
        "mode": leg.mode,
        "options": {
            m: {"minutes": round(o.minutes, 1), "km": round(o.km, 2), "estimated": o.estimated}
            for m, o in leg.options.items()
        },
    }


def _assess(leg: Leg, names: dict[str, str], buffer_min: int) -> tuple[Severity, str] | None:
    assert leg.gap_min is not None and leg.travel_min is not None
    a, b = names[leg.from_stop], names[leg.to_stop]
    gap, need, how = round(leg.gap_min), round(leg.travel_min), _HOW[leg.mode]
    if leg.gap_min < 0:
        return "blocker", f"{b} starts {-gap} min before {a} ends."
    if leg.travel_min == 0:  # same place: no travel, so no buffer needed
        return None
    estimated = leg.options[leg.mode].estimated
    if estimated and leg.travel_min * (1 - ESTIMATE_MARGIN) <= leg.gap_min < leg.travel_min:
        return "warning", (
            f"{a} → {b} may not be enough time: about {need} min {how}, with {gap} min available."
        )
    if leg.gap_min < leg.travel_min:
        return "blocker", (f"{a} → {b} needs about {need} min {how}, but there are only {gap} min.")
    if leg.gap_min < leg.travel_min + buffer_min:
        return "warning", (
            f"{a} → {b} is tight: about {need} min {how} with {gap} min available, "
            f"less than the {buffer_min} min buffer."
        )
    return None


async def check_travel_time(
    deps: CheckDeps,
    stops: list[Stop],
    prefs: TravelPreferences | None = None,
    buffer_min: int = DEFAULT_BUFFER_MIN,
) -> CheckResult:
    prefs = prefs or TravelPreferences()
    try:
        plan = await plan_legs(deps, stops, prefs)
    except Unavailable as e:
        return CheckResult.unavailable(CHECK_NAME, str(e))

    names = {s.id: s.place_name for s in stops}
    violations, unknown, checked = [], {}, set()
    for stop_id in plan.unlocated_stop_ids:
        unknown[stop_id] = "not located, so travel to and from it can't be checked"
    for leg in plan.legs:
        if leg.gap_min is None:
            unknown.setdefault(leg.to_stop, f"no time gap after {names[leg.from_stop]} to check")
            continue
        if leg.mode is None:
            unknown.setdefault(leg.to_stop, "no route found with your transport modes")
            continue
        checked |= {leg.from_stop, leg.to_stop}
        if (assessment := _assess(leg, names, buffer_min)) is None:
            continue
        severity, message = assessment
        violations.append(
            Violation(
                check_name=CHECK_NAME,
                severity=severity,
                stop_ids=[leg.from_stop, leg.to_stop],
                message=message,
                evidence=_evidence(leg, buffer_min),
                source=SOURCE_URL,
            )
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status="ok",
        violations=violations,
        unknown=[UnknownStop(stop_id=k, reason=v) for k, v in unknown.items()],
        checked_stop_ids=[s.id for s in stops if s.id in checked],
    )
