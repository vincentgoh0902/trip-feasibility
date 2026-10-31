"""Travel time check: flags consecutive stops whose gap is shorter than the travel time.

- blocker: the stops overlap, or the gap is more than BLOCKER_SHORTFALL_MIN shorter than
  the travel time. For estimated times (transit, taxi) the travel time counted is
  ESTIMATE_MARGIN lower, since the estimate itself could be that far off
- warning: short by more than SHORTFALL_TOLERANCE_MIN. Up to that is no problem: the
  traveller leaves a few minutes early (chatbot itineraries put slots back to back)
- strictness (buffer_min: 0, 10 or 20) is the slack wanted on top: 0 accepts up to 10 min
  short, 10 flags any shortfall, 20 wants 10 min to spare. The default, 0, is the rule
  docs/ANNOTATION.md uses for gold labels
Overlaps are found from the times alone, so they're reported even when a stop isn't located
or is an area ("Lunch" right after a visit that runs into it). Travel time is the fastest of
the traveller's own transport modes (see checks/legs.py for
how each is estimated). Legs with a missing time, an unlocated stop, or an area stop (a
neighbourhood) the traveller is already beside (checks/legs.py) are reported unknown.
"""

from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.checks.legs import Leg, consecutive_pairs, gap_minutes, plan_legs
from trip_feasibility.checks.models import CheckResult, Severity, UnknownStop, Violation
from trip_feasibility.itinerary import Stop, TravelPreferences
from trip_feasibility.sources.routing import SOURCE_URL

CHECK_NAME = "travel_time"
DEFAULT_BUFFER_MIN = 0
ESTIMATE_MARGIN = 0.25
# docs/ANNOTATION.md uses the same rule for gold labels (decision 78)
SHORTFALL_TOLERANCE_MIN = 10
BLOCKER_SHORTFALL_MIN = 30
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


def _overlap(a: Stop, b: Stop) -> Violation | None:
    gap = gap_minutes(a, b)
    if gap is None or gap >= 0:
        return None
    return Violation(
        check_name=CHECK_NAME,
        severity="blocker",
        stop_ids=[a.id, b.id],
        message=f"{b.place_name} starts {-round(gap)} min before {a.place_name} ends.",
        evidence={"gap_min": round(gap, 1)},
        source="Your itinerary's times",
    )


def _assess(leg: Leg, names: dict[str, str], buffer_min: int) -> tuple[Severity, str] | None:
    """Legs that don't overlap (overlaps are reported from the times alone)."""
    assert leg.gap_min is not None and leg.travel_min is not None
    a, b = names[leg.from_stop], names[leg.to_stop]
    gap, need, how = round(leg.gap_min), round(leg.travel_min), _HOW[leg.mode]
    if leg.travel_min == 0:  # same place: no travel, so no buffer needed
        return None
    estimated = leg.options[leg.mode].estimated
    least = leg.travel_min * (1 - ESTIMATE_MARGIN) if estimated else leg.travel_min
    if leg.gap_min < least - BLOCKER_SHORTFALL_MIN:
        return "blocker", (f"{a} → {b} needs about {need} min {how}, but there are only {gap} min.")
    if leg.gap_min >= leg.travel_min + buffer_min - SHORTFALL_TOLERANCE_MIN:
        return None
    if leg.gap_min < leg.travel_min:
        return "warning", (
            f"{a} → {b} is {need - gap} min short: about {need} min {how}, "
            f"with {gap} min available."
        )
    return "warning", (
        f"{a} → {b} is tight: about {need} min {how} with {gap} min available, "
        f"less than the {buffer_min - SHORTFALL_TOLERANCE_MIN} min to spare you asked for."
    )


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
    overlapping = set()
    for a, b in consecutive_pairs(stops):
        if (v := _overlap(a, b)) is not None:
            violations.append(v)
            overlapping.add((a.id, b.id))
            checked |= {a.id, b.id}
    for stop_id in plan.unlocated_stop_ids:
        unknown[stop_id] = "not located, so travel to and from it can't be checked"
    for stop_id in plan.area_stop_ids:
        unknown[stop_id] = (
            "a neighbourhood right next to the stop before or after it, so that walk isn't timed"
        )
    for leg in plan.legs:
        if (leg.from_stop, leg.to_stop) in overlapping:
            continue
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
