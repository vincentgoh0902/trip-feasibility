"""Load hotels and review embeddings from the Kaggle CSV into Postgres (Mac only).

    uv run --group embed python scripts/load_reviews.py
    uv run --group embed python scripts/load_reviews.py --reembed   # ignore cached vectors

1. Reads data/raw/hotel-reviews/Hotel_Reviews.csv; keeps the four dataset cities and the
   reviews with a negative part (stock "No Negative" / "Nothing" answers are dropped).
2. Embeds each negative text with REVIEW_EMBEDDING_MODEL on the Mac's GPU (MPS). Vectors are
   cached in data/embeddings/, keyed by a hash of the model and texts, so reloading is fast.
3. Replaces the hotels and hotel_reviews tables in DATABASE_URL.

The embedding libraries are in the `embed` dependency group, which the Docker image never
installs. The server gets this data from a dump of these tables, not by running this.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector
from sqlalchemy.engine import make_url

from trip_feasibility.config import get_settings
from trip_feasibility.models import REVIEW_EMBEDDING_DIM, REVIEW_EMBEDDING_MODEL

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "data" / "raw" / "hotel-reviews" / "Hotel_Reviews.csv"
CACHE_DIR = ROOT / "data" / "embeddings"
CITIES = {
    "United Kingdom": "london",
    "Paris France": "paris",
    "Barcelona Spain": "barcelona",
    "Vienna Austria": "vienna",
}
EMPTY = {"", "no negative", "nothing", "none", "n a", "na", "nothing really", "nothing at all"}


@dataclass
class Review:
    hotel: tuple[str, str]  # (name, address)
    review_date: date
    reviewer_score: Decimal
    text: str


def city_of(address: str) -> str | None:
    return next((c for suffix, c in CITIES.items() if address.endswith(suffix)), None)


def read_csv(path: Path) -> tuple[dict[tuple[str, str], dict], list[Review]]:
    hotels: dict[tuple[str, str], dict] = {}
    reviews = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            city = city_of(r["Hotel_Address"])
            if city is None:
                continue
            key = (r["Hotel_Name"], r["Hotel_Address"])
            if key not in hotels:
                hotels[key] = {
                    "name": r["Hotel_Name"],
                    "address": r["Hotel_Address"],
                    "city": city,
                    "lat": float(r["lat"]) if r["lat"] not in ("", "NA") else None,
                    "lon": float(r["lng"]) if r["lng"] not in ("", "NA") else None,
                    "average_score": Decimal(r["Average_Score"]),
                    "total_reviews": int(r["Total_Number_of_Reviews"]),
                }
            text = r["Negative_Review"].strip()
            if text.lower() in EMPTY:
                continue
            reviews.append(
                Review(
                    hotel=key,
                    review_date=datetime.strptime(r["Review_Date"], "%m/%d/%Y").date(),
                    reviewer_score=Decimal(r["Reviewer_Score"]),
                    text=text,
                )
            )
    return hotels, reviews


def embed(texts: list[str], *, reembed: bool, batch_size: int) -> np.ndarray:
    digest = hashlib.sha256(REVIEW_EMBEDDING_MODEL.encode())
    for t in texts:
        digest.update(t.encode() + b"\0")
    key = digest.hexdigest()[:16]
    cache = CACHE_DIR / f"{REVIEW_EMBEDDING_MODEL.replace('/', '__')}-{key}.npy"
    if cache.exists() and not reembed:
        print(f"Using cached vectors {cache.name}")
        return np.load(cache)

    from sentence_transformers import SentenceTransformer  # embed group only

    model = SentenceTransformer(REVIEW_EMBEDDING_MODEL, device="mps")
    started = time.monotonic()
    vectors = model.encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=True
    ).astype(np.float16)
    assert vectors.shape == (len(texts), REVIEW_EMBEDDING_DIM)
    print(f"Embedded {len(texts)} texts in {time.monotonic() - started:.0f}s")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(cache, vectors)
    cache.with_suffix(".json").write_text(
        json.dumps({"model": REVIEW_EMBEDDING_MODEL, "rows": len(texts), "key": key})
    )
    return vectors


async def load(hotels: dict, reviews: list[Review], vectors: np.ndarray) -> None:
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    conn = await asyncpg.connect(url.render_as_string(hide_password=False))
    try:
        await register_vector(conn)
        async with conn.transaction():
            await conn.execute("TRUNCATE hotels, hotel_reviews RESTART IDENTITY")
            hotel_rows = list(hotels.values())
            await conn.copy_records_to_table(
                "hotels",
                columns=list(hotel_rows[0]),
                records=[tuple(h.values()) for h in hotel_rows],
            )
            ids = {
                (r["name"], r["address"]): r["id"]
                for r in await conn.fetch("SELECT id, name, address FROM hotels")
            }
            await conn.copy_records_to_table(
                "hotel_reviews",
                columns=["hotel_id", "review_date", "reviewer_score", "negative_text", "embedding"],
                records=(
                    (ids[r.hotel], r.review_date, r.reviewer_score, r.text, v)
                    for r, v in zip(reviews, vectors, strict=True)
                ),
            )
        counts = await conn.fetch(
            "SELECT h.city, count(DISTINCT h.id) AS hotels, count(r.id) AS reviews "
            "FROM hotels h LEFT JOIN hotel_reviews r ON r.hotel_id = h.id "
            "GROUP BY h.city ORDER BY h.city"
        )
        for row in counts:
            print(f"  {row['city']:10} {row['hotels']:>4} hotels {row['reviews']:>7} reviews")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--reembed", action="store_true")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()

    hotels, reviews = read_csv(args.csv)
    print(f"{len(hotels)} hotels, {len(reviews)} reviews with a negative part")
    vectors = embed([r.text for r in reviews], reembed=args.reembed, batch_size=args.batch_size)
    asyncio.run(load(hotels, reviews, vectors))


if __name__ == "__main__":
    main()
