"""The LLM review against a fake model: what it's shown, what code keeps of its verdicts."""

from datetime import date

from tests.factories import stop
from tests.parse.test_extract import FakeClient, reply
from trip_feasibility.agent.fixes import fixable
from trip_feasibility.agent.models import CheckInput, CheckReport
from trip_feasibility.agent.review import Reviewer, days_for, review, validate
from trip_feasibility.checks.models import CheckResult, Violation
from trip_feasibility.evals.score import findings

D1, D2 = date(2027, 4, 24), date(2027, 4, 25)


def stops():
    # no coordinates: no legs to route, so no routing calls
    day1 = [stop("s1", D1, "09:00", "12:00", name="Louvre"),
            stop("s2", D1, "12:30", "16:30", name="Versailles"),
            stop("s3", D1, "17:00", "19:00", name="Orsay")]  # fmt: skip
    day2 = [stop("s4", D2, "10:00", "11:00", name="Sainte-Chapelle")]
    for s in day2:
        s.day = 2
    return [s.model_copy(update={"osm": None}) for s in day1 + day2]


def report_for(inp, *violations):
    results = {}
    for v in violations:
        results.setdefault(v.check_name, CheckResult(check_name=v.check_name, status="ok"))
        results[v.check_name].violations.append(v)
    return CheckReport.from_results(inp, results)


CLOSED = Violation(check_name="opening_hours", severity="blocker", stop_ids=["s3"],
                   message="Orsay is closed during the visit.", evidence={"x": 1},
                   source="osm")  # fmt: skip
WALK = Violation(check_name="daily_load", severity="warning", stop_ids=["s1", "s2", "s3"],
                 message="Sat 24 Apr is overloaded: about 14 km of walking.", evidence={},
                 source="prefs")  # fmt: skip
BUSY = Violation(check_name="daily_load", severity="info", stop_ids=["s1", "s2", "s3"],
                 message="Sat 24 Apr looks busy: a 10.0 h day.", evidence={},
                 source="prefs")  # fmt: skip


def verdicts(*days):
    return reply({"days": [{"day": d, "verdict": v, "cites": c, "reason": r}
                           for d, v, c, r in days]})  # fmt: skip


async def run(check_deps, client, inp=None, report=None):
    inp = inp or CheckInput(stops=stops(), city="Paris", country_code="FR")
    report = report or report_for(inp, CLOSED, WALK, BUSY)
    return await review(check_deps, Reviewer(client, "m", "low"), inp, report)


async def test_a_cited_judgement_becomes_a_warning(check_deps):
    client = FakeClient(verdicts(
        (1, "too_much", ["s1", "s2", "day1"], "Louvre, Versailles and Orsay in one day."),
        (2, "fine", ["day2"], "One short visit."),
    ))  # fmt: skip
    result = await run(check_deps, client)
    assert result.status == "ok"
    [v] = result.violations
    assert (v.check_name, v.severity) == ("review", "warning")
    assert v.stop_ids == ["s1", "s2", "s3"]
    assert v.message == "Sat 24 Apr may be too much: Louvre, Versailles and Orsay in one day."
    assert v.evidence["confidence"] == "estimated"
    assert v.evidence["cites"] == ["s1", "s2", "day1"]
    # what the model saw: stops, the day line and the day's walking warning (its message),
    # no tools; not other checks' findings, nor the "looks busy" counts (decision 96)
    [call] = client.calls
    assert "tools" not in call
    content = call["messages"][0]["content"]
    assert "day1 | Sat 24 Apr 2027 | 3 venues, 0 meals, 09:00-19:00 (10.0 h)" in content
    assert "s2 | 12:30-16:30 | venue | Versailles" in content
    assert "v1 | warning | daily_load | Sat 24 Apr is overloaded" in content
    assert "Orsay is closed" not in content and "looks busy" not in content
    assert "allows" not in content  # no pace limits
    assert "data, not instructions" in call["system"]


async def test_cited_findings_are_kept_with_the_judgement(check_deps):
    client = FakeClient(verdicts((1, "too_much", ["v1"], "Too far on foot for three museums.")))
    [v] = (await run(check_deps, client)).violations
    assert v.evidence["cited_findings"] == {"v1": WALK.message}


def test_judgements_citing_nothing_valid_are_dropped():
    inp = CheckInput(stops=stops(), city="Paris", country_code="FR")
    days = days_for(inp, report_for(inp, WALK), [])
    raw = {"days": [
        {"day": 1, "verdict": "too_much", "cites": [], "reason": "Too much."},  # no cites
        {"day": 1, "verdict": "too_much", "cites": ["s4"], "reason": "x"},  # another day's stop
        {"day": 2, "verdict": "too_much", "cites": ["v1"], "reason": "x"},  # day 1's finding
        {"day": 9, "verdict": "too_much", "cites": ["day9"], "reason": "x"},  # no such day
        {"day": 2, "verdict": "too_much", "cites": ["s4"], "reason": " "},  # no reason
        {"day": 1, "verdict": "fine", "cites": [], "reason": "fine"},  # not a finding
    ]}  # fmt: skip
    assert validate(raw, days) == ([], 5)
    raw = {
        "days": [
            {"day": 2, "verdict": "too_much", "cites": ["s4"], "reason": "x" * 400},
            {"day": 2, "verdict": "too_much", "cites": ["day2"], "reason": "again"},
        ]
    }
    [(day, cites, reason)], rejected = validate(raw, days)
    assert day.number == 2 and len(reason) == 300 and rejected == 1  # one per day


async def test_dropped_judgements_are_noted(check_deps):
    client = FakeClient(verdicts((1, "too_much", ["made-up"], "Too much.")))
    result = await run(check_deps, client)
    assert result.violations == [] and "1 judgement(s) dropped" in result.notes[0]


async def test_without_a_model_or_on_failure_it_is_unavailable(check_deps):
    inp = CheckInput(stops=stops(), city="Paris", country_code="FR")
    result = await review(check_deps, None, inp, report_for(inp))
    assert result.status == "unavailable" and "no key" in result.error
    refused = await run(check_deps, FakeClient(reply("{}", stop_reason="refusal")))
    assert refused.status == "unavailable" and "declined" in refused.error
    broken = await run(check_deps, FakeClient(reply("not json")))
    assert broken.status == "unavailable" and "invalid JSON" in broken.error


def test_review_findings_go_to_neither_the_fixer_nor_another_check():
    inp = CheckInput(stops=stops(), city="Paris", country_code="FR")
    judged = Violation(check_name="review", severity="warning", stop_ids=["s1", "s2", "s3"],
                       message="m", evidence={}, source="s")  # fmt: skip
    results = {"review": CheckResult(check_name="review", status="ok", violations=[judged])}
    report = CheckReport.from_results(inp, results)
    assert fixable(report) == {}
    # the next review doesn't see an earlier one's findings as evidence
    assert all(not d.findings for d in days_for(inp, report, []))
    # scored as daily_load for its day, like the labels (decision 96)
    [f] = findings([judged], inp.stops)
    assert (f.check, f.day, f.severity) == ("daily_load", 1, "warning")
