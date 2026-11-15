"""Wikidata: a place's official website (property P856), to confirm its OSM `website` tag.

Anyone can edit an OSM tag, so a closures finding from the tagged site counts as official
only when Wikidata, edited and watched separately, lists the same site (decision 95). One
`wbgetclaims` request per item, cached; Wikidata asks for an identifying User-Agent and no
parallel bursts, so requests go one at a time.
"""

import re
from datetime import timedelta
from typing import Any

import httpx

from trip_feasibility.checks.deps import CheckDeps, Unavailable

OFFICIAL_WEBSITE = "P856"
_QID = re.compile(r"^Q[1-9]\d*$")


def qid(tags: dict[str, Any]) -> str | None:
    """The OSM `wikidata` tag, if it is one valid item ID."""
    value = tags.get("wikidata")
    return value if isinstance(value, str) and _QID.match(value) else None


async def _claims(deps: CheckDeps, item: str) -> list[str]:
    try:
        r = await deps.http.get(
            f"{deps.settings.wikidata_base_url}/w/api.php",
            params={
                "action": "wbgetclaims",
                "entity": item,
                "property": OFFICIAL_WEBSITE,
                "format": "json",
            },  # fmt: skip
        )
    except httpx.HTTPError as e:
        raise Unavailable(f"Wikidata request failed: {e!r}") from e
    if r.status_code != 200:
        raise Unavailable(f"Wikidata returned HTTP {r.status_code}")
    try:
        claims = r.json().get("claims", {}).get(OFFICIAL_WEBSITE, [])
    except (ValueError, AttributeError) as e:
        raise Unavailable("Wikidata returned invalid JSON") from e
    sites = []
    for c in claims:
        value = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if c.get("rank") != "deprecated" and isinstance(value, str):
            sites.append(value)
    return sites


async def official_websites(deps: CheckDeps, items: list[str]) -> dict[str, list[str]]:
    """{item: its official websites} (an empty list when Wikidata gives none). Raises
    Unavailable if Wikidata can't be reached; failures aren't cached."""
    ttl = timedelta(days=deps.settings.cache_ttl_wikidata_days)
    out: dict[str, list[str]] = {}
    for item in dict.fromkeys(items):

        async def fetch(item: str = item) -> list[str]:
            return await _claims(deps, item)

        out[item] = await deps.cached("wikidata", f"wikidata:p856:{item}", ttl, fetch)
    return out
