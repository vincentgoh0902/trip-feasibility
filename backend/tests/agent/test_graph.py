from datetime import date
from types import SimpleNamespace

from tests.factories import stop
from trip_feasibility.agent.graph import (
    GRAPH,
    AgentContext,
    CheckInput,
    CheckReport,
    run_checks,
)
from trip_feasibility.checks.config import CheckConfig
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
    ]
    assert all(r.status == "ok" for r in report.results.values())
    assert mcp.calls["travel_time"]["buffer_min"] == 20
    assert mcp.calls["travel_time"]["preferences"]["pace"] == "relaxed"
    assert mcp.calls["hotel_red_flags"] == {
        "stops": [s.model_dump(mode="json") for s in STOPS],
        "city": "Paris",
        "min_reviews": 10,
    }
    assert mcp.calls["public_holidays"]["country_code"] == "FR"


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
