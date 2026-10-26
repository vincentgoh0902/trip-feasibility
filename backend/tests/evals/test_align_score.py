from trip_feasibility.checks.models import Violation
from trip_feasibility.evals.align import align
from trip_feasibility.evals.score import (
    PARSE_FIELDS,
    Counts,
    compare,
    parse_counts,
    predicted_targets,
)
from trip_feasibility.parse.schema import ExtractedStop


def s(day, name, **kw):
    return ExtractedStop(day=day, place_name=name, type=kw.pop("type", "venue"), **kw)


def test_align_by_day_and_name_with_order_breaking_ties():
    hotel = {"type": "hotel"}
    gold = [s(1, "Hôtel Regina", **hotel), s(1, "Louvre Museum"), s(1, "Hôtel Regina", **hotel),
            s(2, "Musée d'Orsay")]  # fmt: skip
    parsed = [s(1, "Hotel Regina", **hotel), s(1, "The Louvre Museum"),
              s(1, "Hôtel Regina", **hotel), s(1, "Musée d'Orsay"),
              s(2, "Sainte-Chapelle")]  # fmt: skip
    # Orsay is on the wrong day and Sainte-Chapelle isn't in the gold stops: both unaligned
    assert align(parsed, gold) == {0: 0, 1: 1, 2: 2}


def test_parse_counts():
    gold = [s(1, "Louvre", start_time="09:00", end_time="12:00"), s(1, "Orsay")]
    parsed = [s(1, "Louvre", start_time="09:00", end_time="11:00"), s(1, "Extra")]
    c = parse_counts(parsed, gold, {0: 0})
    m = c.metrics()
    assert (m["stop_recall"], m["stop_precision"]) == (0.5, 0.5)
    assert m["field_errors"] == {"end_time": 1}
    assert m["field_accuracy"] == (len(PARSE_FIELDS) - 1) / len(PARSE_FIELDS)
    assert m["exact_stop_rate"] == 0.0


def v(check, severity, *stop_ids):
    return Violation(check_name=check, severity=severity, stop_ids=list(stop_ids), message="m",
                     evidence={}, source="x")  # fmt: skip


def test_predictions_in_label_terms():
    days = {"p1": 1, "p2": 1, "p3": 2}
    ids = {"p1": "s1", "p2": "s2"}  # p3 wasn't aligned
    hotels = {"s1": "paris-regina"}
    got = predicted_targets(
        [
            v("opening_hours", "blocker", "p2"),
            v("travel_time", "warning", "p1", "p2"),
            v("travel_time", "blocker", "p2", "p3"),
            v("daily_load", "warning", "p1", "p2"),
            v("public_holidays", "info", "p3"),  # info never counts
            v("hotel_red_flags", "warning", "p1"),
            v("opening_hours", "warning", "p2"),  # same target: the worst severity wins
        ],
        gold_id=ids.get,
        day_of=days.__getitem__,
        hotel_of=lambda p: hotels.get(ids.get(p, "")),
    )
    assert got == {
        ("opening_hours", ("stop", "s2")): "blocker",
        ("travel_time", ("leg", "s1", "s2")): "warning",
        ("travel_time", ("leg", "s2", "unaligned:p3")): "blocker",
        ("daily_load", ("day", "1")): "warning",
        ("hotel_red_flags", ("hotel", "paris-regina")): "warning",
    }


def test_compare_and_metrics():
    gold = {
        ("opening_hours", ("stop", "s2")): "blocker",
        ("travel_time", ("leg", "s3", "s4")): "warning",
    }
    pred = {("opening_hours", ("stop", "s2")): "warning", ("daily_load", ("day", "1")): "warning"}
    counts = compare(pred, gold)
    oh, tt, dl = counts["opening_hours"], counts["travel_time"], counts["daily_load"]
    assert (oh.tp, oh.severity_agree, tt.fn, dl.fp) == (1, 0, 1, 1)
    total = Counts()
    for c in counts.values():
        total.add(c)
    m = total.metrics()
    assert (m["precision"], m["recall"], m["severity_agreement"]) == (0.5, 0.5, 0.0)
    assert Counts().metrics()["precision"] is None  # nothing predicted: undefined, not 0
