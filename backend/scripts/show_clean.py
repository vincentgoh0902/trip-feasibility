"""Print a saved reply as extraction sees it, after parse step 0 (guide: docs/ANNOTATION.md).

    uv run python scripts/show_clean.py paris-03     # data/dataset/texts/paris-03.md

Gold place_names are copied from this text. Character counts go to stderr.
"""

import argparse
import sys
from pathlib import Path

from trip_feasibility.dataset.validate import text_path
from trip_feasibility.parse.clean import MAX_CLEAN_CHARS, clean_text

DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "dataset"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("id", help="e.g. paris-03")
    args = parser.parse_args()
    path = text_path(DATASET_DIR, args.id)
    if not path.exists():
        print(f"No saved reply at {path}", file=sys.stderr)
        return 1
    raw = path.read_text(encoding="utf-8")
    text = clean_text(raw)
    print(text)
    print(f"\n{len(raw):,} characters raw, {len(text):,} cleaned", file=sys.stderr)
    if len(text) > MAX_CLEAN_CHARS:
        print(f"Over the {MAX_CLEAN_CHARS:,}-character limit for cleaned text", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
