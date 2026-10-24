"""How often each red-flag theme comes up per hotel, to set THEMES' min_share.

    uv run python scripts/hotel_theme_base_rates.py

Runs the check's counting (count_themes) over every hotel in Paris, London and Barcelona with
50+ negative reviews, except the 16 dataset hotels and everything in Vienna, and prints the
distribution of each theme's share of negative reviews. min_share is the 90th percentile:
a red flag means more complaints than 90% of comparable hotels.
"""

import asyncio
import statistics
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.checks.hotel_red_flags import THEMES, count_themes
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.validate import load_yaml
from trip_feasibility.db import make_engine
from trip_feasibility.models import Hotel, HotelReview

HOTELS_YAML = Path(__file__).resolve().parents[2] / "docs" / "annotation" / "hotels.yaml"
MIN_NEGATIVE_REVIEWS = 50


async def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    dataset = {h["csv_name"] for h in load_yaml(HOTELS_YAML)}
    query = (
        select(Hotel.id, Hotel.name)
        .join(HotelReview)
        .where(Hotel.city.in_(["paris", "london", "barcelona"]))
        .group_by(Hotel.id)
        .having(func.count(HotelReview.id) >= MIN_NEGATIVE_REVIEWS)
    )
    async with make_http_client(settings) as http:
        deps = make_check_deps(http, async_sessionmaker(engine), settings)
        async with deps.db() as session:
            hotels = [h for h in (await session.execute(query)).all() if h.name not in dataset]
        started = time.monotonic()
        shares: dict[str, list[float]] = {name: [] for name in THEMES}
        for h in hotels:
            total, counts = await count_themes(deps, h.id)
            for name, c in counts.items():
                shares[name].append(c.reviews / total)
    await engine.dispose()

    print(f"{len(hotels)} hotels in {time.monotonic() - started:.0f}s\n")
    print(f"{'theme':12} {'p50':>6} {'p75':>6} {'p90':>6} {'p95':>6}   current min_share")
    for name, values in shares.items():
        cuts = statistics.quantiles(values, n=20, method="inclusive")  # 5% steps
        p = [cuts[9], cuts[14], cuts[17], cuts[18]]
        print(f"{name:12} " + " ".join(f"{x:6.3f}" for x in p) + f"   {THEMES[name].min_share}")


if __name__ == "__main__":
    asyncio.run(main())
