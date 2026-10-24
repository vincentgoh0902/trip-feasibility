from datetime import date

import pytest

from tests.parse.test_extract import GOOD, FakeClient, reply
from tests.parse.test_geocode import LOUVRE, PARIS, search_route
from trip_feasibility.config import get_settings
from trip_feasibility.parse.clean import MAX_CLEAN_CHARS, MAX_RAW_CHARS
from trip_feasibility.parse.pipeline import ParseError, parse_itinerary

TRIP = date(2026, 12, 8)
CAFE = {**GOOD["stops"][0], "start_time": "12:15", "end_time": "13:30",
        "place_name": "Café Nowhere", "type": "meal"}  # fmt: skip


async def parse(check_deps, client, city="Paris", text="9-12 Louvre"):
    return await parse_itinerary(
        check_deps, client, get_settings(), text, city=city, trip_start=TRIP
    )


async def test_text_to_geocoded_stops(check_deps, respx_mock):
    search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[PARIS])
    search_route(respx_mock, q="Louvre Museum").respond(json=[LOUVRE])
    search_route(respx_mock, q="Café Nowhere").respond(json=[])
    parsed = await parse(check_deps, FakeClient(reply({"stops": [GOOD["stops"][0], CAFE]})))
    louvre, cafe = parsed.stops
    assert (louvre.id, louvre.date, louvre.lat) == ("s1", TRIP, 48.8611)
    assert cafe.lat is None
    assert parsed.unresolved == ["s2"]
    assert parsed.city.country_code == "fr"
    assert parsed.extraction.attempts == 1


async def test_unknown_city_is_a_parse_error(check_deps, respx_mock):
    search_route(respx_mock, q="Atlantis", featureType="settlement").respond(json=[])
    client = FakeClient()
    with pytest.raises(ParseError) as e:
        await parse(check_deps, client, city="Atlantis")
    assert e.value.code == "city_not_found"
    assert client.calls == []  # no LLM call for a city we can't find


async def test_extraction_failure_keeps_its_code(check_deps, respx_mock):
    search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[PARIS])
    with pytest.raises(ParseError) as e:
        await parse(check_deps, FakeClient(reply("not json"), reply("still not json")))
    assert e.value.code == "parse_failed"


async def test_extraction_sees_the_cleaned_text(check_deps, respx_mock):
    search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[PARIS])
    search_route(respx_mock, q="Louvre Museum").respond(json=[LOUVRE])
    client = FakeClient(reply(GOOD))
    text = "9-12 Louvre\n\n[pic](https://images.openai.com/x?purpose=inline)\n\n4.7"
    parsed = await parse(check_deps, client, text=text)
    assert parsed.text == "9-12 Louvre"
    assert "openai" not in client.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "text",
    [
        "x" * (MAX_RAW_CHARS + 1),
        "9-12 Louvre\n" * (MAX_CLEAN_CHARS // 12 + 1),
    ],
    ids=["raw", "cleaned"],
)
async def test_too_long_is_a_parse_error_before_any_call(check_deps, text):
    client = FakeClient()
    with pytest.raises(ParseError) as e:
        await parse(check_deps, client, text=text)
    assert e.value.code == "too_long"
    assert client.calls == []
