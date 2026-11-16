"""Look the demo samples up once with web search, so the public demo (which never searches)
shows closures and the second opinion from these lookups (decisions 94, 97).

    python -m trip_feasibility.demo.warm      # in the API container, or `uv run` locally

Needs ANTHROPIC_API_KEY. Runs each sample's checks as a live, signed-in check would (fixes
and the hotel check aside), which stores the closures findings and the second opinion in
the cache; demo runs read them at any age. About $0.30 for the three samples. Rerun after
changing a sample, the second opinion's prompt (PROMPT_VERSION) or its model.
"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.agent.graph import CheckInput, SecondOpinion, run_agent
from trip_feasibility.checks.config import CheckConfig
from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.db import make_engine
from trip_feasibility.demo import samples
from trip_feasibility.llm import make_client
from trip_feasibility.parse.geocode import geocode_stops, locate_city
from trip_feasibility.parse.normalise import normalise


async def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_http_client(settings) as http:
            deps = make_check_deps(http, async_sessionmaker(engine), settings)
            deps.llm = make_client(settings)  # closures search with it
            opinion = SecondOpinion(
                make_client(settings),
                settings.second_opinion_model,
                settings.second_opinion_effort,
                settings.second_opinion_max_searches,
            )
            config = CheckConfig(enabled=["closures", "second_opinion"])
            for sample in samples().values():
                city = await locate_city(deps, sample.city)
                if city is None:
                    print(f"{sample.id}: city not found")
                    continue
                located = normalise(sample.stops, sample.start_date)
                stops, _ = await geocode_stops(deps, located, city)
                inp = CheckInput.for_city(city, stops, sample.preferences, config)
                report, _ = await run_agent(deps, inp, second_opinion=opinion)
                statuses = ", ".join(f"{n} {r.status}" for n, r in report.results.items())
                print(f"{sample.id}: {statuses}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
