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
        "text": "9-10 Museum A, 10-11 Museum B",
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
                "start_time": "10:00",
                "end_time": "11:00",
                "place_name": "Museum B",
                "type": "venue",
                "osm": "way/2",
            },
        ],
        "drafted_by": "human",
        "verified_by_human": True,
        "violations": [
            {
                "check": "opening_hours",
                "day": 1,
                "place": "Museum A",
                "severity": "blocker",
                "evidence": "Tue closed",
                "source": "test",
                "confidence": "verified",
            },
            # back to back, 12 min walk: more than 10 min short
            {
                "check": "travel_time",
                "day": 1,
                "place": "Museum B",
                "from_place": "Museum A",
                "severity": "warning",
                "evidence": "12 min walk",
                "source": "test",
                "confidence": "verified",
            },
        ],  # fmt: skip
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
    return await evaluate(
        check_deps, [ITIN], {}, mode=mode, config=CONFIG, settings=get_settings(), client=client
    )


async def test_gold_mode_scores_the_checks(check_deps, services):
    [o] = await run(check_deps, "gold")
    assert o.error is None
    assert o.gold == {
        "opening_hours day 1: Museum A": "blocker",
        "travel_time day 1: Museum A → Museum B": "warning",
    }
    assert o.predicted == o.gold
    s = summarise([o])
    assert s["overall"]["precision"] == s["overall"]["recall"] == 1.0
    assert s["coverage"]["unavailable_runs"] == {"opening_hours": 0, "travel_time": 0}
    assert "parse" not in s


def extracted(*names, b_start="10:00"):
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
    assert o.predicted == {"opening_hours day 1: Museum A": "blocker"}
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
    stops = [
        {
            "id": "s0",
            "day": 1,
            "start_time": "08:00",
            "end_time": "08:15",
            "place_name": "Hôtel Regina",
            "type": "hotel",
            "hotel": "paris-regina",
        },
        *[s.model_dump(mode="json", exclude_none=True) for s in ITIN.stops],
    ]
    itin = ITIN.model_copy(
        update={
            "hotel": "paris-regina",
            "stops": DatasetItinerary.model_validate(
                {**ITIN.model_dump(mode="json", by_alias=True), "stops": stops}
            ).stops,
        }
    )
    hotel = Hotel(key="paris-regina", city="paris", name="Hôtel Regina", csv_name="H tel Regina")
    config = CheckConfig(enabled=["opening_hours", "travel_time", "hotel_red_flags"])
    [o] = await evaluate(
        check_deps, [itin], {hotel.key: hotel}, mode="gold", config=config, settings=get_settings()
    )
    assert o.hotel_unlabelled
    assert not any(k.startswith("hotel_red_flags") for k in {**o.gold, **o.predicted})


async def test_gold_stop_without_osm_ref_is_geocoded_with_its_ref(check_deps, services, respx_mock):
    search_route(respx_mock, q="Museum A").respond(json=[result("way", 1, "Museum A", 48.00, 2.0)])
    data = ITIN.model_dump(mode="json", by_alias=True, exclude_none=True)
    del data["stops"][0]["osm"]
    [o] = await run_itin(check_deps, DatasetItinerary.model_validate(data))
    # needs the geocoded OSM ref, not just lat/lon
    assert "opening_hours day 1: Museum A" in o.predicted


async def test_gold_stop_not_located_is_never_looked_up(check_deps, services, respx_mock):
    # a lookalike place exists (a café called "Lunch"), but the gold says it's not a place
    lunch = search_route(respx_mock, q="Lunch").respond(
        json=[result("node", 9, "Lunch", 48.01, 2.0, category="amenity", type="cafe")]
    )
    data = ITIN.model_dump(mode="json", by_alias=True, exclude_none=True)
    del data["stops"][1]["osm"]
    data["stops"][1] |= {"place_name": "Lunch", "type": "meal", "located": False}
    data["violations"] = data["violations"][:1]
    [o] = await run_itin(check_deps, DatasetItinerary.model_validate(data))
    assert not lunch.called
    assert o.predicted == {"opening_hours day 1: Museum A": "blocker"}  # no leg to "Lunch"


async def test_gold_neighbourhood_is_an_area(check_deps, services, respx_mock):
    search_route(respx_mock, q="Le Marais").respond(
        json=[result("relation", 5, "Le Marais", 48.01, 2.0, category="place", type="suburb")]
    )
    data = ITIN.model_dump(mode="json", by_alias=True, exclude_none=True)
    del data["stops"][1]["osm"]
    data["stops"][1]["place_name"] = "Le Marais"
    [o] = await run_itin(check_deps, DatasetItinerary.model_validate(data))
    # back to back, but a leg into a neighbourhood isn't timed
    assert not any(k.startswith("travel_time") for k in o.predicted)


async def run_itin(check_deps, itin):
    return await evaluate(
        check_deps, [itin], {}, mode="gold", config=CONFIG, settings=get_settings()
    )


async def test_fix_validity_is_measured(check_deps, services):
    remove = {
        "op": "remove",
        "stop_id": "s1",
        "day": None,
        "start_time": None,
        "end_time": None,
        "place_name": None,
        "type": None,
        "mode": None,
    }
    client = FakeClient(
        reply(
            {
                "fixes": [
                    {
                        "violation_ids": ["v1", "v2"],
                        "description": "Skip Museum A",
                        "operations": [remove],
                    }
                ]
            }
        )
    )
    [o] = await evaluate(
        check_deps,
        [ITIN],
        {},
        mode="gold",
        config=CONFIG,
        settings=get_settings(),
        client=client,
        fixes=True,
    )
    assert (o.fixes_proposed, o.fixes_valid, o.fixable, o.fixed) == (1, 1, 2, 2)
    fx = summarise([o])["fixes"]
    assert (fx["validity"], fx["coverage"]) == (1.0, 1.0)
