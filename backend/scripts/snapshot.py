"""Record or compare a behaviour snapshot: the regression check for refactoring.

    uv run python scripts/snapshot.py --save    # record data/evals/snapshot.json
    uv run python scripts/snapshot.py           # compare with it; exit 1 on any difference

Runs in eval mode, so every lookup comes from the Postgres cache and nothing calls an LLM:
the dev split's gold-mode eval (each itinerary's predictions, counts, statuses and unknowns)
and the checks on the example itinerary (every result, violation and unknown, hotels
skipped as in check_itinerary.py --example). A change that should alter neither (a refactor,
a docs edit) must leave the snapshot identical. The snapshot holds dataset place names, so
it lives in data/ (gitignored). Dev split only: Vienna and the rest of the test split are
never touched. Run it once with --save before the change, then without after.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility import paths
from trip_feasibility.agent.graph import CheckInput, run_agent
from trip_feasibility.checks.config import CHECK_NAMES, CheckConfig
from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.example import load_example
from trip_feasibility.dataset.validate import validate_dataset
from trip_feasibility.db import make_engine
from trip_feasibility.evals.decisions import MatchDecisions
from trip_feasibility.evals.runner import evaluate
from trip_feasibility.parse.geocode import geocode_stops, locate_city
from trip_feasibility.parse.normalise import normalise

DATASET = paths.DATASET
DECISIONS = paths.MATCH_DECISIONS
SNAPSHOT = paths.SNAPSHOT
VOLATILE = {"seconds", "input_tokens", "output_tokens", "fix_tokens"}


async def take(deps) -> dict:
    report = validate_dataset(DATASET, partial=True)
    if report.outside_test:
        raise SystemExit("Fix the dataset first (scripts/validate_dataset.py).")
    if report.test_errors:
        print(f"{len(report.test_errors)} test-file problem(s), not blocking (dev only)")
    dev = [i for i in report.itineraries if i.split == "dev" and i.annotated and i.stops]
    outcomes = await evaluate(
        deps,
        dev,
        {h.key: h for h in report.hotels},
        mode="gold",
        config=CheckConfig(),
        settings=get_settings(),
        decisions=MatchDecisions.load(DECISIONS),  # read only: never saved here
    )
    gold = {o.id: {k: v for k, v in asdict(o).items() if k not in VOLATILE} for o in outcomes}

    example = load_example()
    city = await locate_city(deps, example.city)
    assert city is not None
    stops, _ = await geocode_stops(deps, normalise(example.stops, example.trip_start), city)
    inp = CheckInput.for_city(
        city,
        stops,
        example.preferences,
        CheckConfig(enabled=[n for n in CHECK_NAMES if n != "hotel_red_flags"]),
    )
    example, _ = await run_agent(deps, inp, None)
    return {
        "dataset_version": report.version,
        "gold_mode": gold,
        "example": {
            "stops": [s.model_dump(mode="json") for s in stops],
            "report": example.model_dump(mode="json"),
        },
    }


def differences(old, new, path: str = "") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in sorted(set(old) | set(new), key=str):
            if k not in new:
                out.append(f"{path}/{k}: gone")
            elif k not in old:
                out.append(f"{path}/{k}: new")
            else:
                out += differences(old[k], new[k], f"{path}/{k}")
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        pairs = enumerate(zip(old, new, strict=True))
        return [d for i, (a, b) in pairs for d in differences(a, b, f"{path}[{i}]")]
    return [] if old == new else [f"{path}: {json.dumps(old)[:120]} -> {json.dumps(new)[:120]}"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", action="store_true", help="record the snapshot")
    args = parser.parse_args()
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_http_client(settings) as http:
            deps = make_check_deps(http, async_sessionmaker(engine), settings, eval_mode=True)
            snap = json.loads(json.dumps(await take(deps), default=str))
    finally:
        await engine.dispose()
    if args.save:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snap, indent=1, sort_keys=True))
        print(f"Snapshot saved: {len(snap['gold_mode'])} dev itineraries + the example.")
        return 0
    if not SNAPSHOT.exists():
        print("No snapshot yet: run with --save first.", file=sys.stderr)
        return 1
    diffs = differences(json.loads(SNAPSHOT.read_text()), snap)
    if diffs:
        print(f"Snapshot differs in {len(diffs)} place(s):")
        for d in diffs[:40]:
            print(f"  {d}")
        return 1
    print(f"Snapshot identical: {len(snap['gold_mode'])} dev itineraries + the example.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
