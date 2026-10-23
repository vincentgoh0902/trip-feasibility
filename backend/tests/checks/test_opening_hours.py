from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from tests.factories import stop
from trip_feasibility.checks.opening_hours import check_opening_hours

OVERPASS_A = "https://overpass-a.test/api/interpreter"
OVERPASS_B = "https://overpass-b.test/api/interpreter"
MON, TUE = date(2026, 10, 26), date(2026, 10, 27)


def overpass(*elements):
    return {"version": 0.6, "elements": list(elements)}


def way(osm_id: int, **tags):
    return {"type": "way", "id": osm_id, "tags": tags}


def query_of(request: httpx.Request) -> str:
    return parse_qs(request.content.decode())["data"][0]


@pytest.fixture
def museum(respx_mock):
    """way/1: closed Mondays, 09:00-18:00 otherwise."""
    return respx_mock.post(OVERPASS_A).respond(
        json=overpass(way(1, name="Museum", opening_hours="Tu-Su 09:00-18:00"))
    )


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
    respx_mock.post(OVERPASS_A).respond(json=overpass(way(1, opening_hours=hours)))
    result = await check_opening_hours(check_deps, [stop("a", TUE, "23:00", "01:00")])
    assert [v.severity for v in result.violations] == ([expected] if expected else [])


async def test_public_holiday_rule_uses_country(check_deps, respx_mock):
    respx_mock.post(OVERPASS_A).respond(
        json=overpass(way(1, opening_hours="Mo-Su 09:00-18:00; PH off"))
    )
    christmas = [stop("a", date(2026, 12, 25), "10:00", "12:00")]
    [v] = (await check_opening_hours(check_deps, christmas, "fr")).violations
    assert v.severity == "blocker"


@pytest.mark.parametrize(
    ("tags", "reason_part"),
    [
        ({"name": "Cafe"}, "no opening_hours"),
        ({"opening_hours": "Mo-Fr 9-17"}, "unparseable"),
        ({"opening_hours": "Mo-Su unknown"}, "unknown for this time"),
    ],
)
async def test_unusable_hours_are_unknown_not_violations(check_deps, respx_mock, tags, reason_part):
    respx_mock.post(OVERPASS_A).respond(json=overpass(way(1, **tags)))
    result = await check_opening_hours(check_deps, [stop("a", TUE, "10:00", "11:00")])
    assert result.violations == []
    [u] = result.unknown
    assert u.stop_id == "a"
    assert reason_part in u.reason


async def test_place_missing_from_osm_is_a_warning(check_deps, respx_mock):
    respx_mock.post(OVERPASS_A).respond(json=overpass())
    [v] = (await check_opening_hours(check_deps, [stop("a", TUE, "10:00", "11:00")])).violations
    assert v.severity == "warning"
    assert v.evidence["found"] is False


async def test_disused_place_is_a_blocker(check_deps, respx_mock):
    respx_mock.post(OVERPASS_A).respond(
        json=overpass(way(1, **{"name": "Old Cafe", "disused:amenity": "cafe"}))
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


async def test_one_batched_query_and_cached_afterwards(check_deps, respx_mock):
    route = respx_mock.post(OVERPASS_A).respond(
        json=overpass(
            way(1, opening_hours="24/7"),
            {"type": "node", "id": 7, "tags": {"opening_hours": "24/7"}},
        )
    )
    stops = [
        stop("a", TUE, "10:00", "11:00", osm=("way", 1)),
        stop("b", TUE, "12:00", "13:00", osm=("node", 7)),
        stop("c", TUE, "14:00", "15:00", osm=("relation", 9)),  # not in OSM
    ]
    first = await check_opening_hours(check_deps, stops)
    second = await check_opening_hours(check_deps, stops)
    assert route.call_count == 1
    query = query_of(route.calls[0].request)
    assert "node(id:7);" in query and "way(id:1);" in query and "relation(id:9);" in query
    assert first == second
    assert [v.stop_ids for v in second.violations] == [["c"]]


@pytest.mark.parametrize(
    "first_server",
    [
        httpx.Response(504),
        httpx.Response(200, json={"elements": [], "remark": "runtime error: timeout"}),
        httpx.ConnectTimeout("slow"),
    ],
)
async def test_falls_back_to_next_overpass_server(check_deps, respx_mock, first_server):
    a = respx_mock.post(OVERPASS_A)
    if isinstance(first_server, Exception):
        a.side_effect = first_server
    else:
        a.return_value = first_server
    respx_mock.post(OVERPASS_B).respond(json=overpass(way(1, opening_hours="Tu-Su 09:00-18:00")))
    result = await check_opening_hours(check_deps, [stop("a", MON, "10:00", "11:00")])
    assert result.status == "ok"
    assert [v.severity for v in result.violations] == ["blocker"]


async def test_all_overpass_servers_down_is_unavailable(check_deps, respx_mock):
    respx_mock.post(OVERPASS_A).respond(504)
    respx_mock.post(OVERPASS_B).respond(429)
    result = await check_opening_hours(check_deps, [stop("a", MON, "10:00", "11:00")])
    assert result.status == "unavailable"
    assert "HTTP 504" in result.error and "HTTP 429" in result.error


async def test_no_located_stops_makes_no_request(check_deps, respx_mock):
    result = await check_opening_hours(check_deps, [stop("a", MON, osm=None)])
    assert result.status == "ok"
    assert not respx_mock.calls
