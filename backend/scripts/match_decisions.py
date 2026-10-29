"""Decide uncertain place-name matches between predictions and labels (evals/decisions.py).

    uv run python scripts/match_decisions.py                  # list undecided pairs (dev)
    uv run python scripts/match_decisions.py --ask            # decide them one by one
    uv run python scripts/match_decisions.py --from-baseline  # also collect the baseline's pairs
    uv run python scripts/match_decisions.py --ask --final    # test pairs, after the final run

Pairs are collected by run_eval.py (our system) and --from-baseline (the cached LLM-only
baseline outputs, scored against the verified gold here). Each decision is saved at once in
data/evals/match_decisions.yaml and applies to every later run and system. Test-split pairs
(Vienna included) are neither listed nor asked without --final: deciding them shows the
test predictions.
"""

import argparse
import sys
from pathlib import Path

from trip_feasibility.dataset.models import DatasetItinerary
from trip_feasibility.dataset.validate import Report, gold_violations, validate_dataset
from trip_feasibility.evals.baseline import BASELINE_PROMPT_VERSION, load_cached
from trip_feasibility.evals.decisions import MatchDecisions, Pair
from trip_feasibility.evals.score import Where, compare

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data" / "dataset"
DECISIONS = ROOT / "data" / "evals" / "match_decisions.yaml"
BASELINE = ROOT / "data" / "evals" / "baseline"


def collect_baseline(report: Report, decisions: MatchDecisions, split: str) -> int:
    """Score every cached baseline output for the split's verified files; returns how many."""
    hotels = {h.key: h for h in report.hotels}
    scored = 0
    for itin in report.itineraries:
        if itin.split != split or not itin.annotated:
            continue
        for cache in sorted(BASELINE.glob(f"{itin.id}__*__{BASELINE_PROMPT_VERSION}.json")):
            result = load_cached(itin, cache)
            if result is None:
                continue  # inputs changed since it ran: rerun scripts/run_baseline.py
            gold = [g for g in gold_violations(itin, hotels) if g.check != "hotel_red_flags"]
            compare(result.findings, gold, result.unknowns, Where(itin.id, itin.split, decisions))
            scored += 1
    return scored


def evidence(itins: dict[str, DatasetItinerary], pair: Pair) -> str:
    itin = itins.get(pair.itinerary)
    for v in (itin.violations or []) if itin else []:
        if v.check == pair.check and v.day == pair.day and v.place == pair.gold:
            return v.evidence
    return ""


def ask(report: Report, decisions: MatchDecisions, pairs: list[Pair]) -> None:
    itins = {i.id: i for i in report.itineraries}
    print("For each pair: is the predicted place the labelled one? y / n / s(kip) / q(uit)\n")
    for n, pair in enumerate(pairs, 1):
        print(f"[{n}/{len(pairs)}] {pair.itinerary}, {pair.check}, day {pair.day} "
              f"(name score {pair.score})")  # fmt: skip
        print(f"  predicted: {pair.predicted}")
        print(f"  label:     {pair.gold}")
        if why := evidence(itins, pair):
            print(f"  label evidence: {why}")
        while (answer := input("  match? ").strip().lower()) not in {"y", "n", "s", "q"}:
            pass
        if answer == "q":
            break
        if answer in {"y", "n"}:
            decisions.set(pair, answer == "y")
            decisions.save()
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ask", action="store_true", help="decide the undecided pairs")
    parser.add_argument("--from-baseline", action="store_true", help="collect the baseline's pairs")
    parser.add_argument(
        "--final", action="store_true", help="test-split pairs (after the final run)"
    )
    args = parser.parse_args()
    split = "test" if args.final else "dev"

    report = validate_dataset(DATASET, partial=True)
    decisions = MatchDecisions.load(DECISIONS)
    if args.from_baseline:
        n = collect_baseline(report, decisions, split)
        decisions.save()
        print(f"scored {n} cached baseline outputs ({split})")
    pending = [p for p in decisions.pending.values() if p.split == split]
    if not pending:
        print(f"No undecided {split} pairs.")
        return 0
    if args.ask:
        if not sys.stdin.isatty():
            print("--ask needs a terminal; or edit match: null in", DECISIONS, file=sys.stderr)
            return 1
        ask(report, decisions, pending)
        left = sum(p.split == split for p in decisions.pending.values())
        print(f"{left} undecided {split} pairs left.")
        return 0
    print(f"{len(pending)} undecided {split} pairs (decide with --ask, or edit {DECISIONS}):")
    for p in pending:
        print(f"  {p.itinerary:13} {p.check:13} day {p.day}: {p.predicted!r} vs {p.gold!r} "
              f"({p.score})")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
