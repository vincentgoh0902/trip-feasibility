"""Rewrite the itinerary YAML files in one standard layout (dataset/layout.py).

    uv run python scripts/format_dataset.py            # list the files that would change
    uv run python scripts/format_dataset.py --write    # rewrite them (backup first)
    uv run python scripts/format_dataset.py --dir PATH # default: ../data/dataset

Layout only: a file is rewritten only if it reads back to exactly the same data, and files
with comments of their own are skipped. Prints file names, never their content. The dataset
version is a hash of the file bytes, so it changes too: format before an eval run, not after.
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from trip_feasibility.dataset.layout import LayoutError, format_itinerary

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "dataset"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--write", action="store_true", help="rewrite the files")
    args = parser.parse_args()

    files = sorted((args.dir / "itineraries").glob("*.yaml"))
    changes: dict[Path, str] = {}
    skipped = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            formatted = format_itinerary(text)
        except LayoutError as e:
            print(f"skipped  {path.name}: {e}")
            skipped += 1
            continue
        if formatted != text:
            changes[path] = formatted

    if args.write and changes:
        # next to the dataset, not in it, so the validator never reads the copy
        backup = args.dir.parent / f"dataset-backup-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copytree(args.dir / "itineraries", backup / "itineraries")
        print(f"backup   {backup}")
        for path, formatted in changes.items():
            path.write_text(formatted, encoding="utf-8")
    for path in changes:
        print(f"{'rewrote ' if args.write else 'changes '} {path.name}")
    unchanged = len(files) - len(changes) - skipped
    print(f"{len(changes)} to change, {unchanged} already standard, {skipped} skipped")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
