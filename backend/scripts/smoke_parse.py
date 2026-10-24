"""Run parse steps 3-4 (normalise, geocode) once against the real Nominatim.

    uv run python scripts/smoke_parse.py

Uses the made-up example itinerary (docs/annotation/example.yaml), never the dataset.
Cached in DATABASE_URL's api_cache, so a second run makes no requests.
"""

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.validate import load_yaml
from trip_feasibility.parse.geocode import geocode_stops, locate_city
from trip_feasibility.parse.normalise import normalise
from trip_feasibility.parse.schema import ExtractedStop

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "annotation" / "example.yaml"
CITIES = ["Paris", "London", "Barcelona", "Vienna"]


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async with make_http_client(settings) as http:
        deps = make_check_deps(http, async_sessionmaker(engine), settings)
        for name in CITIES:
            city = await locate_city(deps, name)
            if city is None:
                print(f"{name}: not found")
                continue
            print(f"{name}: {city.place.osm.url} {city.country_code} bbox={city.place.bbox}")

        raw = load_yaml(EXAMPLE)
        extracted = [ExtractedStop(**{k: s[k] for k in s if k in ExtractedStop.model_fields})
                     for s in raw["stops"]]  # fmt: skip
        extracted.append(ExtractedStop(day=2, place_name="Château de Versailles", type="venue"))
        paris = await locate_city(deps, "Paris")
        assert paris is not None
        stops = normalise(extracted, raw["trip_start"])
        _, geocodes = await geocode_stops(deps, stops, paris)
        print()
        for s in stops:
            g = geocodes[s.id]
            if g.place is None:
                print(f"{s.id} {s.place_name!r}: UNRESOLVED ({g.reason})")
            else:
                where = " (outside city)" if g.outside_city else ""
                print(f"{s.id} {s.place_name!r} -> {g.place.name} {g.place.osm.url}{where}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
