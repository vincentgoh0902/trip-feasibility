"""Nominatim (OpenStreetMap's search service): tags of known OSM places, and geocoding.

Used instead of Overpass, whose public servers are overloaded and ask automated clients to
self-host (see docs/ARCHITECTURE.md). Usage policy: identify the app, at most 1 request per
second, cache results, no bulk downloads. https://operations.osmfoundation.org/policies/nominatim/
"""

import json
from datetime import timedelta
from typing import Any

import httpx
from pydantic import BaseModel

from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.itinerary import OsmRef

LOOKUP_BATCH = 50  # Nominatim's maximum IDs per /lookup request
_PREFIX = {"node": "N", "way": "W", "relation": "R"}
_BACK_OFF_STATUSES = {403, 429, 503}


def tags_key(ref: OsmRef) -> str:
    return f"osm-tags:{ref.type}:{ref.id}"


def _place_tags(place: dict[str, Any]) -> dict[str, Any]:
    """OSM tags as far as Nominatim exposes them: its extratags plus the main tag
    (e.g. tourism=museum), which it reports separately as category/type."""
    tags = dict(place.get("extratags") or {})
    if place.get("category") and place.get("type"):
        tags.setdefault(place["category"], place["type"])
    if place.get("name"):
        tags.setdefault("name", place["name"])
    return tags


async def _get(deps: CheckDeps, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """One rate-limited request; every failure becomes Unavailable (and 403/429/503 a pause)."""
    await deps.nominatim_limiter.wait()
    try:
        r = await deps.http.get(
            f"{deps.settings.nominatim_base_url}/{path}",
            params={**params, "format": "jsonv2"},
            timeout=deps.settings.nominatim_timeout_s,
        )
    except httpx.HTTPError as e:
        raise Unavailable(f"Nominatim request failed: {e!r}") from e
    if r.status_code in _BACK_OFF_STATUSES:
        deps.nominatim_limiter.back_off()
        raise Unavailable(f"Nominatim returned HTTP {r.status_code}; backing off")
    if r.status_code != 200:
        raise Unavailable(f"Nominatim returned HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise Unavailable("Nominatim returned invalid JSON") from e
    if not isinstance(body, list):
        raise Unavailable(f"Nominatim returned an unexpected response: {str(body)[:200]}")
    return body


async def _lookup(deps: CheckDeps, refs: list[OsmRef]) -> list[dict[str, Any]]:
    osm_ids = ",".join(f"{_PREFIX[r.type]}{r.id}" for r in refs)
    return await _get(deps, "lookup", {"osm_ids": osm_ids, "extratags": "1"})


async def _cached_lookup(deps: CheckDeps, refs: list[OsmRef]) -> dict[str, dict[str, Any]]:
    """Cached /lookup result per tags_key(ref): {"tags", "lat", "lon"} or {"missing": True}.
    Only uncached places (or entries cached before coordinates were stored) are requested."""
    keys = {tags_key(r): r for r in refs}
    found: dict[str, Any] = await deps.cache.get_many(list(keys))
    found = {k: v for k, v in found.items() if v.get("missing") or "lat" in v}
    missing = [r for k, r in keys.items() if k not in found]
    for i in range(0, len(missing), LOOKUP_BATCH):
        batch = missing[i : i + LOOKUP_BATCH]
        fetched: dict[str, Any] = {tags_key(r): {"missing": True} for r in batch}
        for place in await _lookup(deps, batch):
            ref = OsmRef(type=place["osm_type"], id=place["osm_id"])
            fetched[tags_key(ref)] = {
                "tags": _place_tags(place),
                "lat": float(place["lat"]) if "lat" in place else None,
                "lon": float(place["lon"]) if "lon" in place else None,
            }
        await deps.cache.put_many(
            "nominatim", fetched, timedelta(days=deps.settings.cache_ttl_osm_days)
        )
        found.update(fetched)
    return found


async def fetch_osm_tags(deps: CheckDeps, refs: list[OsmRef]) -> dict[str, dict[str, Any] | None]:
    """Tags per tags_key(ref); None when Nominatim doesn't know the place (deleted from OSM,
    or not something Nominatim indexes)."""
    found = await _cached_lookup(deps, refs)
    return {k: (None if v.get("missing") else v["tags"]) for k, v in found.items()}


async def fetch_osm_points(
    deps: CheckDeps, refs: list[OsmRef]
) -> dict[str, tuple[float, float] | None]:
    """(lat, lon) per tags_key(ref); None when Nominatim doesn't know the place."""
    found = await _cached_lookup(deps, refs)
    return {
        k: None if v.get("missing") or v["lat"] is None else (v["lat"], v["lon"])
        for k, v in found.items()
    }


class Place(BaseModel):
    """One Nominatim search result."""

    osm: OsmRef
    lat: float
    lon: float
    name: str
    display_name: str
    category: str
    type: str
    country_code: str | None = None
    # south, north, west, east
    bbox: tuple[float, float, float, float] | None = None


def _place(raw: dict[str, Any]) -> Place | None:
    if raw.get("osm_type") not in _PREFIX:
        return None
    bbox = raw.get("boundingbox")
    return Place(
        osm=OsmRef(type=raw["osm_type"], id=raw["osm_id"]),
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        name=raw.get("name") or "",
        display_name=raw.get("display_name") or "",
        category=raw.get("category") or "",
        type=raw.get("type") or "",
        country_code=(raw.get("address") or {}).get("country_code"),
        bbox=tuple(float(x) for x in bbox) if bbox and len(bbox) == 4 else None,
    )


async def search(
    deps: CheckDeps,
    query: str,
    *,
    viewbox: tuple[float, float, float, float] | None = None,
    country_code: str | None = None,
    feature_type: str | None = None,
    limit: int = 5,
) -> list[Place]:
    """Free-text search, best match first. viewbox (south, north, west, east) restricts results
    to that box. Cached, including "no results"; never use it for autocomplete (policy)."""
    params = {"q": query, "limit": str(limit), "addressdetails": "1"}
    if viewbox is not None:
        south, north, west, east = viewbox
        params |= {"viewbox": f"{west},{north},{east},{south}", "bounded": "1"}
    if country_code:
        params["countrycodes"] = country_code.lower()
    if feature_type:
        params["featureType"] = feature_type
    key = "nominatim-search:" + json.dumps(params, sort_keys=True, ensure_ascii=False)

    async def fetch() -> list[dict[str, Any]]:
        return await _get(deps, "search", params)

    raw = await deps.cached(
        "nominatim", key, timedelta(days=deps.settings.cache_ttl_osm_days), fetch
    )
    return [p for p in map(_place, raw) if p is not None]
