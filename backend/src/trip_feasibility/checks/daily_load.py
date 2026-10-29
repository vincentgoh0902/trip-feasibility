"""Daily load check: flags days that are too full for the traveller's pace or walking limit.

Per day: number of venue stops, length of the day (first start to last end), and walking
distance (from checks/legs.py). One violation per overloaded day, listing every limit it
exceeds.
- warning: more venues or a longer day than the pace allows, or walking over the limit
- blocker: walking more than WALK_BLOCKER_FACTOR times the limit
The pace limits are starting defaults, to be tuned against the annotated dev set.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.checks.legs import plan_legs
from trip_feasibility.checks.models import CheckResult, Severity, Violation
from trip_feasibility.itinerary import Pace, Stop, TravelPreferences

CHECK_NAME = "daily_load"
# pace -> (max venue stops per day, max hours from first start to last end)
PACE_LIMITS: dict[Pace, tuple[int, float]] = {
    "relaxed": (3, 8.0),
    "moderate": (5, 10.0),
    "packed": (7, 13.0),
}
WALK_BLOCKER_FACTOR = 1.5
SOURCE = "Your travel preferences (pace and walking limit)"


def _day_hours(stops: list[Stop]) -> float | None:
    starts = [datetime.combine(s.date, s.start_time) for s in stops if s.start_time]
    ends = []
    for s in stops:
        if s.end_time is None:
            continue
        end = datetime.combine(s.date, s.end_time)
        if s.start_time is not None and s.end_time < s.start_time:
            end += timedelta(days=1)
        ends.append(end)
    times = starts + ends
    if len(times) < 2:
        return None
    return (max(times) - min(starts or times)).total_seconds() / 3600


async def check_daily_load(
    deps: CheckDeps, stops: list[Stop], prefs: TravelPreferences | None = None
) -> CheckResult:
    prefs = prefs or TravelPreferences()
    max_venues, max_hours = PACE_LIMITS[prefs.pace]
    notes = []
    walk_by_day: dict[date, float] | None = defaultdict(float)
    try:
        plan = await plan_legs(deps, stops, prefs)
        for leg in plan.legs:
            walk_by_day[leg.date] += leg.walk_km
        if plan.unlocated_stop_ids:
            notes.append("Walking distance leaves out legs to or from stops that weren't located.")
        if plan.area_stop_ids:
            notes.append("Walking distance leaves out legs to or from neighbourhoods.")
    except Unavailable as e:
        walk_by_day = None
        notes.append(f"Walking distance not checked: {e}")

    by_date: dict[date, list[Stop]] = defaultdict(list)
    for s in stops:
        by_date[s.date].append(s)

    violations = []
    for day, day_stops in sorted(by_date.items()):
        venues = sum(s.type == "venue" for s in day_stops)
        hours = _day_hours(day_stops)
        walk_km = walk_by_day[day] if walk_by_day is not None else None
        reasons: list[str] = []
        severity: Severity = "warning"
        if venues > max_venues:
            reasons.append(f"{venues} venues (a {prefs.pace} pace allows {max_venues})")
        if hours is not None and hours > max_hours:
            reasons.append(f"a {hours:.1f} h day (a {prefs.pace} pace allows {max_hours:g} h)")
        if walk_km is not None and walk_km > prefs.max_walk_km:
            reasons.append(
                f"about {walk_km:.1f} km of walking (your limit is {prefs.max_walk_km:g})"
            )
            if walk_km > prefs.max_walk_km * WALK_BLOCKER_FACTOR:
                severity = "blocker"
        if not reasons:
            continue
        violations.append(
            Violation(
                check_name=CHECK_NAME,
                severity=severity,
                stop_ids=[s.id for s in day_stops],
                message=f"{day:%a %d %b} is overloaded: " + "; ".join(reasons) + ".",
                evidence={
                    "date": day.isoformat(),
                    "venues": venues,
                    "day_hours": round(hours, 1) if hours is not None else None,
                    "walk_km": round(walk_km, 1) if walk_km is not None else None,
                    "pace": prefs.pace,
                    "limits": {
                        "venues": max_venues,
                        "day_hours": max_hours,
                        "walk_km": prefs.max_walk_km,
                    },
                },
                source=SOURCE,
            )
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status="ok",
        violations=violations,
        checked_stop_ids=[s.id for s in stops],
        notes=notes,
    )
