"""Run the check tools once against the real data sources (not part of the test suite).

    uv run python scripts/smoke_checks.py

Uses DATABASE_URL for the response cache, so a second run makes no external calls.
"""

import asyncio
from datetime import date, time

from mcp import Client

from trip_feasibility.itinerary import OsmRef, Stop
from trip_feasibility.mcp_server import server

STOPS = [
    Stop(
        id="eiffel-bastille",
        day=1,
        date=date(2026, 7, 14),
        start_time=time(10),
        end_time=time(12),
        place_name="Tour Eiffel",
        type="venue",
        osm=OsmRef(type="way", id=5013364),
    ),
    Stop(
        id="eiffel-late",
        day=2,
        date=date(2026, 10, 27),
        start_time=time(23, 30),
        end_time=time(1, 0),
        place_name="Tour Eiffel",
        type="venue",
        osm=OsmRef(type="way", id=5013364),
    ),
    # An OSM node with no tags: Nominatim doesn't index it, so it reports "not found".
    # Real stops come from Nominatim geocoding and are always indexed.
    Stop(
        id="unindexed",
        day=2,
        date=date(2026, 10, 27),
        start_time=time(14),
        end_time=time(15),
        place_name="Unindexed node",
        type="venue",
        osm=OsmRef(type="node", id=3227252034),
    ),
]


def _paris(stop_id: str, start: str, end: str, name: str, lat: float, lon: float) -> Stop:
    return Stop(
        id=stop_id,
        day=1,
        date=date(2026, 10, 27),
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        place_name=name,
        type="venue",
        lat=lat,
        lon=lon,
    )


# A packed Paris day with deliberately tight transfers, for travel time and daily load.
PARIS_DAY = [
    _paris("louvre", "09:00", "12:00", "Louvre", 48.8606, 2.3376),
    _paris("orsay", "12:05", "14:00", "Musée d'Orsay", 48.8600, 2.3266),
    _paris("eiffel", "14:20", "16:00", "Tour Eiffel", 48.8584, 2.2945),
    _paris("arc", "16:10", "17:00", "Arc de Triomphe", 48.8738, 2.2950),
    _paris("sacre", "17:30", "18:30", "Sacré-Cœur", 48.8867, 2.3431),
    _paris("notredame", "19:00", "20:00", "Notre-Dame", 48.8530, 2.3499),
]


def _print(tool: str, out: dict) -> None:
    print(f"\n== {tool}: {out['status']} {out.get('error') or ''}")
    for v in out["violations"]:
        print(f"  [{v['severity']}] {v['stop_ids']}: {v['message']}")
    for u in out["unknown"]:
        print(f"  [unknown] {u['stop_id']}: {u['reason']}")
    for n in out.get("notes", []):
        print(f"  [note] {n}")


async def main() -> None:
    args = {"stops": [s.model_dump(mode="json") for s in STOPS], "country_code": "FR"}
    day = {
        "stops": [s.model_dump(mode="json") for s in PARIS_DAY],
        "preferences": {
            "pace": "moderate",
            "max_walk_km": 8,
            "transport_modes": ["walk", "transit"],
        },
    }
    async with Client(server) as client:
        for tool in ("public_holidays", "opening_hours"):
            _print(tool, (await client.call_tool(tool, args)).structured_content)
        for tool in ("travel_time", "daily_load"):
            _print(tool, (await client.call_tool(tool, day)).structured_content)


if __name__ == "__main__":
    asyncio.run(main())
