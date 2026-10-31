from datetime import date

import pytest

from tests import fake_osrm
from tests.factories import stop
from trip_feasibility.checks.daily_load import check_daily_load
from trip_feasibility.itinerary import TravelPreferences

DAY = date(2026, 10, 27)


@pytest.fixture
def osrm(respx_mock):
    return fake_osrm.install(respx_mock)


def venues(n: int, *, start_hour=9, km_apart=0.5, on=DAY, prefix="v"):
    """n one-hour venue visits, an hour apart, km_apart km from each other."""
    return [
        stop(
            f"{prefix}{i}",
            on,
            f"{start_hour + 2 * i:02d}:00",
            f"{start_hour + 2 * i + 1:02d}:00",
            at=(48 + i * km_apart / 100, 2.0),
        )
        for i in range(n)
    ]


@pytest.mark.parametrize(
    ("pace", "n", "overloaded"),
    [("relaxed", 3, False), ("relaxed", 4, True), ("moderate", 5, False), ("moderate", 6, True)],
)
async def test_venue_count_against_pace(check_deps, osrm, pace, n, overloaded):
    prefs = TravelPreferences(pace=pace, max_walk_km=50)
    # start at 07:00 so even 6 visits end by 18:00 (an 11 h day: only "relaxed" minds that)
    stops = venues(n, start_hour=7)
    result = await check_daily_load(check_deps, stops, prefs)
    flagged = [
        v for v in result.violations if v.evidence["venues"] > v.evidence["limits"]["venues"]
    ]
    assert bool(flagged) is overloaded
    # a count is a hint for the review, not a fault (decision 81)
    assert all(v.severity == "info" and "looks busy" in v.message for v in flagged)


async def test_long_day(check_deps, osrm):
    stops = [
        stop("a", DAY, "08:00", "09:00", at=(48.0, 2.0)),
        stop("b", DAY, "19:00", "21:00", at=(48.0, 2.0), type="meal"),
    ]
    [v] = (await check_daily_load(check_deps, stops, TravelPreferences())).violations
    assert v.severity == "info"
    assert v.evidence["day_hours"] == 13.0
    assert "13.0 h day" in v.message


@pytest.mark.parametrize(("limit", "expected"), [(10, None), (3, "warning"), (2, "blocker")])
async def test_walking_against_limit(check_deps, osrm, limit, expected):
    # 4 walking legs of 0.8 km = 3.2 km
    prefs = TravelPreferences(pace="packed", max_walk_km=limit, transport_modes=["walk"])
    result = await check_daily_load(check_deps, venues(5, km_apart=0.8), prefs)
    assert [v.severity for v in result.violations] == ([expected] if expected else [])
    if expected:
        assert result.violations[0].evidence["walk_km"] == 3.2
        assert "is overloaded" in result.violations[0].message


async def test_walking_outranks_a_busy_day(check_deps, osrm):
    # 6 venues for a moderate pace (info) and 4 km walked against a 3 km limit (warning)
    prefs = TravelPreferences(pace="moderate", max_walk_km=3, transport_modes=["walk"])
    [v] = (
        await check_daily_load(check_deps, venues(6, start_hour=7, km_apart=0.8), prefs)
    ).violations
    assert v.severity == "warning"
    assert "km of walking" in v.message and "6 venues" in v.message


async def test_transit_legs_count_station_walking_only(check_deps, osrm):
    # 4 legs of 5 km: too far to walk, so transit; 4 x 0.4 km of walking to stations
    prefs = TravelPreferences(pace="packed", max_walk_km=1, transport_modes=["walk", "transit"])
    [v] = (await check_daily_load(check_deps, venues(5, km_apart=5), prefs)).violations
    assert v.evidence["walk_km"] == 1.6


async def test_each_day_is_judged_separately(check_deps, osrm):
    prefs = TravelPreferences(pace="relaxed", max_walk_km=50)
    stops = venues(3, on=date(2026, 10, 27)) + venues(4, on=date(2026, 10, 28), prefix="w")
    [v] = (await check_daily_load(check_deps, stops, prefs)).violations
    assert v.evidence["date"] == "2026-10-28"
    assert v.stop_ids == ["w0", "w1", "w2", "w3"]


async def test_routing_down_still_checks_venues_and_hours(check_deps, respx_mock):
    respx_mock.get(url__regex=fake_osrm.ROUTING.pattern).respond(503)
    prefs = TravelPreferences(pace="relaxed")
    result = await check_daily_load(check_deps, venues(4), prefs)
    assert result.status == "ok"
    [v] = result.violations
    assert v.evidence["walk_km"] is None
    assert "Walking distance not checked" in result.notes[0]


async def test_defaults_apply_without_preferences(check_deps, osrm):
    result = await check_daily_load(check_deps, venues(6))
    [v] = result.violations
    assert v.evidence["pace"] == "moderate"
