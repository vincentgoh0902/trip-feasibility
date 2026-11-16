"""Prepare the demo: look the samples up and record the model's replies, so demo runs (which
never search and never reach the model) show closures, the second opinion and fixes
(decisions 94, 97, 100).

    python -m trip_feasibility.demo.warm      # in the API container, or `uv run` locally

Needs ANTHROPIC_API_KEY. For each sample, both ways a visitor checks it: the ready-made
stops (its card), and its text pasted, parsed and checked as parsed. Each goes through the
API's own path as a demo run that may search the web, with a `PreparingClient` storing every
model reply (extraction, closures, second opinion, fixes); then a plain demo run confirms it
needs nothing that wasn't prepared. About $0.50 for the three samples. Rerun after changing
a sample or any prompt, model or check that changes what the model is asked.
"""

import asyncio
import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.api.checks import pasted_run, sample_run
from trip_feasibility.api.common import Services
from trip_feasibility.api.itineraries import parse_text
from trip_feasibility.api.schemas import ParseRequest, PastedCheck
from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.db import make_engine
from trip_feasibility.demo import samples
from trip_feasibility.itinerary import Stop
from trip_feasibility.llm import make_client
from trip_feasibility.prepared import PreparedClient, PreparingClient
from trip_feasibility.ratelimit import SlidingWindowLimiter
from trip_feasibility.runs import RunRequest, RunService
from trip_feasibility.tracing import make_tracer


async def _finish(runs: RunService, req: RunRequest) -> dict[str, Any]:
    run = await runs.start(req)
    async for _ in run.follow():
        pass
    if run.result is None:
        error = next((e for e in run.events if e["type"] == "error"), {})
        raise RuntimeError(error.get("message", "the run failed"))
    return run.result


def _summary(result: dict[str, Any]) -> str:
    unchecked = [n for n, r in result["results"].items() if r["status"] != "ok"]
    return (
        f"{len(result['violations'])} problems, {len(result['fixes'])} fixes"
        + (f", fix error: {result['fix_error']}" if result["fix_error"] else "")
        + (f", not ok: {', '.join(unchecked)}" if unchecked else "")
    )


async def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with make_http_client(settings) as http:
            deps = make_check_deps(http, sessionmaker, settings)
            tracer = make_tracer(settings)
            preparing = PreparingClient(make_client(settings), deps.cache)
            warming = RunService(settings, deps, sessionmaker, tracer, None, prepared=preparing)
            checking = RunService(
                settings, deps, sessionmaker, tracer, None, prepared=PreparedClient(deps.cache)
            )
            limit = SlidingWindowLimiter("unused", 1)
            svc = Services(settings, deps, warming, tracer, None, limit, limit, limit)
            for sample in samples().values():
                parsed = await parse_text(
                    svc,
                    preparing,
                    ParseRequest(text=sample.text, city=sample.city, start_date=sample.start_date),
                )
                # the stops as the browser sends them back: the parse response, as JSON
                stops = [Stop.model_validate(s.model_dump(mode="json")) for s in parsed.stops]
                pasted = pasted_run(PastedCheck(city=sample.city, stops=stops))
                for how, req in (("card", sample_run(sample)), ("pasted", pasted)):
                    await _finish(warming, replace(req, searches=True, run_id=uuid.uuid4()))
                    result = await _finish(checking, replace(req, run_id=uuid.uuid4()))
                    print(f"{sample.id} ({how}): {_summary(result)}")
            print(f"{preparing.stored} model replies stored")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
