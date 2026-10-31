"""Parse step 4: place names → OpenStreetMap places, in code (the LLM never gives coordinates).

The trip's city is located first: its bounding box scopes every search, and its country code
is what the holiday check needs. Each place name is then resolved in this order (decision 82):
1. Inside the city's box. The top result is taken when one of its names (OSM's name, name:en,
   alt_name ...) is exactly the text's name.
2. Otherwise also anywhere in the country within MAX_KM_FROM_CITY of the centre (day trips).
   A result there whose name is exact wins over an inexact one in the box: "Versailles" is
   the town 15 km out, not "Paris Expo Porte de Versailles" in the city.
3. Otherwise the box's top result, as Nominatim ranked it.
4. Otherwise the nearby result, but only when its name matches (exactly, or one name inside
   the other): Nominatim's loose matches far from the city are other places that share a word.
5. Otherwise, if the name ends in a word saying what kind of place it is ("Tibidabo Amusement
   Park"), the rest of the name, accepting only a result of that kind (a theme park).
Anything else is left unresolved, never guessed. Results outside the box are marked
outside_city. All calls go through Nominatim's rate limiter and are cached.
"""

import re
import unicodedata
from math import asin, cos, radians, sin, sqrt
from typing import Any

from pydantic import BaseModel

from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.itinerary import Stop
from trip_feasibility.sources.nominatim import Place, search

MAX_KM_FROM_CITY = 60
CANDIDATES = 5
# Nominatim results that are a neighbourhood or district rather than a place you go to
AREA_TYPES = {
    ("place", t)
    for t in ("suburb", "quarter", "neighbourhood", "borough", "city_district", "locality")
} | {("boundary", "administrative"), ("boundary", "tourist_zone"), ("historic", "district")}
# Trailing words that say what kind of place a name is, and the OSM types that fit them
_KINDS: dict[str, set[tuple[str, str]]] = {
    "amusement park": {("tourism", "theme_park")},
    "theme park": {("tourism", "theme_park")},
    "market": {("amenity", "marketplace")},
    "museum": {("tourism", "museum")},
    "gallery": {("tourism", "gallery"), ("tourism", "museum"), ("amenity", "arts_centre")},
    "abbey": {("amenity", "monastery"), ("amenity", "place_of_worship")},
    "monastery": {("amenity", "monastery"), ("amenity", "place_of_worship")},
    "cathedral": {("amenity", "place_of_worship"), ("building", "cathedral")},
    "basilica": {("amenity", "place_of_worship"), ("building", "church")},
    "church": {("amenity", "place_of_worship"), ("building", "church")},
    "gardens": {("leisure", "park"), ("leisure", "garden")},
    "garden": {("leisure", "park"), ("leisure", "garden")},
    "park": {("leisure", "park"), ("leisure", "garden")},
    "palace": {("building", "palace"), ("historic", "castle"), ("tourism", "attraction")},
    "castle": {("historic", "castle"), ("building", "castle")},
    "hotel": {("tourism", "hotel")},
}
_ARTICLES = re.compile(r"^(the|le|la|les|l|el|els|il|lo|der|die|das) ")


def _norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c)).lower()
    return _ARTICLES.sub("", " ".join(re.sub(r"[^a-z0-9]+", " ", name).split()))


def name_match(query: str, place: Place) -> float:
    """1.0 when one of the place's names is the query, 0.9 when one contains the other as
    whole words ("Louvre" / "Musée du Louvre"), else 0."""
    q = _norm(query)
    names = {_norm(n) for n in [place.name, *place.names]} - {""}
    if not q or not names:
        return 0.0
    if q in names:
        return 1.0
    padded = f" {q} "
    if any(f" {n} " in padded or padded in f" {n} " for n in names):
        return 0.9
    return 0.0


