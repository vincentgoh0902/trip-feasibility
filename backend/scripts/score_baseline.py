"""Score the cached LLM-only baseline outputs against the verified gold.

    uv run python scripts/score_baseline.py                       # dev split
    uv run python scripts/score_baseline.py --split test --final  # reported results only

Reads what scripts/run_baseline.py cached for the current BASELINE_PROMPT_VERSION; files
whose inputs changed since then are listed as stale (rerun the baseline). Hotel red flags
are left out: the baseline doesn't check hotels. Uncertain place-name pairs use the human
decisions (scripts/match_decisions.py --from-baseline --ask); undecided ones make the
numbers provisional. Prints only; record kept runs in docs/EVALS.md. The test split needs
--final and prints no itinerary ids; its seen cities and the unseen one (Vienna) are also
reported apart.
"""

import argparse
import sys

from trip_feasibility import paths
from trip_feasibility.dataset.split import UNSEEN_CITY
from trip_feasibility.dataset.validate import gold_violations, validate_dataset
from trip_feasibility.evals.baseline import BASELINE_PROMPT_VERSION, load_cached
from trip_feasibility.evals.decisions import MatchDecisions
from trip_feasibility.evals.score import Counts, Where, add_by_cause, compare, found_by_cause

DATASET = paths.DATASET
BASELINE = paths.BASELINE
DECISIONS = paths.MATCH_DECISIONS


def _of(n: int, total: int) -> str:
    return f"{n} of {total} ({n / total:.0%})" if total else "n/a"


def print_counts(label: str, per_check: dict[str, Counts]) -> None:
    print(label)
    print(f"  {'check':16} {'precision':>18} {'recall':>18} {'severity':>9}")
    overall = Counts()
    for check in sorted(per_check):
        overall.add(per_check[check])
    for check, c in {**dict(sorted(per_check.items())), "ALL": overall}.items():
        agree = f"{c.severity_agree / c.tp:.0%}" if c.tp else "-"
        print(f"  {check:16} {_of(c.tp, c.tp + c.fp):>18} {_of(c.tp, c.tp + c.fn):>18} "
              f"{agree:>9}")  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--final", action="store_true", help="required for --split test")
    args = parser.parse_args()
    if args.split == "test" and not args.final:
        parser.error("the test split is only for reported results: add --final")
    test = args.split == "test"
    report = validate_dataset(DATASET, partial=True)
    # dev only: test files in progress don't block it; the test split needs every file valid
    if report.errors if test else report.outside_test:
        print("Fix the dataset first (scripts/validate_dataset.py).", file=sys.stderr)
        return 1
    hotels = {h.key: h for h in report.hotels}
    decisions = MatchDecisions.load(DECISIONS)
    per_city: dict[bool, dict[str, Counts]] = {True: {}, False: {}}  # unseen city?
    by_cause: dict[str, list[int]] = {}
    scored, stale, missing = [], [], []
    for itin in report.itineraries:
        if itin.split != args.split or not itin.annotated:
            continue
        caches = sorted(BASELINE.glob(f"{itin.id}__*__{BASELINE_PROMPT_VERSION}.json"))
        if not caches:
            missing.append(itin.id)
            continue
        result = load_cached(itin, caches[0])
        if result is None:
            stale.append(itin.id)
            continue
        gold = [g for g in gold_violations(itin, hotels) if g.check != "hotel_red_flags"]
        where = Where(itin.id, itin.split, decisions)
        counts = per_city[itin.city == UNSEEN_CITY]
        for check, c in compare(result.findings, gold, itin.unknowns, where).items():
            counts.setdefault(check, Counts()).add(c)
        add_by_cause(by_cause, found_by_cause(result.findings, gold, where))
        scored.append(itin.id)
    decisions.save()

    per_check: dict[str, Counts] = {}
    for counts in per_city.values():
        for check, c in counts.items():
            per_check.setdefault(check, Counts()).add(c)
    print_counts(
        f"{BASELINE_PROMPT_VERSION}: {len(scored)} {args.split} itineraries, "
        f"dataset {report.version}",
        per_check,
    )
    if by_cause:
        parts = [f"{c} {_of(f, n)}" for c, (f, n) in sorted(by_cause.items())]
        print(f"  opening hours found, by cause: {', '.join(parts)}")
    if per_city[True]:
        print_counts("  seen cities (Paris, London, Barcelona)", per_city[False])
        print_counts("  unseen city (Vienna)", per_city[True])
    if missing or stale:
        if test:
            print(f"  not scored: {len(missing)} with no output, {len(stale)} stale")
        else:
            print(f"  not scored: no output {missing or '-'}, stale {stale or '-'}")
    if pending := sum(p.itinerary in scored for p in decisions.pending.values()):
        ask = "--from-baseline --ask" + (" --final" if test else "")
        print(f"  provisional: {pending} place-name pairs need your decision "
              f"(scripts/match_decisions.py {ask})")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
