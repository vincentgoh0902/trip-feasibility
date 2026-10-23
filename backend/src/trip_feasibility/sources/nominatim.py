"""Nominatim (OpenStreetMap's search service): tags of known OSM places.

Used instead of Overpass, whose public servers are overloaded and ask automated clients to
self-host (see docs/ARCHITECTURE.md). Usage policy: identify the app, at most 1 request per
second, cache results, no bulk downloads. https://operations.osmfoundation.org/policies/nominatim/
"""

from datetime import timedelta
from typing import Any

import httpx

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


async def _lookup(deps: CheckDeps, refs: list[OsmRef]) -> list[dict[str, Any]]:
    await deps.nominatim_limiter.wait()
    params = {
        "osm_ids": ",".join(f"{_PREFIX[r.type]}{r.id}" for r in refs),
        "format": "jsonv2",
        "extratags": "1",
    }
    try:
        r = await deps.http.get(
            f"{deps.settings.nominatim_base_url}/lookup",
            params=params,
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


async def fetch_osm_tags(deps: CheckDeps, refs: list[OsmRef]) -> dict[str, dict[str, Any] | None]:
    """Tags per tags_key(ref); None when Nominatim doesn't know the place (deleted from OSM,
    or not something Nominatim indexes). Cached; only uncached places are requested."""
    keys = {tags_key(r): r for r in refs}
    found: dict[str, Any] = await deps.cache.get_many(list(keys))
    missing = [r for k, r in keys.items() if k not in found]
    for i in range(0, len(missing), LOOKUP_BATCH):
        batch = missing[i : i + LOOKUP_BATCH]
        fetched: dict[str, Any] = {tags_key(r): {"missing": True} for r in batch}
        for place in await _lookup(deps, batch):
            ref = OsmRef(type=place["osm_type"], id=place["osm_id"])
            fetched[tags_key(ref)] = {"tags": _place_tags(place)}
        await deps.cache.put_many(
            "nominatim", fetched, timedelta(days=deps.settings.cache_ttl_osm_days)
        )
        found.update(fetched)
    return {k: (None if v.get("missing") else v["tags"]) for k, v in found.items()}
