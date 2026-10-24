from datetime import date

import pytest

from tests import fake_osrm
from tests.parse.test_extract import FakeClient, reply
from tests.parse.test_geocode import PARIS, result, search_route
from trip_feasibility.checks.config import CheckConfig
from trip_feasibility.config import get_settings
from trip_feasibility.dataset.models import DatasetItinerary, Hotel
from trip_feasibility.evals.runner import evaluate, summarise

TUESDAY = date(2026, 12, 8)
CONFIG = CheckConfig(enabled=["opening_hours", "travel_time"])
ITIN = DatasetItinerary.model_validate(
    {
        "id": "paris-01",
        "city": "paris",
        "split": "dev",
        "source": "claude",
        "trip_start": TUESDAY,
        "preferences": {"transport_modes": ["walk"]},
        "text": "9-10 Museum A, 10:05-11 Museum B",
        "stops": [
            {
                "id": "s1",
                "day": 1,
                "start_time": "09:00",
                "end_time": "10:00",
                "place_name": "Museum A",
                "type": "venue",
                "osm": "way/1",
            },
            {
                "id": "s2",
                "day": 1,
                "start_time": "10:05",
                "end_time": "11:00",
                "place_name": "Museum B",
                "type": "venue",
                "osm": "way/2",
            },
        ],  # fmt: skip
        # 5 min gap, 12 min walk: a derived travel_time blocker
        "legs": [{"from": "s1", "to": "s2", "mode": "walk", "minutes": 12, "source": "test"}],
        "violations": [
            {"check": "opening_hours", "severity": "blocker", "stop": "s1", "note": "Tue closed"}
        ],
    }
)


def place(osm_id, lat, hours):
    return {
        "osm_type": "way",
        "osm_id": osm_id,
        "lat": str(lat),
        "lon": "2.0",
        "category": "tourism",
        "type": "museum",
        "extratags": {"opening_hours": hours},
    }


@pytest.fixture
def services(respx_mock):
    search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[PARIS])
    # A is closed on Tuesdays; B is 1 km (12 min on foot) from A in the fake router
    respx_mock.get("https://nominatim.test/lookup").respond(
        json=[place(1, 48.00, "We-Su 10:00-18:00"), place(2, 48.01, "Mo-Su 09:00-18:00")]
    )
    fake_osrm.install(respx_mock)


async def run(check_deps, mode, client=None):
    return await evaluate(check_deps, [ITIN], {}, mode=mode, config=CONFIG,
                          settings=get_settings(), client=client)  # fmt: skip


async def test_gold_mode_scores_the_checks(check_deps, services):
    [o] = await run(check_deps, "gold")
    assert o.error is None
    assert o.gold == {"opening_hours stop:s1": "blocker", "travel_time leg:s1:s2": "blocker"}
    assert o.predicted == o.gold
    s = summarise([o])
    assert s["overall"]["precision"] == s["overall"]["recall"] == 1.0
    assert s["coverage"]["unavailable_runs"] == {"opening_hours": 0, "travel_time": 0}
    assert "parse" not in s


def extracted(*names, b_start="10:05"):
    times = [("09:00", "10:00"), (b_start, "11:00"), ("12:00", "13:00")]
    stop = {"day": 1, "time_of_day": None, "type": "venue"}
    pairs = zip(names, times, strict=False)
    return {
        "stops": [{**stop, "place_name": n, "start_time": a, "end_time": b} for n, (a, b) in pairs]
    }


async def test_parsed_mode_aligns_then_scores(check_deps, services, respx_mock):
    search_route(respx_mock, q="Museum A").respond(json=[result("way", 1, "Museum A", 48.00, 2.0)])
    search_route(respx_mock, q="Museum B").respond(json=[result("way", 2, "Museum B", 48.01, 2.0)])
    search_route(respx_mock, q="Museum C").respond(json=[])
    # B's start misread as 10:30 (so the tight leg is missed), and a stop that isn't there
    reply_ = reply(extracted("Museum A", "Museum B", "Museum C", b_start="10:30"))
    [o] = await run(check_deps, "parsed", FakeClient(reply_))
    assert o.error is None
    assert o.predicted == {"opening_hours stop:s1": "blocker"}
    s = summarise([o])
    assert (s["overall"]["tp"], s["overall"]["fp"], s["overall"]["fn"]) == (1, 0, 1)
    assert s["parse"]["stop_recall"] == 1.0
    assert s["parse"]["stop_precision"] == 2 / 3
    assert s["parse"]["field_errors"] == {"start_time": 1}
    assert s["tokens"] == {"input": 100, "output": 50}


async def test_parse_failure_misses_everything(check_deps, services):
    [o] = await run(check_deps, "parsed", FakeClient(reply("nope"), reply("nope")))
    assert o.error == "parse_failed"
    s = summarise([o])
    assert (s["overall"]["tp"], s["overall"]["fn"]) == (0, 2)
    assert s["errors"] == {"paris-01": "parse_failed"}
    assert s["parse"]["stop_recall"] == 0.0


async def test_unlabelled_hotel_is_left_out_of_the_scores(check_deps, services, respx_mock):
    search_route(respx_mock, q="Hôtel Regina").respond(json=[])
    stops = [{"id": "s0", "day": 1, "start_time": "08:00", "end_time": "08:15",
              "place_name": "Hôtel Regina", "type": "hotel", "hotel": "paris-regina"},
             *[s.model_dump(mode="json", exclude_none=True) for s in ITIN.stops]]  # fmt: skip
    itin = ITIN.model_copy(update={"stops": DatasetItinerary.model_validate(
        {**ITIN.model_dump(mode="json", by_alias=True), "stops": stops}).stops})  # fmt: skip
    hotel = Hotel(key="paris-regina", city="paris", name="Hôtel Regina", csv_name="H tel Regina")
    config = CheckConfig(enabled=["opening_hours", "travel_time", "hotel_red_flags"])
    [o] = await evaluate(check_deps, [itin], {hotel.key: hotel}, mode="gold", config=config,
                         settings=get_settings())  # fmt: skip
    assert o.hotel_unlabelled
    assert not any(k.startswith("hotel_red_flags") for k in {**o.gold, **o.predicted})


async def test_gold_stop_without_osm_ref_is_geocoded_with_its_ref(check_deps, services, respx_mock):
    search_route(respx_mock, q="Museum A").respond(json=[result("way", 1, "Museum A", 48.00, 2.0)])
    data = ITIN.model_dump(mode="json", by_alias=True, exclude_none=True)
    del data["stops"][0]["osm"]
    [o] = await run_itin(check_deps, DatasetItinerary.model_validate(data))
    assert "opening_hours stop:s1" in o.predicted  # needs the geocoded OSM ref, not just lat/lon


async def run_itin(check_deps, itin):
    return await evaluate(check_deps, [itin], {}, mode="gold", config=CONFIG,
                          settings=get_settings())  # fmt: skip
