"""Print a random sample of one hotel's negative reviews, for labelling hotels.yaml.

    uv run python scripts/sample_reviews.py "Hotel Cavendish"            # csv_name from hotels.yaml
    uv run python scripts/sample_reviews.py "Hotel Cavendish" -n 50 --seed 1

Reads the Kaggle CSV (default: ../data/raw/hotel-reviews/Hotel_Reviews.csv). Skips reviews
whose negative text is empty or a stock "No Negative" / "Nothing". The same seed always gives
the same sample, so a label can be re-checked against exactly what was read.
"""

import argparse
import csv
import random
import sys
from pathlib import Path

DEFAULT_CSV = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "hotel-reviews" / "Hotel_Reviews.csv"
)
EMPTY = {"", "no negative", "nothing", "none", "n a", "na", "nothing really", "nothing at all"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("hotel", help="Hotel_Name as written in the CSV (csv_name)")
    parser.add_argument("-n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()

    with args.csv.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["Hotel_Name"] == args.hotel]
    if not rows:
        print(f"No reviews for {args.hotel!r}; use csv_name from hotels.yaml.", file=sys.stderr)
        return 1
    negative = [r for r in rows if r["Negative_Review"].strip().lower() not in EMPTY]
    sample = random.Random(args.seed).sample(negative, min(args.n, len(negative)))
    print(f"{args.hotel}: {len(rows)} reviews, {len(negative)} with a negative part")
    print(f"showing {len(sample)} (seed {args.seed})\n")
    for i, r in enumerate(sample, 1):
        print(f"{i:>3}. [{r['Reviewer_Score']}] {r['Review_Date']}  {r['Negative_Review'].strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
