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


async def main() -> None:
    args = {"stops": [s.model_dump(mode="json") for s in STOPS], "country_code": "FR"}
    async with Client(server) as client:
        for tool in ("public_holidays", "opening_hours"):
            out = (await client.call_tool(tool, args)).structured_content
            print(f"\n== {tool}: {out['status']} {out.get('error') or ''}")
            for v in out["violations"]:
                print(f"  [{v['severity']}] {v['stop_ids']}: {v['message']}")
            for u in out["unknown"]:
                print(f"  [unknown] {u['stop_id']}: {u['reason']}")


if __name__ == "__main__":
    asyncio.run(main())
