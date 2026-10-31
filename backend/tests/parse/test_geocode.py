from datetime import date

import httpx
import pytest

from trip_feasibility.checks.deps import Unavailable
from trip_feasibility.parse.geocode import geocode_stops, km_between, locate_city
from trip_feasibility.parse.normalise import normalise
from trip_feasibility.parse.schema import ExtractedStop
from trip_feasibility.sources.nominatim import search

SEARCH = "https://nominatim.test/search"
# south, north, west, east
PARIS_BBOX = ["48.8155755", "48.9021560", "2.2241220", "2.4697602"]


def result(osm_type, osm_id, name, lat, lon, *, bbox=None, country_code="fr", **extra):
    """A Nominatim /search result (jsonv2, addressdetails=1)."""
    return {
        "osm_type": osm_type,
        "osm_id": osm_id,
        "lat": str(lat),
        "lon": str(lon),
        "category": extra.get("category", "tourism"),
        "type": extra.get("type", "museum"),
        "name": name,
        "display_name": f"{name}, Paris, France",
        "address": {"country_code": country_code},
        "boundingbox": bbox or [str(lat), str(lat), str(lon), str(lon)],
    }


PARIS = result("relation", 71525, "Paris", 48.8535, 2.3484, bbox=PARIS_BBOX,
               category="boundary", type="administrative")  # fmt: skip
LOUVRE = result("relation", 7515426, "Louvre", 48.8611, 2.3358)
VERSAILLES = result("way", 85744442, "Château de Versailles", 48.8049, 2.1204)
NICE = result("relation", 1, "Promenade des Anglais", 43.6950, 7.2650)


def search_route(respx_mock, **params):
    return respx_mock.get(SEARCH, params=params)


@pytest.fixture
def paris(respx_mock):
    return search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[PARIS])


def stops(*names):
    return normalise(
        [ExtractedStop(day=1, place_name=n, type="venue") for n in names], date(2026, 12, 8)
    )


async def test_locate_city(check_deps, paris):
    city = await locate_city(check_deps, "Paris")
    assert city is not None
    assert city.country_code == "fr"
    assert city.place.bbox == (48.8155755, 48.902156, 2.224122, 2.4697602)


async def test_city_regions_from_the_address(check_deps, respx_mock):
    barcelona = result("relation", 347950, "Barcelona", 41.38, 2.17, country_code="es")
    barcelona["address"] |= {"ISO3166-2-lvl6": "ES-B", "ISO3166-2-lvl4": "ES-CT"}
    search_route(respx_mock, q="Barcelona", featureType="settlement").respond(json=[barcelona])
    city = await locate_city(check_deps, "Barcelona")
    assert city is not None
    assert city.regions == ["ES-CT", "ES-B"]  # broadest first


async def test_city_not_found(check_deps, respx_mock):
    respx_mock.get(SEARCH).respond(json=[])
    assert await locate_city(check_deps, "Atlantis") is None


async def test_search_inside_the_city_box(check_deps, paris, respx_mock):
    # Nominatim's viewbox is west,north,east,south
    viewbox = "2.224122,48.902156,2.4697602,48.8155755"
    inside = search_route(respx_mock, q="Louvre Museum", bounded="1", viewbox=viewbox)
    inside.respond(json=[LOUVRE])
    city = await locate_city(check_deps, "Paris")
    located, geocodes = await geocode_stops(check_deps, stops("Louvre Museum"), city)
    assert inside.called
    assert located[0].osm.id == 7515426 and located[0].lat == pytest.approx(48.8611)
    assert geocodes["s1"].outside_city is False


async def test_neighbourhoods_are_marked_as_areas(check_deps, paris, respx_mock):
    marais = result("relation", 2, "Le Marais", 48.859, 2.362, category="place", type="quarter")
    search_route(respx_mock, q="Le Marais", bounded="1").respond(json=[marais])
    search_route(respx_mock, q="Louvre", bounded="1").respond(json=[LOUVRE])
    city = await locate_city(check_deps, "Paris")
    located, _ = await geocode_stops(check_deps, stops("Le Marais", "Louvre"), city)
    assert [s.area for s in located] == [True, False]


async def test_day_trip_outside_the_city_box(check_deps, paris, respx_mock):
    search_route(respx_mock, q="Versailles", bounded="1").respond(json=[])
    search_route(respx_mock, q="Versailles", countrycodes="fr").respond(json=[NICE, VERSAILLES])
    city = await locate_city(check_deps, "Paris")
    located, geocodes = await geocode_stops(check_deps, stops("Versailles"), city)
    # Nice is the first result but 680 km away; Versailles is 17 km away
    assert located[0].osm.id == 85744442
    assert geocodes["s1"].outside_city is True


async def test_unresolved_places_are_flagged_not_guessed(check_deps, paris, respx_mock):
    search_route(respx_mock, q="Promenade des Anglais", bounded="1").respond(json=[])
    search_route(respx_mock, q="Promenade des Anglais", countrycodes="fr").respond(json=[NICE])
    city = await locate_city(check_deps, "Paris")
    located, geocodes = await geocode_stops(check_deps, stops("Promenade des Anglais"), city)
    assert located[0].osm is None and located[0].lat is None
    assert geocodes["s1"].reason == "no match in or within 60 km of Paris"


async def test_activity_named_stops_are_not_searched(check_deps, paris, respx_mock):
    # no search route for "Lunch": respx fails the test if it's requested
    city = await locate_city(check_deps, "Paris")
    located, geocodes = await geocode_stops(
        check_deps, stops("Lunch"), city, not_places=frozenset({"s1"})
    )
    assert located[0].lat is None
    assert geocodes["s1"].place is None and geocodes["s1"].reason.startswith("not a place")


async def test_each_name_is_searched_once_and_cached(check_deps, paris, respx_mock):
    inside = search_route(respx_mock, q="Louvre Museum", bounded="1").respond(json=[LOUVRE])
    city = await locate_city(check_deps, "Paris")
    located, _ = await geocode_stops(check_deps, stops("Louvre Museum", "Louvre Museum"), city)
    assert [s.osm.id for s in located] == [7515426, 7515426]
    await geocode_stops(check_deps, stops("Louvre Museum"), city)
    assert inside.call_count == 1
    assert paris.call_count == 1


async def test_overload_raises_unavailable_and_backs_off(check_deps, respx_mock):
    route = respx_mock.get(SEARCH).respond(503)
    with pytest.raises(Unavailable, match="503"):
        await search(check_deps, "Louvre")
    with pytest.raises(Unavailable, match="back off"):
        await search(check_deps, "Louvre")
    assert route.call_count == 1


async def test_network_error_raises_unavailable(check_deps, respx_mock):
    respx_mock.get(SEARCH).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(Unavailable, match="request failed"):
        await search(check_deps, "Louvre")


def test_km_between():
    assert km_between(48.8535, 2.3484, 48.8049, 2.1204) == pytest.approx(17.6, abs=0.5)
