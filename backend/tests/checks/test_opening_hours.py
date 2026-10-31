from datetime import date

import httpx
import pytest

from tests.factories import stop
from trip_feasibility.checks.opening_hours import check_opening_hours

LOOKUP = "https://nominatim.test/lookup"
MON, TUE = date(2026, 10, 26), date(2026, 10, 27)


def place(osm_type: str, osm_id: int, *, category="tourism", type="museum", **extratags):
    """A Nominatim /lookup result; the main tag comes back as category/type."""
    return {
        "osm_type": osm_type,
        "osm_id": osm_id,
        "category": category,
        "type": type,
        "name": f"{osm_type} {osm_id}",
        "extratags": extratags or None,
    }


def way(osm_id: int, **extratags):
    return place("way", osm_id, **extratags)


@pytest.fixture
def museum(respx_mock):
    """way/1: closed Mondays, 09:00-18:00 otherwise."""
    return respx_mock.get(LOOKUP).respond(json=[way(1, opening_hours="Tu-Su 09:00-18:00")])


@pytest.mark.parametrize(
    ("on", "start", "end", "expected"),
    [
        (TUE, "10:00", "12:00", None),
        (MON, "10:00", "12:00", "blocker"),
        (TUE, "17:00", "19:00", "warning"),
        (TUE, "07:00", "08:30", "blocker"),
        (TUE, "08:30", None, "blocker"),  # arrival time only, before opening
        (TUE, None, None, None),
        (MON, None, None, "blocker"),  # no time given and closed all day
    ],
)
async def test_visit_against_hours(check_deps, museum, on, start, end, expected):
    result = await check_opening_hours(check_deps, [stop("a", on, start, end)])
    assert result.status == "ok"
    assert [v.severity for v in result.violations] == ([expected] if expected else [])
    assert result.checked_stop_ids == ["a"]


async def test_violation_carries_evidence_and_source(check_deps, museum):
    [v] = (await check_opening_hours(check_deps, [stop("a", TUE, "17:00", "19:00")])).violations
    assert v.check_name == "opening_hours"
    assert v.stop_ids == ["a"]
    assert v.evidence["opening_hours"] == "Tu-Su 09:00-18:00"
    assert v.evidence["open_that_day"] == ["09:00-18:00"]
    assert v.source == "https://www.openstreetmap.org/way/1"


@pytest.mark.parametrize(
    ("hours", "expected"),
    [("Mo-Su 18:00-02:00", None), ("Mo-Su 18:00-24:00", "warning")],
)
async def test_visit_past_midnight(check_deps, respx_mock, hours, expected):
    respx_mock.get(LOOKUP).respond(json=[way(1, opening_hours=hours)])
    result = await check_opening_hours(check_deps, [stop("a", TUE, "23:00", "01:00")])
    assert [v.severity for v in result.violations] == ([expected] if expected else [])


async def test_sunset_hours_use_the_place_coordinates(check_deps, respx_mock):
    # a park closing 30 min before sunset: about 16:35 in Paris on 2 Jan, not 18:30
    respx_mock.get(LOOKUP).respond(json=[way(1, opening_hours="sunrise-(sunset-00:30)")])
    afternoon = stop("a", date(2027, 1, 2), "13:00", "17:00", at=(48.8467, 2.3364))
    [v] = (await check_opening_hours(check_deps, [afternoon], "fr")).violations
    assert v.severity == "warning"
    assert v.evidence["open_that_day"] == ["08:43-16:34"]


async def test_public_holiday_rule_uses_country(check_deps, respx_mock):
    respx_mock.get(LOOKUP).respond(json=[way(1, opening_hours="Mo-Su 09:00-18:00; PH off")])
    christmas = [stop("a", date(2026, 12, 25), "10:00", "12:00")]
    [v] = (await check_opening_hours(check_deps, christmas, "fr")).violations
    assert v.severity == "blocker"


@pytest.mark.parametrize(
    ("tags", "reason_part"),
    [
        ({"wheelchair": "yes"}, "no opening_hours"),
        ({"opening_hours": "Mo-Fr 9-17"}, "unparseable"),
        ({"opening_hours": "Mo-Su unknown"}, "unknown for this time"),
    ],
)
async def test_unusable_hours_are_unknown_not_violations(check_deps, respx_mock, tags, reason_part):
    respx_mock.get(LOOKUP).respond(json=[way(1, **tags)])
    result = await check_opening_hours(check_deps, [stop("a", TUE, "10:00", "11:00")])
    assert result.violations == []
    [u] = result.unknown
    assert u.stop_id == "a"
    assert reason_part in u.reason


