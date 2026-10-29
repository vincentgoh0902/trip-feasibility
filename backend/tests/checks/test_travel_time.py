import asyncio
from datetime import date

import httpx
import pytest

from tests import fake_osrm
from tests.factories import stop
from trip_feasibility.checks.travel_time import check_travel_time
from trip_feasibility.itinerary import TravelPreferences
from trip_feasibility.sources.routing import _chunks

DAY = date(2026, 10, 27)
WALK = TravelPreferences(transport_modes=["walk"])
# 1 km apart: 12 min on foot in the fake router
A, B = (48.00, 2.00), (48.01, 2.00)
FAR = (48.05, 2.00)  # 5 km from A


@pytest.fixture
def osrm(respx_mock):
    return fake_osrm.install(respx_mock)


def two_stops(end_a: str, start_b: str, b_at=B):
    return [
        stop("a", DAY, "09:00", end_a, at=A, name="Museum"),
        stop("b", DAY, start_b, "13:00", at=b_at, name="Cafe"),
    ]


@pytest.mark.parametrize(
    ("end_a", "start_b", "expected"),
    [
        ("10:00", "10:30", None),  # 30 min gap, 12 min walk
        ("10:00", "10:15", "warning"),  # fits, but under 12 + 10 buffer
        ("10:00", "10:10", "warning"),  # 2 min short: leave a little early
        ("10:00", "10:02", "warning"),  # 10 min short: still within the tolerance
        ("10:00", "10:01", "blocker"),  # 11 min short
        ("10:00", "09:50", "blocker"),  # overlap
    ],
)
async def test_gap_against_walking_time(check_deps, osrm, end_a, start_b, expected):
    result = await check_travel_time(check_deps, two_stops(end_a, start_b), WALK)
    assert result.status == "ok"
    assert [v.severity for v in result.violations] == ([expected] if expected else [])
    assert result.checked_stop_ids == ["a", "b"]


async def test_violation_evidence(check_deps, osrm):
    [v] = (await check_travel_time(check_deps, two_stops("10:00", "10:10"), WALK)).violations
    assert v.stop_ids == ["a", "b"]
    assert v.evidence["gap_min"] == 10
    assert v.evidence["travel_min"] == 12
    assert v.evidence["mode"] == "walk"
    assert v.evidence["options"]["walk"] == {"minutes": 12.0, "km": 1.0, "estimated": False}
    assert "Museum → Cafe" in v.message
    assert v.source == "https://routing.openstreetmap.de"


async def test_overlap_message(check_deps, osrm):
    [v] = (await check_travel_time(check_deps, two_stops("10:00", "09:50"), WALK)).violations
    assert v.message == "Cafe starts 10 min before Museum ends."


async def test_zero_buffer(check_deps, osrm):
    result = await check_travel_time(check_deps, two_stops("10:00", "10:15"), WALK, buffer_min=0)
    assert result.violations == []


async def test_long_legs_use_transit_estimate_not_walking(check_deps, osrm):
    # 5 km: walking (60 min) is over the 3 km walking-leg limit; transit = 10 min car x 1.5 + 10
    prefs = TravelPreferences(transport_modes=["walk", "transit"])
    stops = two_stops("10:00", "10:30", b_at=FAR)
    [v] = (await check_travel_time(check_deps, stops, prefs)).violations
    assert v.severity == "warning"  # 30 min gap: 25 min transit fits, but not with the buffer
    assert v.evidence["mode"] == "transit"
    assert v.evidence["travel_min"] == 25
    assert v.evidence["options"]["transit"]["estimated"] is True
    assert "walk" not in v.evidence["options"]


@pytest.mark.parametrize(
    ("start_b", "expected"),
    [("10:40", None), ("10:30", "warning"), ("10:18", "warning"), ("10:09", "warning"),
     ("10:08", "blocker")],
)  # fmt: skip
async def test_estimated_times_only_block_when_clearly_short(check_deps, osrm, start_b, expected):
    # transit estimate 25 min, counted as 18.75 (25% margin); blocker under 18.75 - 10 min
    prefs = TravelPreferences(transport_modes=["transit"])
    result = await check_travel_time(check_deps, two_stops("10:00", start_b, b_at=FAR), prefs)
    assert [v.severity for v in result.violations] == ([expected] if expected else [])


async def test_walking_only_traveller_walks_long_legs(check_deps, osrm):
    stops = two_stops("10:00", "10:30", b_at=FAR)
    [v] = (await check_travel_time(check_deps, stops, WALK)).violations
    assert v.evidence["mode"] == "walk"
    assert v.evidence["travel_min"] == 60
    assert v.severity == "blocker"


async def test_fastest_preferred_mode_wins(check_deps, osrm):
    prefs = TravelPreferences(transport_modes=["walk", "bike", "car"])
    stops = two_stops("10:00", "11:00", b_at=FAR)
    plan_result = await check_travel_time(check_deps, stops, prefs)
    assert plan_result.violations == []
    # 5 km: bike 20 min, car 10 x 1.3 + 5 = 18 min -> car
    stops = two_stops("10:00", "10:20", b_at=FAR)
    [v] = (await check_travel_time(check_deps, stops, prefs)).violations
    assert v.evidence["mode"] == "car"
    assert v.evidence["travel_min"] == 18


