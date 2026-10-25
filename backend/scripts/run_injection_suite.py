"""Run the prompt-injection suite (evals/injection.py) against the real model, with and without
defences, and log the result to eval_runs.

    uv run python scripts/run_injection_suite.py                  # both conditions
    uv run python scripts/run_injection_suite.py --defended-only  # cheaper: the shipped code
    uv run python scripts/run_injection_suite.py --no-save        # trying things out

Needs ANTHROPIC_API_KEY. 24 cases per condition, one or two model calls each (a fix retry can
add one): a few cents with Haiku 5.5. Details per case go to data/evals/runs/ (gitignored).
No dataset itinerary is used: the base is the demo sample.
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
from trip_feasibility.config import get_settings
from trip_feasibility.db import make_engine
from trip_feasibility.evals.injection import (
    baseline_extraction,
    cases,
    defences_off,
    run_case,
    suite_version,
    summarise,
)
from trip_feasibility.models import EvalRun
from trip_feasibility.parse.extract import PROMPT_VERSION, make_client
from trip_feasibility.tracing import NoTracer, TracedClient, Usage

OUT = Path(__file__).resolve().parents[2] / "data" / "evals" / "runs"
CONCURRENCY = 4


async def run_condition(name: str, client, settings) -> tuple[dict, list]:
    models = {
        "extraction_model": settings.extraction_model,
        "extraction_effort": settings.extraction_effort,
        "fix_model": settings.fix_model,
        "fix_effort": settings.fix_effort,
    }
    baseline = await baseline_extraction(
        client, settings.extraction_model, settings.extraction_effort
    )
    slots = asyncio.Semaphore(CONCURRENCY)

    async def one(case):
        async with slots:
            r = await run_case(case, client, baseline=baseline, **models)
            mark = {True: "FOLLOWED", False: "resisted", None: "failed  "}[r.followed]
            print(f"  [{name}] {mark} {r.case}")
            return r

    results = await asyncio.gather(*(one(c) for c in cases()))
    return summarise(results), results


def print_summary(name: str, summary: dict) -> None:
    def line(label: str, b: dict) -> str:
        pct = f"{b['resistance']:.0%}" if b["resistance"] is not None else "n/a"
        failed = f", {b['failed_calls']} failed call(s)" if b["failed_calls"] else ""
        return f"  {label:14} {b['resisted']} of {b['judged']} resisted ({pct}){failed}"

    print(f"\n{name}")
    print(line("overall", summary["overall"]))
    for v, b in summary["by_vector"].items():
        print(line(v, b))
    for g, b in summary["by_goal"].items():
        print(line(g, b))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--defended-only", action="store_true")
    parser.add_argument("--no-save", action="store_true", help="don't write eval_runs")
    args = parser.parse_args()
    settings = get_settings()
    if settings.anthropic_api_key is None:
        print("ANTHROPIC_API_KEY is not set (repo-root .env).", file=sys.stderr)
        return 1
    usage = Usage()
    client = TracedClient(make_client(settings), NoTracer(), usage, "injection")

    summaries, details = {}, {}
    summaries["defended"], details["defended"] = await run_condition("defended", client, settings)
    if not args.defended_only:
        with defences_off():
            summaries["undefended"], details["undefended"] = await run_condition(
                "undefended", client, settings
            )
    metrics = {
        "kind": "injection",
        "suite_version": suite_version(),
        "extraction_model": settings.extraction_model,
        "fix_model": settings.fix_model,
        **summaries,
        "usage": usage.as_dict(),
    }
    run_id = uuid.uuid4()
    if not args.no_save:
        engine = make_engine(settings.database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                session.add(
                    EvalRun(
                        id=run_id,
                        dataset_version=suite_version(),
                        model_id=f"{settings.extraction_model} / {settings.fix_model}",
                        prompt_version=f"{PROMPT_VERSION} / {FIX_PROMPT_VERSION}",
                        metrics=metrics,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()
    OUT.mkdir(parents=True, exist_ok=True)
    detail = OUT / f"{datetime.now():%Y%m%d-%H%M}-injection-{str(run_id)[:8]}.json"
    detail.write_text(
        json.dumps(
            {"metrics": metrics, "cases": {k: [asdict(r) for r in v] for k, v in details.items()}},
            indent=2,
            default=str,
        )  # fmt: skip
    )
    for name, summary in summaries.items():
        print_summary(name, summary)
    u = usage.as_dict()
    print(f"\n{u['calls']} calls, {u['input_tokens']} in / {u['output_tokens']} out tokens, "
          f"${u['cost_usd']}")  # fmt: skip
    print(f"Details: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
