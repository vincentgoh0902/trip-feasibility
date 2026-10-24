"""Scoring: predicted violations vs gold labels, and parsed stops vs gold stops.

Predictions are put in the gold labels' terms (validate.GoldViolation.target): a stop for
opening_hours, a leg for travel_time, a day for public_holidays and daily_load, a hotel key
for hotel_red_flags. Only warning and blocker count, as in the labels. A prediction matches
a label with the same check and target, whatever the severity; severity agreement is
reported separately. Counts are summed over itineraries (micro-averaged).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from trip_feasibility.checks.models import SEVERITY_RANK, Violation
from trip_feasibility.parse.schema import ExtractedStop

Target = tuple[str, ...]
PARSE_FIELDS = tuple(ExtractedStop.model_fields)


def predicted_targets(
    violations: list[Violation],
    *,
    gold_id: Callable[[str], str | None],
    day_of: Callable[[str], int],
    hotel_of: Callable[[str], str | None],
) -> dict[tuple[str, Target], str]:
    """(check, target) -> worst severity. gold_id maps a predicted stop id to the gold stop it
    was aligned with (identity in gold mode); unaligned stops give targets no label can match.
    """
    out: dict[tuple[str, Target], str] = {}

    def add(check: str, target: Target, severity: str) -> None:
        key = (check, target)
        if key not in out or SEVERITY_RANK[severity] > SEVERITY_RANK[out[key]]:
            out[key] = severity

    def stop(stop_id: str) -> str:
        return gold_id(stop_id) or f"unaligned:{stop_id}"

    for v in violations:
        if v.severity == "info" or not v.stop_ids:
            continue
        match v.check_name:
            case "opening_hours":
                for s in v.stop_ids:
                    add(v.check_name, ("stop", stop(s)), v.severity)
            case "travel_time":
                add(v.check_name, ("leg", stop(v.stop_ids[0]), stop(v.stop_ids[-1])), v.severity)
            case "public_holidays" | "daily_load":
                add(v.check_name, ("day", str(day_of(v.stop_ids[0]))), v.severity)
            case "hotel_red_flags":
                keys = {hotel_of(s) for s in v.stop_ids} - {None}
                for key in keys or {f"unaligned:{v.stop_ids[0]}"}:
                    add(v.check_name, ("hotel", key), v.severity)
    return out


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
    predicted: dict[tuple[str, Target], str], gold: dict[tuple[str, Target], str]
) -> dict[str, Counts]:
    """Counts per check."""
    counts: dict[str, Counts] = {}
    for check, target in predicted.keys() | gold.keys():
        c = counts.setdefault(check, Counts())
        key = (check, target)
        if key in predicted and key in gold:
            c.tp += 1
            c.severity_agree += predicted[key] == gold[key]
        elif key in predicted:
            c.fp += 1
        else:
            c.fn += 1
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