async def test_place_missing_from_osm_is_a_warning(check_deps, respx_mock):
    respx_mock.get(LOOKUP).respond(json=[])
    [v] = (await check_opening_hours(check_deps, [stop("a", TUE, "10:00", "11:00")])).violations
    assert v.severity == "warning"
    assert v.evidence["found"] is False


async def test_disused_place_is_a_blocker(check_deps, respx_mock):
    respx_mock.get(LOOKUP).respond(
        json=[way(1, category="building", type="yes", **{"disused:amenity": "cafe"})]
    )
    [v] = (await check_opening_hours(check_deps, [stop("a", TUE, "10:00", "11:00")])).violations
    assert v.severity == "blocker"
    assert "closed down" in v.message


async def test_only_located_venues_and_meals_are_checked(check_deps, museum):
    stops = [
        stop("venue", TUE, "10:00", "11:00"),
        stop("hotel", MON, "10:00", "11:00", type="hotel"),
        stop("transfer", MON, "10:00", "11:00", type="transfer"),
        stop("nowhere", TUE, "10:00", "11:00", osm=None),
    ]
    result = await check_opening_hours(check_deps, stops)
    assert result.checked_stop_ids == ["venue"]
    assert [(u.stop_id, u.reason) for u in result.unknown] == [
        ("nowhere", "not resolved to an OpenStreetMap place")
    ]


async def test_current_main_tag_overrides_old_lifecycle_tags(check_deps, respx_mock):
    respx_mock.get(LOOKUP).respond(
        json=[place("way", 1, category="amenity", type="restaurant", **{"was:shop": "bakery"})]
    )
    result = await check_opening_hours(check_deps, [stop("a", TUE, "10:00", "11:00")])
    assert result.violations == []


async def test_one_lookup_and_cached_afterwards(check_deps, respx_mock):
    route = respx_mock.get(LOOKUP).respond(
        json=[way(1, opening_hours="24/7"), place("node", 7, opening_hours="24/7")]
    )
    stops = [
        stop("a", TUE, "10:00", "11:00", osm=("way", 1)),
        stop("b", TUE, "12:00", "13:00", osm=("node", 7)),
        stop("c", TUE, "14:00", "15:00", osm=("relation", 9)),  # unknown to Nominatim
    ]
    first = await check_opening_hours(check_deps, stops)
    second = await check_opening_hours(check_deps, stops)
    assert route.call_count == 1
    params = route.calls[0].request.url.params
    assert params["osm_ids"] == "W1,N7,R9"
    assert params["extratags"] == "1"
    assert first == second
    assert [v.stop_ids for v in second.violations] == [["c"]]


async def test_more_than_50_places_are_split_into_batches(check_deps, respx_mock):
    route = respx_mock.get(LOOKUP).respond(json=[])
    stops = [stop(f"s{i}", TUE, "10:00", "11:00", osm=("node", i)) for i in range(1, 121)]
    await check_opening_hours(check_deps, stops)
    sizes = [len(c.request.url.params["osm_ids"].split(",")) for c in route.calls]
    assert sizes == [50, 50, 20]


@pytest.mark.parametrize(
    ("response", "error_part"),
    [
        (httpx.Response(500), "HTTP 500"),
        (httpx.ConnectTimeout("slow"), "request failed"),
        (httpx.Response(200, text="<html>"), "invalid JSON"),
        (httpx.Response(200, json={"error": "x"}), "unexpected response"),
    ],
)
async def test_source_failures_make_the_check_unavailable(
    check_deps, respx_mock, response, error_part
):
    route = respx_mock.get(LOOKUP)
    if isinstance(response, Exception):
        route.side_effect = response
    else:
        route.return_value = response
    result = await check_opening_hours(check_deps, [stop("a", MON, "10:00", "11:00")])
    assert result.status == "unavailable"
    assert error_part in result.error


@pytest.mark.parametrize("status", [429, 403, 503])
async def test_rate_limit_response_pauses_further_requests(check_deps, respx_mock, status):
    route = respx_mock.get(LOOKUP).respond(status)
    first = await check_opening_hours(check_deps, [stop("a", MON, "10:00", "11:00")])
    second = await check_opening_hours(check_deps, [stop("b", MON, "10:00", "11:00")])
    assert first.status == second.status == "unavailable"
    assert "backing off" in first.error
    assert "back off" in second.error
    assert route.call_count == 1  # the second check never reached Nominatim


async def test_no_located_stops_makes_no_request(check_deps, respx_mock):
    result = await check_opening_hours(check_deps, [stop("a", MON, osm=None)])
    assert result.status == "ok"
    assert not respx_mock.calls
