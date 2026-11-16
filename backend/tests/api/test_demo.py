"""Parsing a pasted sample without signing in (decision 100): prepared replies only."""

from datetime import date

from tests.parse.test_extract import GOOD, FakeClient, reply
from trip_feasibility.api.itineraries import parse_text
from trip_feasibility.api.schemas import ParseRequest
from trip_feasibility.demo import samples
from trip_feasibility.prepared import PreparingClient

V1 = "/api/v1/demo"
PARIS_SAMPLE = samples()["paris-one-day"]
BODY = {"text": PARIS_SAMPLE.text, "city": "Paris", "start_date": "2026-12-08"}


async def prepare(services):
    """What demo.warm does for the sample's text: one extraction, stored."""
    preparing = PreparingClient(FakeClient(reply(GOOD)), services.deps.cache)
    req = ParseRequest(text=PARIS_SAMPLE.text, city="Paris", start_date=date(2026, 12, 8))
    await parse_text(services, preparing, req)
    assert preparing.stored == 1


async def test_a_pasted_sample_parses_from_its_prepared_reply(api, services, nominatim):
    await prepare(services)
    services.llm = model = FakeClient()  # would fail if called: nothing is left to answer
    # a copy that lost its line breaks and gained spaces is still the sample
    pasted = "  " + " ".join(PARIS_SAMPLE.text.split()) + "\n\n"
    r = await api.post(f"{V1}/parse", json=BODY | {"text": pasted})
    assert r.status_code == 200, r.text
    body = r.json()
    [stop] = body["stops"]
    assert stop["place_name"] == "Louvre Museum" and stop["resolved"]
    assert body["usage"]["cost_usd"] == 0
    assert model.calls == []


async def test_other_text_or_another_date_is_refused_without_a_model_call(api, services, nominatim):
    await prepare(services)
    services.llm = model = FakeClient()
    for body in (BODY | {"text": "9-12 Louvre, 13-14 lunch"}, BODY | {"start_date": "2026-12-09"}):
        r = await api.post(f"{V1}/parse", json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "not_prepared"
        assert "sample itineraries" in r.json()["error"]["message"]
    assert model.calls == []


async def test_demo_parsing_is_rate_limited_per_ip(api, services, nominatim):
    services.parse_limit.per_hour = 1
    assert (await api.post(f"{V1}/parse", json=BODY)).status_code == 422  # not prepared
    r = await api.post(f"{V1}/parse", json=BODY)
    assert r.status_code == 429
