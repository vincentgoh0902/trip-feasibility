"""Convert itinerary files to the violations-first gold format (ARCHITECTURE decision 74).

    uv run python scripts/convert_gold.py            # show what would change and be dropped
    uv run python scripts/convert_gold.py --write    # rewrite the files (backup first)
    uv run python scripts/convert_gold.py --dir PATH # default: ../data/dataset

Old hand labels named a stop id (opening_hours) or a day, with a `note`; new ones name the
day and the place as written, with evidence, a source and a confidence. Converted files are
`drafted_by: human`, `verified_by_human: false`: the old labels covered three checks, so
the file needs checking against all four labelled ones before it counts. Dev files only:
test and Vienna files are skipped unread (they have no labels yet anyway). Files that don't
parse are skipped and named. Prints the dev files' labels and what was dropped.
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

from trip_feasibility.dataset.layout import LayoutError, format_itinerary
from trip_feasibility.dataset.validate import load_yaml

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "dataset"
NEW_KEYS = {"drafted_by", "verified_by_human", "unknowns", "other_issues"}


def convert(raw: dict) -> tuple[dict, list[str]]:
    """The file in the new format, and what didn't carry over."""
    dropped = []
    stops = {s["id"]: s for s in raw.get("stops") or []}
    new = []
    for v in raw["violations"]:
        label = {"check": v["check"], "severity": v["severity"]}
        if v.get("stop") is not None:
            stop = stops[v["stop"]]
            label |= {"day": stop["day"], "place": stop["place_name"]}
            dropped.append(f"{v['check']}: stop id {v['stop']} (now its day and place)")
        else:
            label["day"] = v["day"]
        label["evidence"] = v["note"]
        label["source"] = v.get("source") or "not recorded"
        label["confidence"] = "verified" if v.get("source") else "estimated"
        if not v.get("source"):
            dropped.append(f"{v['check']} day {label['day']}: no source (marked estimated)")
        dropped.append(f"{v['check']} day {label['day']}: no quoted time (old labels had none)")
        new.append(label)
    out = {k: val for k, val in raw.items() if k != "violations"}
    out |= {"drafted_by": "human", "verified_by_human": False, "violations": new}
    return out, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--write", action="store_true", help="rewrite the files")
    args = parser.parse_args()

    changes: dict[Path, str] = {}
    for path in sorted((args.dir / "itineraries").glob("*.yaml")):
        if path.stem.startswith("vienna-"):
            continue  # held out: not even opened
        try:
            raw = load_yaml(path)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            print(f"skipped  {path.name}: not valid YAML (line {mark.line + 1 if mark else '?'})")
            continue
        if raw.get("split") != "dev" or "violations" not in raw:
            continue
        if NEW_KEYS & raw.keys():
            print(f"already  {path.name}")
            continue
        converted, dropped = convert(raw)
        try:
            text = format_itinerary(yaml.safe_dump(converted, allow_unicode=True, sort_keys=False))
        except LayoutError as e:
            print(f"skipped  {path.name}: {e}")
            continue
        changes[path] = text
        n_old, n_new = len(raw["violations"]), len(converted["violations"])
        print(f"\n{path.name}: {n_old} old label(s) → {n_new}")
        for v in converted["violations"]:
            print(f"  {v}")
        for d in dropped:
            print(f"  dropped: {d}")
        print("  verified_by_human: false (old labels covered 3 checks, not travel_time)")

    if args.write and changes:
        backup = args.dir.parent / f"dataset-backup-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copytree(args.dir / "itineraries", backup / "itineraries")
        print(f"\nbackup   {backup}")
        for path, text in changes.items():
            path.write_text(text, encoding="utf-8")
    print(f"\n{len(changes)} file(s) {'rewritten' if args.write else 'to rewrite'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
