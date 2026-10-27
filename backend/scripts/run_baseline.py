"""Run the LLM-only baseline (evals/baseline.py) on dataset itineraries.

    uv run python scripts/run_baseline.py --ids paris-08,london-04   # chosen dev files
    uv run python scripts/run_baseline.py                            # every dev file
    uv run python scripts/run_baseline.py --refresh                  # ignore the cache
    uv run python scripts/run_baseline.py --backend claude-code --model claude-haiku-5-5
    uv run python scripts/run_baseline.py --split test --final       # reported results only

Needs no gold: it only produces outputs, cached under data/evals/baseline/; scoring them
against the gold is the comparison run's job. Reruns use the cache, so they make no calls.
The test split (Vienna included) needs --final, and prints no per-itinerary lines.
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from trip_feasibility.dataset import validate_dataset
from trip_feasibility.evals.baseline import cache_path, run_baseline
from trip_feasibility.evals.search_llm import BACKENDS, SearchLLMError, make_backend

DATA = Path(__file__).resolve().parents[2] / "data"
CACHE = DATA / "evals" / "baseline"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DATA / "dataset")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--final", action="store_true", help="required for --split test")
    parser.add_argument("--ids", default="", help="comma-separated dev ids")
    parser.add_argument("--refresh", action="store_true", help="rerun even when cached")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="claude-code")
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--parallel", type=int, default=2)
    args = parser.parse_args()
    if args.split == "test" and not args.final:
        parser.error("the test split is only for reported results: add --final")
    if args.split == "test" and args.ids:
        parser.error("--ids is for the dev split only")

    report = validate_dataset(args.dir, partial=True)
    ids = set(filter(None, args.ids.split(",")))
    chosen = [i for i in report.itineraries if i.split == args.split and (not ids or i.id in ids)]
    if missing := ids - {i.id for i in chosen}:
        parser.error(f"not dev itineraries (or don't load): {', '.join(sorted(missing))}")
    llm = make_backend(args.backend, args.model, args.effort)
    print(f"baseline on {len(chosen)} {args.split} itineraries: {args.backend}/{args.model}")
    return asyncio.run(run_all(llm, args, chosen))


async def run_all(llm, args, chosen) -> int:
    gate = asyncio.Semaphore(args.parallel)
    failed = 0
    quiet = args.split == "test"

    async def one(itin) -> None:
        nonlocal failed
        cache = cache_path(CACHE, itin, args.backend, llm)
        async with gate:
            try:
                result = await run_baseline(llm, itin, cache, refresh=args.refresh)
            except SearchLLMError as e:
                failed += 1
                print(f"failed   {'(test)' if quiet else itin.id}: {e}")
                return
        if quiet:
            return
        by_check = Counter(f.check for f in result.findings)
        run = result.run
        how = "cached" if result.cached else f"{run.searches} searches, {run.seconds / 60:.1f} min"
        counts = dict(sorted(by_check.items()))
        print(
            f"{itin.id}: {len(result.findings)} problems {counts}, "
            f"{len(result.unknowns)} unknowns, {len(result.invalid)} invalid ({how})"
        )

    await asyncio.gather(*(one(i) for i in chosen))
    print(
        f"done; outputs in {CACHE.relative_to(DATA.parent)}"
        + (f", {failed} failed" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
