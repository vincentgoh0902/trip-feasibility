"""Run the eval on the annotated dataset and log it to eval_runs.

    uv run python scripts/run_eval.py                         # gold stops, dev split
    uv run python scripts/run_eval.py --mode parsed           # full pipeline (needs API key)
    uv run python scripts/run_eval.py --mode both
    uv run python scripts/run_eval.py --fixes                 # + fix validity (needs API key)
    uv run python scripts/run_eval.py --ids paris-01,london-03 --no-save   # trying things out
    uv run python scripts/run_eval.py --split test --final    # reported results only

Runs in eval mode: cached lookups are reused even when expired, so reruns make no external
calls once every lookup is cached. Only annotated (human-verified) itineraries are run;
gold mode only those with gold stops (dev only, for parser debugging). The test split
(including Vienna) needs --final: it is touched only for reported results, and per-itinerary
lines are never printed for it. Details per itinerary go to data/evals/runs/ (gitignored).
Uncertain place-name matches wait for a human decision (scripts/match_decisions.py); the
run lists how many are undecided, and its scores are provisional until there are none.
"""

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.agent.fixes import FIX_PROMPT_VERSION
from trip_feasibility.checks.config import CheckConfig
from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.validate import validate_dataset
from trip_feasibility.db import make_engine
from trip_feasibility.evals.decisions import MatchDecisions
from trip_feasibility.evals.runner import Outcome, evaluate, summarise
from trip_feasibility.models import EvalRun
from trip_feasibility.parse.clean import CLEAN_VERSION
from trip_feasibility.parse.extract import PROMPT_VERSION, make_client

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data" / "dataset"
OUT = ROOT / "data" / "evals" / "runs"
DECISIONS = ROOT / "data" / "evals" / "match_decisions.yaml"


def _of(n: int, total: int) -> str:
    return f"{n} of {total} ({n / total:.0%})" if total else "n/a"


def print_summary(title: str, s: dict) -> None:
    print(f"\n{title}")
    print(f"  {'check':16} {'precision':>18} {'recall':>18} {'F1':>5} {'severity':>9}")
    rows = {**s["per_check"], "ALL": s["overall"]}
    for check, m in rows.items():
        f1 = f"{m['f1']:.2f}" if m["f1"] is not None else "-"
        agree = f"{m['severity_agreement']:.0%}" if m["severity_agreement"] is not None else "-"
        print(
            f"  {check:16} {_of(m['tp'], m['tp'] + m['fp']):>18} "
            f"{_of(m['tp'], m['tp'] + m['fn']):>18} {f1:>5} {agree:>9}"
        )


