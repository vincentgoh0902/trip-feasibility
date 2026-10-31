"""Legs between consecutive stops on the same day, with the travel options for each.

Shared by the travel-time and daily-load checks (the second call is served from the cache).
Stops are taken in itinerary order, not re-sorted by time: an itinerary whose times run
backwards is itself a problem for the travel-time check to report. A leg to or from an area
stop (a neighbourhood) under AREA_BESIDE_KM on foot is left out: you're already beside it,
and routing to its centre point would time a journey that mostly doesn't exist. Farther
than that it's a real journey, timed to the centre (a little over the nearest edge that
docs/ANNOTATION.md times to). Decision 80; the 2 km was set on the dev set.

Estimates, all documented in docs/ARCHITECTURE.md and tuned from the dev set:
- walk: OSRM foot profile; only offered for legs up to WALK_LEG_MAX_KM, unless walking is
  the traveller's only mode.
- bike: OSRM bike profile.
- car (taxi): OSRM car time is free-flow (no traffic), so it's scaled for city traffic and
  given a pickup wait.
- transit: no free routing service covers public transport, so it's estimated from the
  free-flow car time plus access and waiting time, and marked estimated=True.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.itinerary import Stop, TransportMode, TravelPreferences
from trip_feasibility.sources.routing import Point, Profile, Route, route_pairs

WALK_LEG_MAX_KM = 3.0
CAR_TRAFFIC_FACTOR = 1.3
CAR_PICKUP_MIN = 5.0
TRANSIT_FACTOR = 1.5
TRANSIT_ACCESS_MIN = 10.0
TRANSIT_ACCESS_WALK_KM = 0.4  # walking to and from stations, counted toward daily walking
AREA_BESIDE_KM = 2.0


class ModeOption(BaseModel):
    minutes: float
    km: float
    estimated: bool = False


class Leg(BaseModel):
    from_stop: str
    to_stop: str
    date: date
    gap_min: float | None  # None when a time is missing
    options: dict[TransportMode, ModeOption]
    mode: TransportMode | None  # fastest option the traveller uses; None if none works
    travel_min: float | None
    walk_km: float


class LegPlan(BaseModel):
    legs: list[Leg]
    unlocated_stop_ids: list[str]
    area_stop_ids: list[str] = []  # areas with a leg left out (one beside them)


def _point(stop: Stop) -> Point | None:
    return (stop.lat, stop.lon) if stop.lat is not None and stop.lon is not None else None


def gap_minutes(a: Stop, b: Stop) -> float | None:
    if a.end_time is None or b.start_time is None:
        return None
    end = datetime.combine(a.date, a.end_time)
    if a.start_time is not None and a.end_time < a.start_time:
        end += timedelta(days=1)  # the first stop runs past midnight
    return (datetime.combine(b.date, b.start_time) - end).total_seconds() / 60


def _profiles(modes: list[TransportMode]) -> list[Profile]:
    profiles: list[Profile] = ["foot"]  # always: walking distance feeds the daily-load check
    if "bike" in modes:
        profiles.append("bike")
    if "car" in modes or "transit" in modes:
        profiles.append("car")
    return profiles


def _options(
    routes: dict[Profile, Route | None], modes: list[TransportMode]
) -> dict[TransportMode, ModeOption]:
    options: dict[TransportMode, ModeOption] = {}
    foot, bike, car = routes.get("foot"), routes.get("bike"), routes.get("car")
    if "walk" in modes and foot:
        km = foot.distance_m / 1000
        if km <= WALK_LEG_MAX_KM or set(modes) == {"walk"}:
            options["walk"] = ModeOption(minutes=foot.duration_s / 60, km=km)
    if "bike" in modes and bike:
        options["bike"] = ModeOption(minutes=bike.duration_s / 60, km=bike.distance_m / 1000)
    if "car" in modes and car:
        options["car"] = ModeOption(
            minutes=car.duration_s / 60 * CAR_TRAFFIC_FACTOR + CAR_PICKUP_MIN,
            km=car.distance_m / 1000,
            estimated=True,
        )
    if "transit" in modes and car:
        options["transit"] = ModeOption(
            minutes=car.duration_s / 60 * TRANSIT_FACTOR + TRANSIT_ACCESS_MIN,
            km=car.distance_m / 1000,
            estimated=True,
        )
    return options


def _walk_km(mode: TransportMode | None, options: dict[TransportMode, ModeOption]) -> float:
    if mode == "walk":
        return options["walk"].km
    if mode == "transit":
        return TRANSIT_ACCESS_WALK_KM
    return 0.0


def consecutive_pairs(stops: list[Stop]) -> list[tuple[Stop, Stop]]:
    """Each stop and the next one on the same day, in itinerary order."""
    by_date: dict[date, list[Stop]] = defaultdict(list)
    for s in stops:
        by_date[s.date].append(s)
    return [
        (a, b)
        for day in sorted(by_date)
        for a, b in zip(by_date[day], by_date[day][1:], strict=False)
    ]


async def plan_legs(deps: CheckDeps, stops: list[Stop], prefs: TravelPreferences) -> LegPlan:
    """Raises Unavailable if the routing service fails."""
    pairs = consecutive_pairs(stops)
    located = [(a, b) for a, b in pairs if _point(a) and _point(b) and _point(a) != _point(b)]
    points = [(_point(a), _point(b)) for a, b in located]

    routes: dict[Profile, dict] = {}
    for profile in _profiles(prefs.transport_modes):
        routes[profile] = await route_pairs(deps, profile, points) if points else {}

    legs, beside = [], set()
    for a, b in pairs:
        pa, pb = _point(a), _point(b)
        if pa is None or pb is None:
            continue
        if a.area or b.area:
            foot = routes["foot"].get((pa, pb)) if pa != pb else None
            if foot is None or foot.distance_m < AREA_BESIDE_KM * 1000:
                beside |= {s.id for s in (a, b) if s.area}
                continue
        if pa == pb:  # same place (e.g. lunch at the museum): no travel
            options = {m: ModeOption(minutes=0, km=0) for m in prefs.transport_modes}
        else:
            options = _options({p: routes[p][(pa, pb)] for p in routes}, prefs.transport_modes)
        mode = min(options, key=lambda m: options[m].minutes) if options else None
        legs.append(
            Leg(
                from_stop=a.id,
                to_stop=b.id,
                date=a.date,
                gap_min=gap_minutes(a, b),
                options=options,
                mode=mode,
                travel_min=options[mode].minutes if mode else None,
                walk_km=_walk_km(mode, options),
            )
        )
    return LegPlan(
        legs=legs,
        unlocated_stop_ids=[s.id for s in stops if _point(s) is None],
        area_stop_ids=[s.id for s in stops if s.id in beside],
    )
