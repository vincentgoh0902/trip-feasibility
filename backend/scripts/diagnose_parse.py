"""Where extraction differs from the gold stops, itinerary by itinerary (dev split only).

    uv run python scripts/diagnose_parse.py                       # every dev file with gold stops
    uv run python scripts/diagnose_parse.py --ids paris-02,london-08 --samples 3
    uv run python scripts/diagnose_parse.py --summary             # counts only

For parser debugging (gold stops are dev only and frozen, decision 74). Each file's cleaned
text goes through parse step 1 with the current prompt and settings; the result is aligned
with the gold stops (evals/align.py) and printed: gold stops missed, parsed stops with no gold
match, and the fields that differ on matched pairs. Extractions are cached in
data/evals/extractions/ per text, prompt version, model, effort and sample number, so a rerun
costs nothing; --samples N asks N times (the model varies), to tell a systematic miss from
noise. Test files (Vienna included) are never read here.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter

from trip_feasibility import paths
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.models import DatasetItinerary
from trip_feasibility.dataset.validate import validate_dataset
from trip_feasibility.evals.align import align
from trip_feasibility.evals.score import PARSE_FIELDS
from trip_feasibility.llm import make_client
from trip_feasibility.parse.clean import clean_text
from trip_feasibility.parse.extract import (
    EXTRACTION_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    ExtractionError,
    extract_stops,
)
from trip_feasibility.parse.schema import ExtractedStop, Extraction

CACHE = paths.EVALS / "extractions"


def _stop(s: ExtractedStop) -> str:
    when = "-".join(str(t)[:5] for t in (s.start_time, s.end_time) if t) or s.time_of_day or "-"
    extra = [f"{s.duration_min}-{s.duration_max}min"] if s.duration_min else []
    if s.travel_min is not None or s.travel_start:
        extra.append(f"travel {s.travel_start or ''} {s.travel_min}-{s.travel_max}")
    if not s.located:
        extra.append("not located")
    return f"day {s.day} {when:11} {s.type:8} {s.place_name!r} {' '.join(extra)}".rstrip()


async def extraction(client, itin: DatasetItinerary, sample: int, settings) -> Extraction | str:
    text = clean_text(itin.text)
    # the prompt's text is in the key too, so an edit without a version bump can't hit the cache
    prompt = SYSTEM_PROMPT + json.dumps(EXTRACTION_SCHEMA, sort_keys=True)
    key = hashlib.sha256(
        f"{text}|{prompt}|{settings.extraction_model}|{settings.extraction_effort}".encode()
    ).hexdigest()[:12]
    path = CACHE / f"{itin.id}__{PROMPT_VERSION}__{key}__{sample}.json"
    if path.exists():
        return Extraction.model_validate_json(path.read_text())
    try:
        result = await extract_stops(
            client,
            text,
            city=itin.city.title(),
            trip_start=itin.trip_start,
            model=settings.extraction_model,
            effort=settings.extraction_effort,
        )
    except ExtractionError as e:
        return f"{e.code}: {e}"
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(result.extraction.model_dump_json(indent=1))
    return result.extraction


def compare(parsed: list[ExtractedStop], gold: list[ExtractedStop]) -> dict:
    pairs = align(parsed, gold)
    matched = set(pairs.values())

    def wrong(p: ExtractedStop, g: ExtractedStop) -> list[str]:
        return [f for f in PARSE_FIELDS if getattr(p, f) != getattr(g, f)]

    return {
        "missed": [gold[j] for j in range(len(gold)) if j not in matched],
        "extra": [parsed[i] for i in range(len(parsed)) if i not in pairs],
        "diffs": [
            (parsed[i], gold[j], wrong(parsed[i], gold[j]))
            for i, j in sorted(pairs.items(), key=lambda p: p[1])
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ids", default="", help="comma-separated dev ids")
    parser.add_argument("--samples", type=int, default=1, help="extractions per file")
    parser.add_argument("--summary", action="store_true", help="counts only")
    args = parser.parse_args()
    settings = get_settings()
    if settings.anthropic_api_key is None:
        print("Needs ANTHROPIC_API_KEY (repo-root .env), unless every extraction is cached.")
    report = validate_dataset(paths.DATASET, partial=True)
    ids = set(filter(None, args.ids.split(",")))
    dev = [i for i in report.itineraries if i.split == "dev" and i.stops is not None]
    if ids - {i.id for i in dev}:
        unknown = ", ".join(sorted(ids - {i.id for i in dev}))
        parser.error(f"not dev files with gold stops: {unknown}")
    chosen = [i for i in dev if not ids or i.id in ids]
    client = make_client(settings) if settings.anthropic_api_key else None
    totals: Counter = Counter()
    field_errors: Counter = Counter()
    for itin in chosen:
        gold = [ExtractedStop(**{f: getattr(s, f) for f in PARSE_FIELDS}) for s in itin.stops]
        for n in range(1, args.samples + 1):
            got = await extraction(client, itin, n, settings)
            label = f"{itin.id} (sample {n})" if args.samples > 1 else itin.id
            if isinstance(got, str):
                print(f"\n{label}: extraction failed: {got}")
                totals["failed"] += 1
                continue
            c = compare(got.stops, gold)
            wrong = [d for d in c["diffs"] if d[2]]
            totals.update(
                gold=len(gold), parsed=len(got.stops), missed=len(c["missed"]),
                extra=len(c["extra"]), matched_with_diffs=len(wrong),
            )  # fmt: skip
            for _, _, fields in wrong:
                field_errors.update(fields)
            if args.summary:
                print(f"{label:24} gold {len(gold):3}  parsed {len(got.stops):3}  "
                      f"missed {len(c['missed']):2}  extra {len(c['extra']):2}  "
                      f"fields wrong on {len(wrong)}")  # fmt: skip
                continue
            print(f"\n== {label}: {len(gold)} gold, {len(got.stops)} parsed")
            for s in c["missed"]:
                print(f"  MISSED  {_stop(s)}")
            for s in c["extra"]:
                print(f"  EXTRA   {_stop(s)}")
            for p, g, fields in wrong:
                print(f"  DIFF    {', '.join(fields)}\n          gold   {_stop(g)}\n"
                      f"          parsed {_stop(p)}")  # fmt: skip
    print(f"\nTotals ({PROMPT_VERSION}): {dict(totals)}")
    print(f"Fields wrong on matched stops: {dict(field_errors.most_common())}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
