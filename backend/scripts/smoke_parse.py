"""Run the parse pipeline once against the real services.

    uv run python scripts/smoke_parse.py

Steps 3-4 (normalise, geocode via Nominatim) always run, cached in DATABASE_URL's api_cache.
Step 1 (Claude extraction) runs only when ANTHROPIC_API_KEY is set, costs a fraction of a
cent, and is compared field by field with the example's gold stops. Uses the made-up example
itinerary (docs/annotation/example.yaml), never the dataset.
"""

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.validate import load_yaml
from trip_feasibility.parse.extract import extract_stops, make_client
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
        if settings.anthropic_api_key is None:
            print("\nANTHROPIC_API_KEY not set: skipping extraction (step 1).")
        else:
            await smoke_extraction(settings, raw, extracted[: len(raw["stops"])])
    await engine.dispose()


async def smoke_extraction(settings, raw: dict, gold: list[ExtractedStop]) -> None:
    result = await extract_stops(
        make_client(settings),
        raw["text"],
        city=raw["city"].title(),
        trip_start=raw["trip_start"],
        model=settings.extraction_model,
        effort=settings.extraction_effort,
    )
    got = result.extraction.stops
    print(
        f"\nExtraction: {result.model}, {result.prompt_version}, {result.attempts} attempt(s), "
        f"{result.input_tokens} in / {result.output_tokens} out tokens"
    )
    fields = list(ExtractedStop.model_fields)
    matched = sum(
        getattr(g, f) == getattr(x, f) for g, x in zip(gold, got, strict=False) for f in fields
    )
    print(f"{len(got)} stops (gold {len(gold)}); {matched}/{len(gold) * len(fields)} fields match")
    for i, (g, x) in enumerate(zip(gold, got, strict=False), 1):
        diffs = [f"{f}: {getattr(x, f)!r} != {getattr(g, f)!r}" for f in fields
                 if getattr(g, f) != getattr(x, f)]  # fmt: skip
        print(f"  s{i} {x.place_name!r}: {'ok' if not diffs else '; '.join(diffs)}")


if __name__ == "__main__":
    asyncio.run(main())
