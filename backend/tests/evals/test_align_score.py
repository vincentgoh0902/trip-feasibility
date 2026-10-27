from datetime import date

from trip_feasibility.checks.models import Violation
from trip_feasibility.dataset.models import Unknown
from trip_feasibility.dataset.validate import GoldViolation
from trip_feasibility.evals.align import align
from trip_feasibility.evals.score import (
    PARSE_FIELDS,
    Counts,
    Finding,
    compare,
    findings,
    parse_counts,
)
from trip_feasibility.itinerary import Stop
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


def stop(id, day, name, type="venue"):
    return Stop(id=id, day=day, date=date(2026, 12, 7 + day), place_name=name, type=type)


def gold(check, day, place, severity="warning", from_place=None):
    return GoldViolation(itinerary_id="x", check=check, severity=severity, day=day, place=place,
                         from_place=from_place, confidence="verified", derived=False)  # fmt: skip


def test_findings_in_label_terms():
    stops = [stop("p1", 1, "Hôtel Regina", "hotel"), stop("p2", 1, "Louvre"),
             stop("p3", 2, "Orsay")]  # fmt: skip
    got = findings(
        [
            v("opening_hours", "blocker", "p2"),
            v("travel_time", "warning", "p1", "p2"),
            v("daily_load", "warning", "p1", "p2"),
            v("public_holidays", "info", "p3"),  # info never counts
            v("hotel_red_flags", "warning", "p1"),
            v("opening_hours", "warning", "p2"),  # same target: the worst severity wins
            v("opening_hours", "warning", "gone"),  # names no stop: dropped
        ],
        stops,
    )
    assert set(got) == {
        Finding("opening_hours", "blocker", 1, "Louvre"),
        Finding("travel_time", "warning", 1, "Louvre", "Hôtel Regina"),
        Finding("daily_load", "warning", 1, None),
        Finding("hotel_red_flags", "warning", None, "Hôtel Regina"),
    }
    assert Finding("travel_time", "warning", 1, "Louvre", "Hôtel Regina").label() == (
        "travel_time day 1: Hôtel Regina → Louvre"
    )


def test_compare_matches_check_day_and_place():
    labels = [
        gold("opening_hours", 1, "Louvre Museum", "blocker"),
        gold("travel_time", 1, "Eiffel Tower"),
        gold("daily_load", 2, None),
        gold("hotel_red_flags", None, "Hôtel Regina"),
    ]
    pred = [
        Finding("opening_hours", "warning", 1, "The Louvre Museum"),  # name inside: a match
        Finding("travel_time", "warning", 2, "Eiffel Tower"),  # wrong day
        Finding("daily_load", "warning", 2, None),
        Finding("hotel_red_flags", "warning", None, "Hotel Regina"),
        Finding("public_holidays", "warning", 1, None),
    ]
    counts = compare(pred, labels)
    oh, tt, dl = counts["opening_hours"], counts["travel_time"], counts["daily_load"]
    assert (oh.tp, oh.severity_agree, tt.tp, tt.fp, tt.fn, dl.tp) == (1, 0, 0, 1, 1, 1)
    assert counts["hotel_red_flags"].tp == 1 and counts["public_holidays"].fp == 1
    total = Counts()
    for c in counts.values():
        total.add(c)
    m = total.metrics()
    assert (m["precision"], m["recall"], m["severity_agreement"]) == (0.6, 0.75, 2 / 3)
    assert Counts().metrics()["precision"] is None  # nothing predicted: undefined, not 0


def test_matches_are_one_to_one():
    labels = [gold("opening_hours", 1, "Louvre Museum")]
    pred = [Finding("opening_hours", "warning", 1, "Louvre"),
            Finding("opening_hours", "warning", 1, "Louvre Museum")]  # fmt: skip
    c = compare(pred, labels)["opening_hours"]
    assert (c.tp, c.fp) == (1, 1)


def test_a_prediction_on_an_unknown_isnt_counted():
    unknowns = [Unknown(check="opening_hours", day=1, place="Bateaux Parisiens", reason="r")]
    pred = [Finding("opening_hours", "warning", 1, "Bateaux Parisiens"),
            Finding("opening_hours", "warning", 2, "Bateaux Parisiens")]  # fmt: skip
    c = compare(pred, [], unknowns)["opening_hours"]
    assert (c.tp, c.fp, c.fn) == (0, 1, 0)
