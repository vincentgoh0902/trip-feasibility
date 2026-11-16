from datetime import date
from types import SimpleNamespace

from tests.factories import stop
from tests.parse.test_extract import FakeClient, reply
from trip_feasibility.agent.graph import (
    GRAPH,
    AgentContext,
    CheckInput,
    CheckReport,
    Reviewer,
    check_all,
    run_checks,
    stream_agent,
)
from trip_feasibility.checks.config import CHECK_NAMES, CheckConfig
from trip_feasibility.checks.models import CheckResult, Violation
from trip_feasibility.itinerary import TravelPreferences

DAY = date(2026, 7, 14)
STOPS = [stop("a", DAY, "10:00", "12:00"), stop("b", DAY, "13:00", "14:00", type="meal")]


def violation(check, severity, stop_ids):
    return Violation(
        check_name=check, severity=severity, stop_ids=stop_ids, message="m", evidence={}, source="s"
    )


class FakeMcp:
    """Answers every tool call with an ok result, records the calls."""

    def __init__(self, fail=(), raise_on=(), results=None):
        self.calls = {}
        self.fail, self.raise_on, self.results = fail, raise_on, results or {}

    async def call_tool(self, name, args):
        self.calls[name] = args
        if name in self.raise_on:
            raise ConnectionError("gone")
        if name in self.fail:
            return SimpleNamespace(
                is_error=True, structured_content=None, content=[SimpleNamespace(text="bad input")]
            )
        result = self.results.get(name, CheckResult(check_name=name, status="ok"))
        return SimpleNamespace(
            is_error=False, structured_content=result.model_dump(mode="json"), content=[]
        )


async def run(inp, mcp):
    state = await GRAPH.ainvoke({"input": inp, "results": {}}, context=AgentContext(mcp=mcp))
    return CheckReport.from_results(inp, state["results"])


async def test_every_enabled_check_runs_with_its_settings():
    mcp = FakeMcp()
    inp = CheckInput(
        stops=STOPS,
        city="Paris",
        country_code="FR",
        regions=["FR-IDF", "FR-75C"],
        preferences=TravelPreferences(pace="relaxed"),
        config=CheckConfig(travel_buffer_min=20, hotel_min_reviews=10),
    )
    report = await run(inp, mcp)
    assert list(report.results) == [
        "opening_hours",
        "public_holidays",
        "travel_time",
        "daily_load",
        "hotel_red_flags",
        "closures",
        "second_opinion",
    ]  # not the review: off by default (decision 96)
    # every tool ran; the second opinion has no model here (run() passes none)
    assert all(r.status == "ok" for n, r in report.results.items() if n != "second_opinion")
    assert mcp.calls["travel_time"]["buffer_min"] == 20
    assert mcp.calls["travel_time"]["preferences"]["pace"] == "relaxed"
    assert mcp.calls["hotel_red_flags"] == {
        "stops": [s.model_dump(mode="json") for s in STOPS],
        "city": "Paris",
        "min_reviews": 10,
    }
    assert mcp.calls["public_holidays"]["country_code"] == "FR"
    assert mcp.calls["public_holidays"]["regions"] == ["FR-IDF", "FR-75C"]
    assert mcp.calls["closures"]["city"] == "Paris"
    assert mcp.calls["closures"]["search"] is True


async def test_fix_rechecks_make_no_new_searches():
    mcp = FakeMcp()
    await check_all(mcp, CheckInput(stops=STOPS, city="Paris", country_code="FR"))
    assert mcp.calls["closures"]["search"] is False


async def test_demo_runs_make_no_new_searches():
    mcp = FakeMcp()
    await run(CheckInput(stops=STOPS, city="Paris", country_code="FR", web_search=False), mcp)
    assert mcp.calls["closures"]["search"] is False


async def test_disabled_checks_dont_run():
    mcp = FakeMcp()
    inp = CheckInput(
        stops=STOPS, city="Paris", country_code="FR", config=CheckConfig(enabled=["daily_load"])
    )
    report = await run(inp, mcp)
    assert set(mcp.calls) == set(report.results) == {"daily_load"}


async def test_failures_become_unavailable_and_the_run_finishes():
    mcp = FakeMcp(fail={"opening_hours"}, raise_on={"travel_time"})
    report = await run(CheckInput(stops=STOPS, city="Paris", country_code=None), mcp)
    statuses = {n: r.status for n, r in report.results.items()}
    assert statuses == {
        "opening_hours": "unavailable",
        "public_holidays": "unavailable",
        "travel_time": "unavailable",
        "daily_load": "ok",
        "hotel_red_flags": "ok",
        "closures": "ok",
        "second_opinion": "unavailable",  # no model given
    }
    assert report.results["opening_hours"].error == "bad input"
    assert "country is unknown" in report.results["public_holidays"].error
    assert "public_holidays" not in mcp.calls  # not called without a country
    assert "ConnectionError" in report.results["travel_time"].error


