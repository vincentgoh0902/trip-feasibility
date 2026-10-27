"""Draft gold violations for dev itineraries with a search-enabled LLM (dataset/draft.py).

    uv run python scripts/draft_gold.py                       # every dev file with no gold yet
    uv run python scripts/draft_gold.py --ids paris-08,london-04
    uv run python scripts/draft_gold.py --replace-unverified  # also redo unverified gold
    uv run python scripts/draft_gold.py --prompt paris-08     # print the prompt, run nothing
    uv run python scripts/draft_gold.py --backend codex --model gpt-6-sol --effort high

Dev only; Vienna files are never opened. Files with verified gold are never touched. Each
draft is written as soon as it's done (`drafted_by` = model, tool and prompt version;
`verified_by_human: false`), after one backup of itineraries/. The raw outputs, with usage
and search counts, go to data/dataset-drafts/ (outside the dataset, so they don't change
its version). Check each draft against reality, then set `verified_by_human: true`.
"""

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from trip_feasibility.dataset.draft import (
    DRAFT_PROMPT_VERSION,
    apply_draft,
    build_prompt,
    draft_itinerary,
)
from trip_feasibility.dataset.layout import LayoutError, format_itinerary
from trip_feasibility.dataset.models import DatasetItinerary
from trip_feasibility.dataset.validate import load_yaml, text_path
from trip_feasibility.evals.search_llm import BACKENDS, SearchLLMError, make_backend

DATA = Path(__file__).resolve().parents[2] / "data"
DEFAULT_DIR = DATA / "dataset"
RAW_OUT = DATA / "dataset-drafts"


def load(root: Path, path: Path) -> tuple[dict, DatasetItinerary]:
    raw = load_yaml(path)
    text = text_path(root, path.stem).read_text(encoding="utf-8").rstrip()
    return raw, DatasetItinerary.model_validate(raw | {"text": text})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--ids", default="", help="comma-separated dev ids")
    parser.add_argument("--replace-unverified", action="store_true")
    parser.add_argument("--prompt", metavar="ID", help="print one file's prompt and stop")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="codex")
    parser.add_argument("--model", default="gpt-6-sol")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--parallel", type=int, default=3)
    args = parser.parse_args()

    ids = set(filter(None, args.ids.split(",")))
    if args.prompt:
        ids = {args.prompt}
    if any(i.startswith("vienna-") for i in ids):
        parser.error("Vienna is held out: never drafted here")

    chosen: list[tuple[Path, dict, DatasetItinerary]] = []
    for path in sorted((args.dir / "itineraries").glob("*.yaml")):
        if path.stem.startswith("vienna-") or (ids and path.stem not in ids):
            continue  # held out: not even opened
        try:
            raw, itin = load(args.dir, path)
        except (yaml.YAMLError, ValueError, OSError) as e:
            print(f"skipped  {path.name}: doesn't load ({type(e).__name__}); fix it first")
            continue
        if itin.split != "dev":
            if ids:
                print(f"skipped  {path.name}: test split (needs Vincent's go-ahead)")
            continue
        if itin.violations is not None and (itin.verified_by_human or not args.replace_unverified):
            if ids:
                state = "verified" if itin.verified_by_human else "has an unverified draft"
                print(f"skipped  {path.name}: {state}")
            continue
        chosen.append((path, raw, itin))

    if args.prompt:
        if not chosen:
            return 1
        print(build_prompt(chosen[0][2]))
        return 0
    if not chosen:
        print("Nothing to draft.")
        return 0

    llm = make_backend(args.backend, args.model, args.effort)
    if llm.provider == "anthropic":
        parser.error("drafts never come from Anthropic models (Claude is the extractor)")
    backup = args.dir.parent / f"dataset-backup-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(args.dir / "itineraries", backup / "itineraries")
    print(f"backup   {backup}")
    RAW_OUT.mkdir(exist_ok=True)
    print(f"drafting {len(chosen)} file(s) with {args.backend}/{args.model}, "
          f"{args.parallel} at a time")  # fmt: skip
    return asyncio.run(run_all(llm, chosen, args.parallel))


async def run_all(llm, chosen, parallel: int) -> int:
    gate = asyncio.Semaphore(parallel)
    failed = 0

    async def one(path: Path, raw: dict, itin: DatasetItinerary) -> None:
        nonlocal failed
        async with gate:
            try:
                draft = await draft_itinerary(llm, itin)
            except SearchLLMError as e:
                print(f"failed   {itin.id}: {e}")
                failed += 1
                return
        drafted_by = f"{llm.label}, {DRAFT_PROMPT_VERSION}"
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        (RAW_OUT / f"{itin.id}-{stamp}.json").write_text(
            json.dumps(
                {
                    "id": itin.id,
                    "drafted_by": drafted_by,
                    "dropped": draft.dropped,
                    "runs": [asdict(r) for r in draft.runs],
                },
                ensure_ascii=False,
                indent=1,
            )  # fmt: skip
        )
        try:
            text = format_itinerary(
                yaml.safe_dump(
                    apply_draft(raw, draft, drafted_by), allow_unicode=True, sort_keys=False
                )  # fmt: skip
            )
        except LayoutError as e:
            print(f"failed   {itin.id}: can't write the file ({e}); raw output saved")
            failed += 1
            return
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
        searches = sum(r.searches for r in draft.runs)
        minutes = sum(r.seconds for r in draft.runs) / 60
        print(f"drafted  {itin.id}: {len(draft.violations)} violations, "
              f"{len(draft.unknowns)} unknowns, {len(draft.other_issues)} other issues"
              f"{f', {len(draft.dropped)} dropped' if draft.dropped else ''} "
              f"({len(draft.runs)} run(s), {searches} searches, {minutes:.1f} min)")  # fmt: skip

    await asyncio.gather(*(one(*c) for c in chosen))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
