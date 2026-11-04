"""Set the cause on verified opening_hours labels that don't have one (decision 88).

    uv run python scripts/set_cause.py           # list what's missing (dev)
    uv run python scripts/set_cause.py --ask     # decide them one by one

For labels verified before causes existed. You decide each one, from the label's own
evidence and source: regular = the venue's standing published hours (weekly closing days,
opening times, last admission, the holidays it closes on every year); special = something
tied to particular dates (a maintenance shutdown, renovation, this year's hours, an event, a
strike). Nothing here decides for you. Each file is saved as soon as it's done, after one
backup of itineraries/, in the standard layout (dataset/layout.py); a file with comments of
its own is left for you to edit by hand. Dev files only: test files get their causes with
their drafts (scripts/draft_gold.py --test), checked while verifying.
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

from trip_feasibility import paths
from trip_feasibility.dataset.layout import LayoutError, format_itinerary
from trip_feasibility.dataset.validate import load_yaml, validate_dataset

ANSWERS = {"r": "regular", "s": "special"}


def missing(raw: dict) -> list[int]:
    """Indexes of the file's opening_hours labels without a cause."""
    return [
        i
        for i, v in enumerate(raw.get("violations") or [])
        if v.get("check") == "opening_hours" and not v.get("cause")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ask", action="store_true", help="decide them one by one")
    parser.add_argument("--dir", type=Path, default=paths.DATASET)
    args = parser.parse_args()

    report = validate_dataset(args.dir, partial=True)
    todo = [i for i in report.itineraries if i.split == "dev" and i.causes_missing]
    if not todo:
        print("Every verified opening_hours label has a cause.")
        return 0
    print(f"{sum(i.causes_missing for i in todo)} label(s) in {len(todo)} file(s) need a cause.")
    if not args.ask:
        for itin in todo:
            print(f"  {itin.id}: {itin.causes_missing}")
        print("Run with --ask to decide them.")
        return 0

    backup = args.dir.parent / f"dataset-backup-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(args.dir / "itineraries", backup / "itineraries")
    print(f"backup {backup}\n")
    print("For each: r(egular), s(pecial), k (skip), q (quit). Regular = standing hours,")
    print("yearly holiday closures included; special = tied to particular dates.\n")
    for itin in todo:
        path = args.dir / "itineraries" / f"{itin.id}.yaml"
        raw = load_yaml(path)
        changed = False
        for i in missing(raw):
            v = raw["violations"][i]
            when = f" ({v['time']})" if v.get("time") else ""
            print(f"{itin.id}, day {v['day']}: {v['place']}{when}, {v['severity']}")
            print(f"  evidence: {v['evidence']}\n  source:   {v['source']}")
            answer = ""
            while answer not in ("r", "s", "k", "q"):
                answer = input("  cause? [r/s/k/q] ").strip().lower()[:1]
            if answer == "q":
                break
            if answer in ANSWERS:
                v["cause"] = ANSWERS[answer]
                changed = True
            print()
        if changed:
            try:
                text = format_itinerary(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
            except LayoutError as e:
                print(f"  not saved: {itin.id} ({e}); add the causes by hand")
            else:
                path.write_text(text, encoding="utf-8")
                print(f"  saved {itin.id}\n")
        if answer == "q":
            break
    left = sum(i.causes_missing for i in validate_dataset(args.dir, partial=True).itineraries)
    print(f"{left} label(s) still without a cause.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
