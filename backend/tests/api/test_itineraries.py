import pytest

from tests.api.conftest import ANA, BOB, ITINERARY, stop_in
from tests.parse.test_extract import GOOD, FakeClient, reply
from tests.parse.test_geocode import LOUVRE, PARIS, search_route

V1 = "/api/v1/itineraries"


async def save(api, headers=ANA, **changes):
    r = await api.post(V1, json=ITINERARY | changes, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_save_and_read_back(api):
    it = await save(api)
    [version] = it["versions"]
    assert version["version_no"] == 1 and version["source"] == "user"
    louvre, cafe = version["stops"]
    assert (louvre["id"], louvre["date"], louvre["lat"]) == ("s1", "2026-12-08", 48.8611)
    assert cafe["type"] == "meal"
    r = await api.get(f"{V1}/{it['id']}", headers=ANA)
    assert r.json()["versions"][0]["raw_text"] == ITINERARY["raw_text"]


async def test_time_of_day_and_later_days(api):
    stops = [{"day": 2, "time_of_day": "evening", "place_name": "Bateaux Parisiens",
              "type": "meal"}]  # fmt: skip
    it = await save(api, stops=stops)
    [stop] = it["versions"][0]["stops"]
    assert (stop["date"], stop["start_time"], stop["end_time"]) == (
        "2026-12-09",
        "18:00:00",
        "21:00:00",
    )
    assert stop["lat"] is None  # looked up when the check runs


async def test_list_rename_and_new_version(api):
    first = await save(api, title="A")
    await save(api, title="B")
    listed = (await api.get(V1, headers=ANA)).json()
    assert [i["title"] for i in listed] == ["B", "A"]
    r = await api.patch(f"{V1}/{first['id']}", json={"title": "Renamed"}, headers=ANA)
    assert r.json()["title"] == "Renamed"
    r = await api.post(
        f"{V1}/{first['id']}/versions",
        json={"stops": [stop_in("Musée d'Orsay", "10:00", "12:00")]},
        headers=ANA,
    )
    assert r.status_code == 201
    assert r.json()["version_no"] == 2
    detail = (await api.get(f"{V1}/{first['id']}", headers=ANA)).json()
    assert [v["version_no"] for v in detail["versions"]] == [2, 1]
    summary = next(i for i in (await api.get(V1, headers=ANA)).json() if i["id"] == first["id"])
    assert summary["versions"] == 2 and summary["latest_version_id"] == r.json()["id"]


async def test_delete(api):
    it = await save(api)
    assert (await api.delete(f"{V1}/{it['id']}", headers=ANA)).status_code == 204
    assert (await api.get(f"{V1}/{it['id']}", headers=ANA)).status_code == 404


async def test_another_users_itinerary_is_not_found(api):
    it = await save(api)
    assert (await api.get(V1, headers=BOB)).json() == []
    path = f"{V1}/{it['id']}"
    for method, url, body in [
        ("GET", path, None),
        ("PATCH", path, {"title": "mine now"}),
        ("DELETE", path, None),
        ("POST", f"{path}/versions", {"stops": ITINERARY["stops"]}),
    ]:
        r = await api.request(method, url, json=body, headers=BOB)
        assert r.status_code == 404, (method, url)
    assert (await api.get(path, headers=ANA)).json()["title"] == ITINERARY["title"]


@pytest.mark.parametrize(
    "bad",
    [
        {"stops": []},
        {"stops": [{"day": 0, "place_name": "x", "type": "venue"}]},
        {"stops": [{"day": 1, "place_name": "x", "type": "venue", "lat": 48.0}]},
        {
            "stops": [
                {
                    "day": 1,
                    "place_name": "x",
                    "type": "venue",
                    "time_of_day": "morning",
                    "start_time": "09:00",
                }
            ]
        },  # fmt: skip
    ],
)
async def test_invalid_stops_are_422(api, bad):
    r = await api.post(V1, json=ITINERARY | bad, headers=ANA)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


# --- parsing ---

PARSE = {"text": "9-12 Louvre Museum", "city": "Paris", "start_date": "2026-12-08"}


async def test_parse_needs_sign_in_and_a_key(api):
    assert (await api.post(f"{V1}/parse", json=PARSE)).status_code == 401
    r = await api.post(f"{V1}/parse", json=PARSE, headers=ANA)
    assert r.status_code == 503  # no ANTHROPIC_API_KEY in tests


async def test_parse_returns_located_stops_and_cost(api, services, respx_mock):
    search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[PARIS])
    search_route(respx_mock, q="Louvre Museum").respond(json=[LOUVRE])
    services.llm = FakeClient(reply(GOOD, input_tokens=1000, output_tokens=200))
    r = await api.post(f"{V1}/parse", json=PARSE, headers=ANA)
    assert r.status_code == 200, r.text
    body = r.json()
    [stop] = body["stops"]
    assert stop["resolved"] and stop["osm"] == {"type": "relation", "id": 7515426}
    assert body["country_code"] == "fr"
    assert body["usage"]["calls"] == 1
    assert body["usage"]["cost_usd"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)


async def test_parse_errors_keep_their_code(api, services, respx_mock):
    search_route(respx_mock, q="Atlantis", featureType="settlement").respond(json=[])
    services.llm = FakeClient()
    r = await api.post(f"{V1}/parse", json=PARSE | {"city": "Atlantis"}, headers=ANA)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "city_not_found"
    r = await api.post(f"{V1}/parse", json=PARSE | {"text": "x" * 50_001}, headers=ANA)
    assert r.json()["error"]["code"] == "too_long"


async def test_parse_is_rate_limited(api, services, respx_mock):
    search_route(respx_mock, q="Atlantis", featureType="settlement").respond(json=[])
    services.llm = FakeClient()
    services.parse_limit.per_hour = 2
    codes = [
        (await api.post(f"{V1}/parse", json=PARSE | {"city": "Atlantis"}, headers=ANA)).status_code
        for _ in range(3)
    ]
    assert codes == [422, 422, 429]
    # per user: someone else still can
    r = await api.post(f"{V1}/parse", json=PARSE | {"city": "Atlantis"}, headers=BOB)
    assert r.status_code == 422
