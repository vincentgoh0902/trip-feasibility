"""Walking, cycling and driving distances/durations between points, from the FOSSGIS OSRM
servers (routing.openstreetmap.de).

Uses OSRM's table service: one request per profile returns every pair at once. Results are
cached per pair, so re-checking a reordered itinerary only asks for the new pairs.
Policy: at most 1 request per second, identifying User-Agent, no heavy use, and the UI must
show OSM attribution. https://routing.openstreetmap.de/about.html
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

import httpx

from trip_feasibility.checks.deps import CheckDeps, Unavailable

Profile = Literal["foot", "bike", "car"]
Point = tuple[float, float]  # (lat, lon)

# OSRM servers cap table size; stay well below the usual limit of 100 points.
MAX_TABLE_POINTS = 50
_BACK_OFF_STATUSES = {403, 429, 503}
SOURCE_URL = "https://routing.openstreetmap.de"


@dataclass(frozen=True)
class Route:
    distance_m: float
    duration_s: float


def _coord(p: Point) -> str:
    return f"{p[1]:.5f},{p[0]:.5f}"  # OSRM wants lon,lat


def _key(profile: Profile, a: Point, b: Point) -> str:
    return f"route:{profile}:{_coord(a)};{_coord(b)}"


def _chunks(pairs: list[tuple[Point, Point]]) -> list[list[tuple[Point, Point]]]:
    """Split pairs so each table request has at most MAX_TABLE_POINTS distinct points."""
    chunks: list[list[tuple[Point, Point]]] = []
    current: list[tuple[Point, Point]] = []
    points: set[Point] = set()
    for a, b in pairs:
        if current and len(points | {a, b}) > MAX_TABLE_POINTS:
            chunks.append(current)
            current, points = [], set()
        current.append((a, b))
        points |= {a, b}
    if current:
        chunks.append(current)
    return chunks


async def _table(deps: CheckDeps, profile: Profile, points: list[Point]) -> dict[str, Any]:
    await deps.routing_limiter.wait()
    coords = ";".join(_coord(p) for p in points)
    url = f"{deps.settings.routing_base_url}/routed-{profile}/table/v1/driving/{coords}"
    try:
        r = await deps.http.get(
            url,
            params={"annotations": "duration,distance"},
            timeout=deps.settings.routing_timeout_s,
        )
    except httpx.HTTPError as e:
        raise Unavailable(f"Routing request failed: {e!r}") from e
    if r.status_code in _BACK_OFF_STATUSES:
        deps.routing_limiter.back_off()
        raise Unavailable(f"Routing server returned HTTP {r.status_code}; backing off")
    try:
        body = r.json()
    except ValueError as e:
        raise Unavailable(f"Routing server returned HTTP {r.status_code}, not JSON") from e
    if r.status_code != 200 or body.get("code") != "Ok":
        raise Unavailable(f"Routing failed: HTTP {r.status_code} {body.get('code')}")
    return body


async def route_pairs(
    deps: CheckDeps, profile: Profile, pairs: list[tuple[Point, Point]]
) -> dict[tuple[Point, Point], Route | None]:
    """Route for each (from, to) pair; None when OSRM finds no route between them."""
    wanted = {_key(profile, a, b): (a, b) for a, b in dict.fromkeys(pairs)}
    found: dict[str, Any] = await deps.cache.get_many(list(wanted))
    missing = [pair for k, pair in wanted.items() if k not in found]
    for chunk in _chunks(missing):
        points = list(dict.fromkeys(p for pair in chunk for p in pair))
        index = {p: i for i, p in enumerate(points)}
        body = await _table(deps, profile, points)
        fetched = {}
        for a, b in chunk:
            duration = body["durations"][index[a]][index[b]]
            distance = body["distances"][index[a]][index[b]]
            fetched[_key(profile, a, b)] = (
                {"distance_m": distance, "duration_s": duration}
                if duration is not None and distance is not None
                else {"no_route": True}
            )
        await deps.cache.put_many(
            "osrm", fetched, timedelta(days=deps.settings.cache_ttl_routes_days)
        )
        found.update(fetched)
    return {
        pair: None if found[k].get("no_route") else Route(**found[k]) for k, pair in wanted.items()
    }
