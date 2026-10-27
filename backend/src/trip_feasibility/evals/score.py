"""Scoring: predicted violations vs gold labels, and parsed stops vs gold stops.

Predictions are put in the gold labels' terms (validate.GoldViolation): check, day and the
place as the system names it (opening_hours and travel_time; travel_time names the place
travelled to), no place for public_holidays and daily_load, no day for hotel_red_flags.
Only warning and blocker count, as in the labels. A prediction matches a label with the
same check and day and a matching place name (evals/align.name_score), one to one,
whatever the severity; severity agreement is reported separately. A prediction matching
none of the labels but one of the file's `unknowns` isn't counted. Counts are summed over
itineraries (micro-averaged).
"""

from dataclasses import dataclass, field

from trip_feasibility.checks.models import SEVERITY_RANK, Violation
from trip_feasibility.dataset.models import Unknown
from trip_feasibility.dataset.validate import GoldViolation
from trip_feasibility.evals.align import name_score
from trip_feasibility.itinerary import Stop
from trip_feasibility.parse.schema import ExtractedStop

PARSE_FIELDS = tuple(ExtractedStop.model_fields)
# place names at least this close match without asking (align.name_score: 0.9 is one name
# inside the other, "Louvre" in "Louvre Museum")
MATCH_SCORE = 0.9


@dataclass(frozen=True)
class Finding:
    """A predicted violation in the labels' terms."""

    check: str
    severity: str
    day: int | None
    place: str | None
    from_place: str | None = None

    def label(self) -> str:
        where = " → ".join(p for p in (self.from_place, self.place) if p)
        return f"{self.check} day {self.day or '-'}{': ' + where if where else ''}"


def gold_label(g: GoldViolation) -> str:
    return Finding(g.check, g.severity, g.day, g.place, g.from_place).label()


def findings(violations: list[Violation], stops: list[Stop]) -> list[Finding]:
    """A run's warnings and blockers in the labels' terms, one per target (the worst
    severity wins)."""
    by_id = {s.id: s for s in stops}
    out: dict[tuple, Finding] = {}

    def add(f: Finding) -> None:
        key = (f.check, f.day, f.place, f.from_place)
        if key not in out or SEVERITY_RANK[f.severity] > SEVERITY_RANK[out[key].severity]:
            out[key] = f

    for v in violations:
        named = [by_id[s] for s in v.stop_ids if s in by_id]
        if v.severity == "info" or not named:
            continue
        match v.check_name:
            case "opening_hours":
                for s in named:
                    add(Finding(v.check_name, v.severity, s.day, s.place_name))
            case "travel_time":
                a, b = named[0], named[-1]
                from_place = a.place_name if a is not b else None
                add(Finding(v.check_name, v.severity, b.day, b.place_name, from_place))
            case "public_holidays" | "daily_load":
                add(Finding(v.check_name, v.severity, named[0].day, None))
            case "hotel_red_flags":
                add(Finding(v.check_name, v.severity, None, named[0].place_name))
    return list(out.values())


def place_score(a: str | None, b: str | None) -> float:
    if a is None or b is None:
        return 1.0 if a is b else 0.0
    return name_score(a, b)


def match(predicted: list[Finding], gold: list[GoldViolation]) -> dict[int, int]:
    """predicted index -> gold index: same check and day, best place names first."""
    candidates = []
    for i, p in enumerate(predicted):
        for j, g in enumerate(gold):
            if p.check != g.check or p.day != g.day:
                continue
            score = place_score(p.place, g.place)
            if score >= MATCH_SCORE:
                candidates.append((-score, i, j))
    pairs: dict[int, int] = {}
    used: set[int] = set()
    for _, i, j in sorted(candidates):
        if i not in pairs and j not in used:
            pairs[i] = j
            used.add(j)
    return pairs


def _is_unknown(p: Finding, unknowns: list[Unknown]) -> bool:
    return any(
        u.check == p.check
        and (u.day is None or u.day == p.day)
        and (u.place is None or place_score(p.place, u.place) >= MATCH_SCORE)
        for u in unknowns
    )


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    severity_agree: int = 0  # among tp

    def add(self, other: "Counts") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.severity_agree += other.severity_agree

    def metrics(self) -> dict[str, float | int | None]:
        p = self.tp / (self.tp + self.fp) if self.tp + self.fp else None
        r = self.tp / (self.tp + self.fn) if self.tp + self.fn else None
        f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": p,
            "recall": r,
            "f1": f1,
            "severity_agreement": self.severity_agree / self.tp if self.tp else None,
        }


def compare(
    predicted: list[Finding], gold: list[GoldViolation], unknowns: list[Unknown] = ()
) -> dict[str, Counts]:
    """Counts per check."""
    counts: dict[str, Counts] = {}
    pairs = match(predicted, gold)
    for i, p in enumerate(predicted):
        c = counts.setdefault(p.check, Counts())
        if i in pairs:
            c.tp += 1
            c.severity_agree += p.severity == gold[pairs[i]].severity
        elif not _is_unknown(p, list(unknowns)):
            c.fp += 1
    matched = set(pairs.values())
    for j, g in enumerate(gold):
        if j not in matched:
            counts.setdefault(g.check, Counts()).fn += 1
    return counts


@dataclass
class ParseCounts:
    gold_stops: int = 0
    parsed_stops: int = 0
    aligned: int = 0
    fields_correct: int = 0
    exact_stops: int = 0  # aligned stops with every field right
    field_errors: dict[str, int] = field(default_factory=dict)

    def add(self, other: "ParseCounts") -> None:
        self.gold_stops += other.gold_stops
        self.parsed_stops += other.parsed_stops
        self.aligned += other.aligned
        self.fields_correct += other.fields_correct
        self.exact_stops += other.exact_stops
        for k, v in other.field_errors.items():
            self.field_errors[k] = self.field_errors.get(k, 0) + v

    def metrics(self) -> dict:
        fields = self.aligned * len(PARSE_FIELDS)
        return {
            "gold_stops": self.gold_stops,
            "parsed_stops": self.parsed_stops,
            "stop_recall": self.aligned / self.gold_stops if self.gold_stops else None,
            "stop_precision": self.aligned / self.parsed_stops if self.parsed_stops else None,
            "field_accuracy": self.fields_correct / fields if fields else None,
            "exact_stop_rate": self.exact_stops / self.gold_stops if self.gold_stops else None,
            "field_errors": self.field_errors,
        }


def parse_counts(
    parsed: list[ExtractedStop], gold: list[ExtractedStop], pairs: dict[int, int]
) -> ParseCounts:
    c = ParseCounts(gold_stops=len(gold), parsed_stops=len(parsed), aligned=len(pairs))
    for i, j in pairs.items():
        wrong = [f for f in PARSE_FIELDS if getattr(parsed[i], f) != getattr(gold[j], f)]
        c.fields_correct += len(PARSE_FIELDS) - len(wrong)
        c.exact_stops += not wrong
        for f in wrong:
            c.field_errors[f] = c.field_errors.get(f, 0) + 1
    return c
