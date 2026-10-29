"""Human decisions on uncertain place-name matches (score.match).

A prediction and a label with the same check and day match automatically when their place
names score at least score.MATCH_SCORE, and never below UNSURE_SCORE. In between ("Musée
d'Orsay" vs "Orsay Museum", "Sagrada Familia" vs "Sagrada Família tour") a human decides,
once: the decision is saved and reused by every later run and every system (ours, the
baseline), since it's about the two names, not who produced them.

Saved as a YAML list in data/evals/match_decisions.yaml (gitignored, next to the runs;
back it up with the dataset). Pairs no one has decided yet are written with `match: null`
and count as no match, so a score with undecided pairs is provisional and says so. Pairs
from the test split are kept apart: listed and asked only with --final, after the final
run (deciding them means seeing the test predictions).
"""

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

UNSURE_SCORE = 0.4  # below this, place names never match ("Musée d'Orsay" vs "Orsay Museum": 0.42)


@dataclass(frozen=True)
class Pair:
    itinerary: str
    split: str
    check: str
    day: int | None
    predicted: str
    gold: str
    score: float

    @property
    def key(self) -> tuple:
        return (self.itinerary, self.check, self.day, self.predicted, self.gold)


class MatchDecisions:
    """Decisions by pair; undecided pairs met while scoring are collected in `pending`."""

    def __init__(self, decided: dict[tuple, bool] | None = None, path: Path | None = None):
        self.decided: dict[tuple, bool] = dict(decided or {})
        self.pending: dict[tuple, Pair] = {}
        self.path = path
        self._rows: dict[tuple, dict] = {}

    @classmethod
    def load(cls, path: Path) -> "MatchDecisions":
        out = cls(path=path)
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
        for row in rows or []:
            pair = Pair(**{k: row[k] for k in Pair.__dataclass_fields__})
            out._rows[pair.key] = row
            if row.get("match") is None:
                out.pending[pair.key] = pair
            else:
                out.decided[pair.key] = bool(row["match"])
        return out

    def decide(self, pair: Pair) -> bool | None:
        """True or False once decided; None (and remembered as pending) until then."""
        if pair.key in self.decided:
            return self.decided[pair.key]
        self.pending.setdefault(pair.key, pair)
        return None

    def set(self, pair: Pair, match: bool) -> None:
        self.decided[pair.key] = match
        self.pending.pop(pair.key, None)

    def rows(self) -> list[dict]:
        rows = dict(self._rows)
        for key, pair in self.pending.items():
            rows.setdefault(key, {**asdict(pair), "match": None})
        for key, match in self.decided.items():
            if key in rows:
                rows[key] = {**rows[key], "match": match}
        return sorted(rows.values(), key=lambda r: (r["itinerary"], r["check"], r["day"] or 0))

    def save(self, path: Path | None = None) -> None:
        path = path or self.path
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Is the predicted place the labelled one? match: true / false; null = not decided.\n"
            "# Written by run_eval.py and match_decisions.py; edit by hand or with --ask.\n"
        )
        body = yaml.safe_dump(self.rows(), allow_unicode=True, sort_keys=False, width=100)
        path.write_text(header + body, encoding="utf-8")

    def version(self) -> str:
        """Hash of the decisions, stored with each eval run (they change the scores)."""
        decided = sorted((list(k), v) for k, v in self.decided.items())
        return hashlib.sha256(repr(decided).encode()).hexdigest()[:12]
