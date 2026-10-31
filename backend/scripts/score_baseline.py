"""Score the cached LLM-only baseline outputs against the verified gold (dev split).

    uv run python scripts/score_baseline.py

Reads what scripts/run_baseline.py cached for the current BASELINE_PROMPT_VERSION; files
whose inputs changed since then are listed as stale (rerun the baseline). Hotel red flags
are left out: the baseline doesn't check hotels. Uncertain place-name pairs use the human
decisions (scripts/match_decisions.py --from-baseline --ask); undecided ones make the
numbers provisional. Prints only; record kept runs in docs/EVALS.md.
"""

import sys
from pathlib import Path

from trip_feasibility.dataset.validate import gold_violations, validate_dataset
from trip_feasibility.evals.baseline import BASELINE_PROMPT_VERSION, load_cached
from trip_feasibility.evals.decisions import MatchDecisions
from trip_feasibility.evals.score import Counts, Where, compare

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data" / "dataset"
BASELINE = ROOT / "data" / "evals" / "baseline"
DECISIONS = ROOT / "data" / "evals" / "match_decisions.yaml"


def _of(n: int, total: int) -> str:
    return f"{n} of {total} ({n / total:.0%})" if total else "n/a"


def main() -> int:
    report = validate_dataset(DATASET, partial=True)
    if report.errors:
        print("Fix the dataset first (scripts/validate_dataset.py).", file=sys.stderr)
        return 1
    hotels = {h.key: h for h in report.hotels}
    decisions = MatchDecisions.load(DECISIONS)
    per_check: dict[str, Counts] = {}
    scored, stale, missing = [], [], []
    for itin in report.itineraries:
        if itin.split != "dev" or not itin.annotated:
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
        for check, c in compare(result.findings, gold, result.unknowns, where).items():
            per_check.setdefault(check, Counts()).add(c)
        scored.append(itin.id)
    decisions.save()

    print(f"{BASELINE_PROMPT_VERSION}: {len(scored)} dev itineraries, dataset {report.version}")
    print(f"  {'check':16} {'precision':>18} {'recall':>18} {'severity':>9}")
    overall = Counts()
    for check in sorted(per_check):
        overall.add(per_check[check])
    for check, c in {**dict(sorted(per_check.items())), "ALL": overall}.items():
        agree = f"{c.severity_agree / c.tp:.0%}" if c.tp else "-"
        print(f"  {check:16} {_of(c.tp, c.tp + c.fp):>18} {_of(c.tp, c.tp + c.fn):>18} "
              f"{agree:>9}")  # fmt: skip
    if missing or stale:
        print(f"  not scored: no output {missing or '-'}, stale {stale or '-'}")
    if pending := sum(p.itinerary in scored for p in decisions.pending.values()):
        print(f"  provisional: {pending} place-name pairs need your decision "
              f"(scripts/match_decisions.py --from-baseline --ask)")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