def progress(split: str):
    done = 0

    def report(o: Outcome) -> None:
        nonlocal done
        done += 1
        if split == "test":
            print(f"  {done} done", end="\r")  # no per-itinerary output for the test set
            return
        found = sum(c.tp for c in o.counts.values())
        wrong = sum(c.fp for c in o.counts.values())
        missed = sum(c.fn for c in o.counts.values())
        status = (
            f"error: {o.error}" if o.error else f"{found} found, {wrong} wrong, {missed} missed"
        )
        print(f"  {o.id:20} {status}  ({o.seconds:.1f}s)")

    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["gold", "parsed", "both"], default="gold")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--final", action="store_true", help="required for --split test")
    parser.add_argument("--ids", default="", help="comma-separated subset (dev only)")
    parser.add_argument("--fixes", action="store_true", help="also propose and re-check fixes")
    parser.add_argument("--no-save", action="store_true", help="don't write eval_runs")
    parser.add_argument("--dir", type=Path, default=DATASET)
    args = parser.parse_args()
    if args.split == "test" and not args.final:
        parser.error("the test split is only for reported results: add --final")
    if args.split == "test" and args.ids:
        parser.error("--ids is for the dev split only")

    report = validate_dataset(args.dir, partial=True)
    if report.errors:
        print("Fix the dataset first (scripts/validate_dataset.py):", file=sys.stderr)
        for e in report.errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    ids = set(filter(None, args.ids.split(",")))
    chosen = [
        i
        for i in report.itineraries
        if i.split == args.split and i.annotated and (not ids or i.id in ids)
    ]
    waiting = sum(1 for i in report.itineraries if i.split == args.split and not i.annotated)
    if ids - {i.id for i in chosen}:
        parser.error(
            f"not annotated dev itineraries: {', '.join(sorted(ids - {i.id for i in chosen}))}"
        )
    if not chosen:
        print(f"No annotated {args.split} itineraries yet ({waiting} waiting).")
        return 1
    print(f"{len(chosen)} {args.split} itineraries, dataset {report.version}"
          + (f" ({waiting} not annotated yet)" if waiting else ""))  # fmt: skip

    settings = get_settings()
    modes = ["gold", "parsed"] if args.mode == "both" else [args.mode]
    if ("parsed" in modes or args.fixes) and settings.anthropic_api_key is None:
        print("Parsed mode and --fixes need ANTHROPIC_API_KEY (repo-root .env).", file=sys.stderr)
        return 1
    hotels = {h.key: h for h in report.hotels}
    config = CheckConfig()
    engine = make_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine)
    OUT.mkdir(parents=True, exist_ok=True)
    decisions = MatchDecisions.load(DECISIONS)
    try:
        async with make_http_client(settings) as http:
            deps = make_check_deps(http, sessionmaker, settings, eval_mode=True)
            for mode in modes:
                print(f"\n{mode} stops")
                # gold stops are dev only (parser debugging), so not every file has them
                runnable = [i for i in chosen if mode == "parsed" or i.stops is not None]
                if len(runnable) < len(chosen):
                    print(f"  {len(chosen) - len(runnable)} without gold stops, left out")
                outcomes = await evaluate(
                    deps,
                    runnable,
                    hotels,
                    mode=mode,
                    config=config,
                    settings=settings,
                    client=make_client(settings) if mode == "parsed" or args.fixes else None,
                    fixes=args.fixes,
                    decisions=decisions,
                    progress=progress(args.split),
                )
                decisions.save()
                summary = summarise(outcomes)
                metrics = {
                    "mode": mode,
                    "split": args.split,
                    "ids": sorted(ids) or None,
                    "config": config.model_dump(),
                    "fix_model": settings.fix_model if args.fixes else None,
                    "fix_prompt_version": FIX_PROMPT_VERSION if args.fixes else None,
                    "clean_version": CLEAN_VERSION if mode == "parsed" else None,
                    "match_decisions_version": decisions.version(),
                    **summary,
                }
                model_id = settings.extraction_model if mode == "parsed" else "none (gold stops)"
                prompt = PROMPT_VERSION if mode == "parsed" else "-"
                run_id = uuid.uuid4()
                if not args.no_save:
                    async with sessionmaker() as session:
                        session.add(
                            EvalRun(
                                id=run_id,
                                dataset_version=report.version,
                                model_id=model_id,
                                prompt_version=prompt,
                                metrics=metrics,
                            )
                        )
                        await session.commit()
                detail = (
                    OUT / f"{datetime.now():%Y%m%d-%H%M}-{args.split}-{mode}-{str(run_id)[:8]}.json"
                )
                detail.write_text(
                    json.dumps(
                        {"metrics": metrics, "outcomes": [asdict(o) for o in outcomes]},
                        indent=2,
                        default=str,
                    )
                )
                print_summary(f"{mode} stops: violations", summary)
                if summary["undecided_pairs"]:
                    ask = "--ask --final" if args.split == "test" else "--ask"
                    n = summary["undecided_pairs"]
                    print(f"  provisional: {n} place-name pairs need your decision "
                          f"(scripts/match_decisions.py {ask}), then rerun")  # fmt: skip
                if "seen_cities" in summary:
                    print_summary(
                        "  seen cities (Paris, London, Barcelona)", summary["seen_cities"]
                    )
                    print_summary("  unseen city (Vienna)", summary["unseen_city"])
                if "parse" in summary:
                    p = summary["parse"]
                    print(f"  parse: stop recall {p['stop_recall']}, precision "
                          f"{p['stop_precision']}, field accuracy {p['field_accuracy']}, "
                          f"errors by field {p['field_errors']}")  # fmt: skip
                if "fixes" in summary:
                    fx = summary["fixes"]
                    valid = _of(fx["valid"], fx["proposed"])
                    fixed = _of(fx["violations_fixed"], fx["violations"])
                    print(f"  fixes: {valid} valid; {fixed} violations got one")
                cov = summary["coverage"]
                print(f"  unavailable runs {cov['unavailable_runs']}")
                print(f"  unknown stops {cov['unknown_stops']}")
                if summary["errors"]:
                    print(f"  errors {summary['errors']}")
                if summary["hotel_unlabelled"]:
                    print(
                        "  hotel check not scored (hotel unlabelled):", summary["hotel_unlabelled"]
                    )
                saved = "not saved" if args.no_save else f"eval_runs {run_id}"
                print(f"  {saved}; details in {detail.relative_to(ROOT)}")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
