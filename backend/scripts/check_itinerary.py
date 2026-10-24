"""Check an itinerary from the command line: parse (Claude) → stops → all checks → report.

    uv run python scripts/check_itinerary.py --example        # no API key needed
    uv run python scripts/check_itinerary.py trip.txt --city Paris --start 2027-04-24
    uv run python scripts/check_itinerary.py trip.txt --city Paris --start 2027-04-24 \\
        --pace relaxed --modes walk,transit --buffer 20 --min-reviews 3 --json report.json

--example checks the made-up example itinerary (docs/annotation/example.yaml) on its
hand-written stops, skipping extraction. Otherwise the text is parsed with Claude first
(needs ANTHROPIC_API_KEY). Real lookups (Nominatim, routing, Nager.Date) go through the
Postgres cache; --eval-mode also serves expired cache entries. Don't use this on dataset
itineraries: the eval runner does that, and keeps Vienna out of sight.
"""

import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.agent.graph import CheckInput, CheckReport, run_checks
from trip_feasibility.checks.config import CHECK_NAMES, CheckConfig
from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.validate import load_yaml
from trip_feasibility.db import make_engine
from trip_feasibility.itinerary import Stop, TravelPreferences
from trip_feasibility.parse.extract import make_client
from trip_feasibility.parse.geocode import Geocode, geocode_stops, locate_city
from trip_feasibility.parse.normalise import normalise
from trip_feasibility.parse.pipeline import ParseError, parse_itinerary
from trip_feasibility.parse.schema import ExtractedStop

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "annotation" / "example.yaml"


def print_stops(stops: list[Stop], geocodes: dict[str, Geocode]) -> None:
    print("\nStops")
    for s in stops:
        when = "-".join(t.strftime("%H:%M") for t in (s.start_time, s.end_time) if t) or "?"
        g = geocodes.get(s.id)
        where = (
            "unresolved"
            if g is None or g.place is None
            else g.place.osm.url + (" (outside the city)" if g.outside_city else "")
        )
        print(f"  {s.id:4} day {s.day} {s.date:%a} {when:11} {s.type:8} {s.place_name}  [{where}]")


def print_report(report: CheckReport) -> None:
    print("\nChecks")
    for name, r in report.results.items():
        line = f"  {name:16} {r.status:11} {len(r.violations)} violation(s), "
        line += f"{len(r.unknown)} unknown, {len(r.checked_stop_ids)} stop(s) checked"
        print(line + (f"  ({r.error})" if r.error else ""))
        for note in r.notes:
            print(f"  {'':16} note: {note}")
        for u in r.unknown:
            print(f"  {'':16} unknown {u.stop_id}: {u.reason}")
    print("\nViolations" if report.violations else "\nNo violations.")
    for v in report.violations:
        print(f"  [{v.severity.upper():7}] {v.check_name:16} {','.join(v.stop_ids)}: {v.message}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", nargs="?", type=Path, help="itinerary text file")
    parser.add_argument("--example", action="store_true")
    parser.add_argument("--city")
    parser.add_argument("--start", type=date.fromisoformat, help="trip start, YYYY-MM-DD")
    parser.add_argument("--pace", choices=["relaxed", "moderate", "packed"], default="moderate")
    parser.add_argument("--max-walk-km", type=float, default=10.0)
    parser.add_argument("--modes", default="walk,transit", help="walk,transit,bike,car")
    parser.add_argument("--buffer", type=int, choices=[0, 10, 20], default=10)
    parser.add_argument("--min-reviews", type=int, choices=[3, 5, 10], default=5)
    parser.add_argument("--disable", default="", help=f"comma-separated: {','.join(CHECK_NAMES)}")
    parser.add_argument("--eval-mode", action="store_true")
    parser.add_argument("--json", type=Path, help="write the full report here")
    args = parser.parse_args()
    if args.example == bool(args.file) or (args.file and not (args.city and args.start)):
        parser.error("give --example, or a file with --city and --start")

    preferences = TravelPreferences(
        pace=args.pace, max_walk_km=args.max_walk_km, transport_modes=args.modes.split(",")
    )
    disabled = set(filter(None, args.disable.split(",")))
    config = CheckConfig(
        enabled=[n for n in CHECK_NAMES if n not in disabled],
        travel_buffer_min=args.buffer,
        hotel_min_reviews=args.min_reviews,
    )
    settings = get_settings()
    engine = make_engine(settings.database_url)
    started = time.monotonic()
    try:
        async with make_http_client(settings) as http:
            deps = make_check_deps(
                http, async_sessionmaker(engine), settings, eval_mode=args.eval_mode
            )
            if args.example:
                if "hotel_red_flags" in config.enabled:
                    # its hotel is one of the 16 dataset hotels: no peeking before labelling
                    config.enabled.remove("hotel_red_flags")
                    print("hotel_red_flags skipped: the example's hotel is a dataset hotel.")
                raw = load_yaml(EXAMPLE)
                city_name, trip_start = raw["city"].title(), raw["trip_start"]
                preferences = TravelPreferences(**raw["preferences"])
                city = await locate_city(deps, city_name)
                assert city is not None
                fields = ExtractedStop.model_fields
                extracted = [
                    ExtractedStop(**{k: v for k, v in s.items() if k in fields})
                    for s in raw["stops"]
                ]
                stops, geocodes = await geocode_stops(deps, normalise(extracted, trip_start), city)
            else:
                if settings.anthropic_api_key is None:
                    print("ANTHROPIC_API_KEY is not set (repo-root .env).", file=sys.stderr)
                    return 1
                try:
                    parsed = await parse_itinerary(
                        deps,
                        make_client(settings),
                        settings,
                        args.file.read_text(),
                        city=args.city,
                        trip_start=args.start,
                    )
                except ParseError as e:
                    print(f"Parse failed ({e.code}): {e.message}", file=sys.stderr)
                    return 1
                city, stops, geocodes = parsed.city, parsed.stops, parsed.geocodes
                x = parsed.extraction
                print(
                    f"Extracted {len(stops)} stops with {x.model} ({x.prompt_version}), "
                    f"{x.attempts} attempt(s), {x.input_tokens} in / {x.output_tokens} out tokens"
                )
            print_stops(stops, geocodes)
            inp = CheckInput(
                stops=stops,
                city=city.name,
                country_code=city.country_code.upper() if city.country_code else None,
                preferences=preferences,
                config=config,
            )
            report = await run_checks(deps, inp)
    finally:
        await engine.dispose()
    print_report(report)
    print(f"\n{time.monotonic() - started:.1f}s")
    if args.json:
        args.json.write_text(report.model_dump_json(indent=2))
        print(f"Report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
