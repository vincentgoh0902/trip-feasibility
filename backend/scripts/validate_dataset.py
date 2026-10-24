"""Validate the annotated dataset (guide: docs/ANNOTATION.md).

    uv run python scripts/validate_dataset.py             # complete dataset: 15 dev, 25 test
    uv run python scripts/validate_dataset.py --partial   # while collecting and annotating
    uv run python scripts/validate_dataset.py --dir PATH  # default: ../data/dataset

Prints counts and the dataset version; exits 1 if anything is wrong.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from trip_feasibility.dataset import gold_violations, validate_dataset

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "dataset"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--partial", action="store_true", help="allow an unfinished dataset")
    args = parser.parse_args()

    report = validate_dataset(args.dir, partial=args.partial)
    hotels = {h.key: h for h in report.hotels}

    rows = Counter()
    for i in report.itineraries:
        kind = "variant" if i.parent_id else "original"
        rows[(i.split, i.city, kind, i.annotated)] += 1
    print(f"Dataset {args.dir}  version {report.version}")
    print(f"{'split':6} {'city':10} {'originals':>9} {'annotated':>9} {'variants':>8}")
    for split, city in sorted({(s, c) for s, c, _, _ in rows}):
        originals = rows[(split, city, "original", True)] + rows[(split, city, "original", False)]
        variants = rows[(split, city, "variant", True)] + rows[(split, city, "variant", False)]
        annotated = rows[(split, city, "original", True)] + rows[(split, city, "variant", True)]
        print(f"{split:6} {city:10} {originals:>9} {annotated:>9} {variants:>8}")

    labelled = sum(h.red_flags is not None for h in report.hotels)
    print(f"hotels: {len(report.hotels)}, {labelled} labelled")
    labels = Counter(
        (g.check, g.severity) for i in report.itineraries for g in gold_violations(i, hotels)
    )
    if labels:
        print("gold violations (all splits):")
        for (check, severity), n in sorted(labels.items()):
            print(f"  {check:16} {severity:8} {n}")

    if report.errors:
        print(f"\n{len(report.errors)} problem(s):")
        for e in report.errors:
            print(f"  - {e}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