def _kind(query: str) -> tuple[str, set[tuple[str, str]]] | None:
    """("Tibidabo", theme park types) for "Tibidabo Amusement Park"; None without a kind word."""
    words = query.strip().split()
    for n in (2, 1):
        if len(words) > n and (kind := " ".join(words[-n:]).lower()) in _KINDS:
            core = " ".join(words[:-n])
            return (core, _KINDS[kind]) if len(_norm(core)) >= 3 else None
    return None


def is_area(place: Place) -> bool:
    return (place.category, place.type) in AREA_TYPES


def is_area_tags(tags: dict[str, Any]) -> bool:
    """The same test on a place's OSM tags (from a /lookup by ref)."""
    return any((k, v) in AREA_TYPES for k, v in tags.items())


class City(BaseModel):
    name: str
    place: Place

    @property
    def country_code(self) -> str | None:
        return self.place.country_code

    @property
    def regions(self) -> list[str]:
        """ISO 3166-2 codes the city is in, for regional holidays."""
        return self.place.regions


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


def _in_box(place: Place, city: City) -> bool:
    assert city.place.bbox is not None
    south, north, west, east = city.place.bbox
    return south <= place.lat <= north and west <= place.lon <= east


async def _nearby(deps: CheckDeps, query: str, city: City) -> list[Place]:
    """Results anywhere in the country within MAX_KM_FROM_CITY whose name matches."""
    found = await search(deps, query, country_code=city.country_code, limit=CANDIDATES)
    return [
        p
        for p in found
        if km_between(city.place.lat, city.place.lon, p.lat, p.lon) <= MAX_KM_FROM_CITY
        and name_match(query, p) >= 0.9
    ]


def _found(place_name: str, place: Place, city: City) -> Geocode:
    return Geocode(place_name=place_name, place=place, outside_city=not _in_box(place, city))


async def geocode_place(deps: CheckDeps, place_name: str, city: City) -> Geocode:
    """See the module docstring for the order."""
    assert city.place.bbox is not None
    query = place_name.strip()
    inside = await search(deps, query, viewbox=city.place.bbox, limit=CANDIDATES)
    if inside and name_match(query, inside[0]) == 1.0:
        return _found(place_name, inside[0], city)
    nearby = await _nearby(deps, query, city)
    exact_outside = [p for p in nearby if name_match(query, p) == 1.0 and not _in_box(p, city)]
    if exact_outside:
        return _found(place_name, exact_outside[0], city)
    if inside:
        return _found(place_name, inside[0], city)
    if nearby:
        return _found(place_name, nearby[0], city)
    if (kind := _kind(query)) is not None:
        core, types = kind
        inside = await search(deps, core, viewbox=city.place.bbox, limit=CANDIDATES)
        candidates = inside + await _nearby(deps, core, city)
        for p in candidates:
            if (p.category, p.type) in types:
                return _found(place_name, p, city)
    return Geocode(
        place_name=place_name,
        reason=f"no match in or within {MAX_KM_FROM_CITY} km of {city.name}",
    )


async def geocode_stops(
    deps: CheckDeps, stops: list[Stop], city: City, not_places: frozenset[str] = frozenset()
) -> tuple[list[Stop], dict[str, Geocode]]:
    """Stops with osm/lat/lon filled in where found, and the geocode for every stop id.
    Each distinct place name is searched once. Stops in `not_places` (named by the activity,
    "Lunch") aren't searched: a café called "Lunch" would be a guess."""
    by_name: dict[str, Geocode] = {}
    for s in stops:
        if s.id not in not_places and s.place_name not in by_name:
            by_name[s.place_name] = await geocode_place(deps, s.place_name, city)
    located, results = [], {}
    for s in stops:
        if s.id in not_places:
            results[s.id] = Geocode(
                place_name=s.place_name, reason="not a place: no location given"
            )
            located.append(s)
            continue
        g = by_name[s.place_name]
        results[s.id] = g
        if g.place is not None:
            s = s.model_copy(
                update={
                    "osm": g.place.osm,
                    "lat": g.place.lat,
                    "lon": g.place.lon,
                    "area": is_area(g.place),
                }
            )
        located.append(s)
    return located, results