async def test_same_place_needs_no_travel(check_deps, osrm):
    stops = [stop("a", DAY, "09:00", "12:00", at=A), stop("b", DAY, "12:00", "13:00", at=A)]
    result = await check_travel_time(check_deps, stops, WALK)
    assert result.violations == []
    assert not osrm.calls


async def test_only_consecutive_stops_on_the_same_day(check_deps, osrm):
    stops = [
        stop("a", DAY, "20:00", "22:00", at=A),
        stop("b", date(2026, 10, 28), "08:00", "09:00", at=FAR),
    ]
    result = await check_travel_time(check_deps, stops, WALK)
    assert result.violations == [] and result.unknown == []


async def test_area_stops_are_not_timed(check_deps, osrm):
    # "El Born" right after a market at its edge: routing to the centre would invent a journey
    stops = [
        stop("a", DAY, "09:00", "11:00", at=A),
        stop("b", DAY, "11:00", "12:00", at=FAR, name="El Born").model_copy(update={"area": True}),
        stop("c", DAY, "12:00", "13:00", at=A),
    ]
    result = await check_travel_time(check_deps, stops, WALK)
    assert result.violations == []
    assert [(u.stop_id, u.reason) for u in result.unknown] == [
        ("b", "a neighbourhood, not one place, so travel to and from it isn't timed")
    ]
    assert not osrm.calls


async def test_missing_times_and_locations_are_unknown(check_deps, osrm):
    stops = [
        stop("a", DAY, "09:00", None, at=A),
        stop("b", DAY, "10:00", "11:00", at=B),
        stop("c", DAY, "12:00", "13:00"),  # not located
    ]
    result = await check_travel_time(check_deps, stops, WALK)
    reasons = {u.stop_id: u.reason for u in result.unknown}
    assert "no time gap" in reasons["b"]
    assert "not located" in reasons["c"]
    assert result.violations == []


async def test_unreachable_destination_is_unknown(check_deps, osrm):
    stops = two_stops("10:00", "10:30", b_at=(0.0, 2.0))
    result = await check_travel_time(check_deps, stops, WALK)
    assert [(u.stop_id, u.reason) for u in result.unknown] == [
        ("b", "no route found with your transport modes")
    ]


async def test_routes_are_cached_per_pair(check_deps, osrm):
    stops = two_stops("10:00", "10:30")
    await check_travel_time(check_deps, stops, WALK)
    await check_travel_time(check_deps, stops, WALK)
    assert osrm.call_count == 1
    # Adding a stop only routes the new pair.
    stops.append(stop("c", DAY, "14:00", "15:00", at=FAR))
    await check_travel_time(check_deps, stops, WALK)
    assert osrm.call_count == 2
    assert str(osrm.calls[-1].request.url).count(";") == 1  # just B and FAR


async def test_one_request_per_profile(check_deps, osrm):
    prefs = TravelPreferences(transport_modes=["walk", "transit", "bike"])
    stops = [
        stop(str(i), DAY, f"{9 + i:02d}:00", f"{9 + i:02d}:30", at=(48 + i / 100, 2.0))
        for i in range(5)
    ]
    await check_travel_time(check_deps, stops, prefs)
    profiles = sorted(str(c.request.url).split("/")[3] for c in osrm.calls)
    assert profiles == ["routed-bike", "routed-car", "routed-foot"]


@pytest.mark.parametrize("status", [429, 403, 503])
async def test_rate_limit_pauses_routing(check_deps, respx_mock, status):
    route = respx_mock.get(url__regex=fake_osrm.ROUTING.pattern).respond(status)
    first = await check_travel_time(check_deps, two_stops("10:00", "10:30"), WALK)
    second = await check_travel_time(check_deps, two_stops("10:00", "10:30"), WALK)
    assert first.status == second.status == "unavailable"
    assert route.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"code": "InvalidQuery"}),
        httpx.Response(502, text="bad gateway"),
        httpx.ConnectError("down"),
    ],
)
async def test_routing_failures_are_unavailable(check_deps, respx_mock, response):
    route = respx_mock.get(url__regex=fake_osrm.ROUTING.pattern)
    if isinstance(response, Exception):
        route.side_effect = response
    else:
        route.return_value = response
    result = await check_travel_time(check_deps, two_stops("10:00", "10:30"), WALK)
    assert result.status == "unavailable"


def test_chunks_keep_each_table_under_the_point_limit():
    pairs = [((float(i), 0.0), (float(i), 1.0)) for i in range(60)]  # 120 distinct points
    chunks = _chunks(pairs)
    assert sum(len(c) for c in chunks) == 60
    assert all(len({p for pair in c for p in pair}) <= 50 for c in chunks)


async def test_parallel_checks_route_each_leg_once(check_deps, osrm):
    """travel_time and daily_load run in parallel in the agent and need the same legs."""
    from trip_feasibility.checks.daily_load import check_daily_load

    async def slow(request):
        await asyncio.sleep(0.05)  # a real server takes a while; both checks miss the cache
        return fake_osrm.respond(request)

    osrm.mock(side_effect=slow)
    stops = two_stops("10:00", "10:30")
    await asyncio.gather(
        check_travel_time(check_deps, stops, WALK), check_daily_load(check_deps, stops, WALK)
    )
    assert osrm.call_count == 1