async def test_report_puts_the_worst_first():
    results = {
        "daily_load": CheckResult(
            check_name="daily_load",
            status="ok",
            violations=[violation("daily_load", "warning", ["a", "b"])],
        ),
        "opening_hours": CheckResult(
            check_name="opening_hours",
            status="ok",
            violations=[violation("opening_hours", "warning", ["b"])],
        ),
        "travel_time": CheckResult(
            check_name="travel_time",
            status="ok",
            violations=[violation("travel_time", "blocker", ["b"])],
        ),
    }
    report = await run(
        CheckInput(stops=STOPS, city="Paris", country_code="FR"), FakeMcp(results=results)
    )
    assert [(v.check_name, v.severity) for v in report.violations] == [
        ("travel_time", "blocker"),
        ("daily_load", "warning"),  # its first stop comes earlier
        ("opening_hours", "warning"),
    ]


async def test_real_tool_server_end_to_end(check_deps, respx_mock):
    respx_mock.get("https://nager.test/api/v3/PublicHolidays/2026/FR").respond(
        json=[
            {
                "date": "2026-07-14",
                "localName": "Fête nationale",
                "name": "Bastille Day",
                "countryCode": "FR",
                "fixed": True,
                "global": True,
                "counties": None,
                "launchYear": None,
                "types": ["Public"],
            }
        ]
    )
    inp = CheckInput(
        stops=STOPS,
        city="Paris",
        country_code="FR",
        config=CheckConfig(enabled=["public_holidays"]),
    )
    report = await run_checks(check_deps, inp)
    [v] = report.violations
    assert (v.check_name, v.severity, v.stop_ids) == ("public_holidays", "warning", ["a", "b"])


class ClosedOnDayOne(FakeMcp):
    """opening_hours reports stop "a" as closed whenever it's on day 1."""

    async def call_tool(self, name, args):
        self.calls.setdefault(name, []).append(args)
        violations = []
        if name == "opening_hours" and any(s["id"] == "a" and s["day"] == 1 for s in args["stops"]):
            violations = [violation("opening_hours", "blocker", ["a"])]
        result = CheckResult(check_name=name, status="ok", violations=violations)
        return SimpleNamespace(
            is_error=False, structured_content=result.model_dump(mode="json"), content=[]
        )


async def test_fix_node_proposes_and_rechecks_through_the_tools(check_deps):
    from tests.parse.test_extract import FakeClient, reply
    from trip_feasibility.agent.graph import Fixer

    two_days = [
        *STOPS,
        stop("c", date(2026, 7, 15), "10:00", "11:00").model_copy(update={"day": 2}),
    ]
    inp = CheckInput(
        stops=two_days,
        city="Paris",
        country_code="FR",
        config=CheckConfig(enabled=["opening_hours"]),
    )
    move = {
        "op": "move",
        "stop_id": "a",
        "day": 2,
        "start_time": "13:00",
        "end_time": "15:00",
        "place_name": None,
        "type": None,
        "mode": None,
    }
    client = FakeClient(
        reply({"fixes": [{"violation_ids": ["v1"], "description": "Day 2", "operations": [move]}]})
    )
    mcp = ClosedOnDayOne()
    state = await GRAPH.ainvoke(
        {"input": inp, "results": {}},
        context=AgentContext(
            mcp=mcp, deps=check_deps, fixer=Fixer(client=client, model="m", effort="low", city=None)
        ),
    )
    fixes = state["fixes"]
    [f] = fixes.fixes
    assert f.valid and fixes.unfixed == []
    assert len(mcp.calls["opening_hours"]) == 2  # the check, then the fix's re-check


async def test_no_fixer_no_fixes():
    state = await GRAPH.ainvoke(
        {"input": CheckInput(stops=STOPS, city="Paris", country_code="FR"), "results": {}},
        context=AgentContext(mcp=FakeMcp()),
    )
    assert state["fixes"] is None


async def test_the_review_judges_the_checks_results_and_streams_last(check_deps):
    unlocated = [s.model_copy(update={"osm": None}) for s in STOPS]  # no legs to route
    walk = violation("daily_load", "warning", ["a", "b"])
    mcp = FakeMcp(
        results={"daily_load": CheckResult(check_name="daily_load", status="ok", violations=[walk])}
    )
    judged = {"days": [{"day": 1, "verdict": "too_much", "cites": ["v1"], "reason": "Rushed."}]}
    client = FakeClient(reply(judged))
    inp = CheckInput(stops=unlocated, city="Paris", country_code="FR",
                     config=CheckConfig(enabled=list(CHECK_NAMES)))  # fmt: skip
    events = [
        e async for e in stream_agent(mcp, check_deps, inp, None, Reviewer(client, "m", "low"))
    ]
    checks = [value.check_name for kind, value in events if kind == "check"]
    assert checks[-2:] == ["second_opinion", "review"] and len(checks) == 8
    assert events[-1] == ("fixes", None)
    assert "v1 | warning | daily_load | m" in client.calls[0]["messages"][0]["content"]
    [v] = next(
        value for kind, value in events if kind == "check" and value.check_name == "review"
    ).violations
    assert v.severity == "warning"


async def test_review_off_makes_no_call(check_deps):
    client = FakeClient()
    config = CheckConfig(enabled=["opening_hours"])
    inp = CheckInput(stops=STOPS, city="Paris", country_code="FR", config=config)
    events = [e async for e in stream_agent(FakeMcp(), check_deps, inp, None,
                                            Reviewer(client, "m", "low"))]  # fmt: skip
    assert [v.check_name for k, v in events if k == "check"] == ["opening_hours"]
    assert client.calls == []
