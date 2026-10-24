"""Parse step 4: place names → OpenStreetMap places, in code (the LLM never gives coordinates).

The trip's city is located first: its bounding box scopes every search, and its country code
is what the holiday check needs. Each place name is searched inside the city's box; if
nothing matches, anywhere in the country within MAX_KM_FROM_CITY of the centre (day trips
such as Versailles), marked outside_city. Anything else is left unresolved, never guessed.
All calls go through Nominatim's rate limiter and are cached.
"""

from math import asin, cos, radians, sin, sqrt

from pydantic import BaseModel

from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.itinerary import Stop
from trip_feasibility.sources.nominatim import Place, search

MAX_KM_FROM_CITY = 60
CANDIDATES = 5


class City(BaseModel):
    name: str
    place: Place

    @property
    def country_code(self) -> str | None:
        return self.place.country_code


class Geocode(BaseModel):
    place_name: str
    place: Place | None = None
    outside_city: bool = False
    reason: str | None = None  # why it's unresolved


def km_between(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance (haversine)."""
    d_lat, d_lon = radians(b_lat - a_lat), radians(b_lon - a_lon)
    h = sin(d_lat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(d_lon / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


async def locate_city(deps: CheckDeps, name: str) -> City | None:
    found = await search(deps, name, feature_type="settlement", limit=1)
    if not found or found[0].bbox is None:
        return None
    return City(name=name, place=found[0])


async def geocode_place(deps: CheckDeps, place_name: str, city: City) -> Geocode:
    assert city.place.bbox is not None
    query = place_name.strip()
    inside = await search(deps, query, viewbox=city.place.bbox, limit=CANDIDATES)
    if inside:
        return Geocode(place_name=place_name, place=inside[0])
    nearby = await search(deps, query, country_code=city.country_code, limit=CANDIDATES)
    for p in nearby:
        if km_between(city.place.lat, city.place.lon, p.lat, p.lon) <= MAX_KM_FROM_CITY:
            return Geocode(place_name=place_name, place=p, outside_city=True)
    return Geocode(
        place_name=place_name,
        reason=f"no match in or within {MAX_KM_FROM_CITY} km of {city.name}",
    )


async def geocode_stops(
    deps: CheckDeps, stops: list[Stop], city: City
) -> tuple[list[Stop], dict[str, Geocode]]:
    """Stops with osm/lat/lon filled in where found, and the geocode for every stop id.
    Each distinct place name is searched once."""
    by_name: dict[str, Geocode] = {}
    for s in stops:
        if s.place_name not in by_name:
            by_name[s.place_name] = await geocode_place(deps, s.place_name, city)
    located, results = [], {}
    for s in stops:
        g = by_name[s.place_name]
        results[s.id] = g
        if g.place is not None:
            s = s.model_copy(update={"osm": g.place.osm, "lat": g.place.lat, "lon": g.place.lon})
        located.append(s)
    return located, results
