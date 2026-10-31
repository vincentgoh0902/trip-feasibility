"""Public holidays check (Nager.Date): flags stops that fall on a public holiday.

A holiday can close a venue or crowd it; whether a specific venue actually closes is the
opening-hours check's job (OSM "PH off" rules). Severity here:
- warning: a nationwide (or matching regional) public holiday on a day with a venue stop
- info: the same day without venue stops, or a bank/optional/observance-only holiday,
  or a regional holiday when the trip's region is unknown
"""

from collections import defaultdict
from datetime import date, timedelta

import httpx
from pydantic import BaseModel, Field

from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.checks.models import CheckResult, Severity, Violation
from trip_feasibility.itinerary import Stop

CHECK_NAME = "public_holidays"


class Holiday(BaseModel):
    date: date
    local_name: str = Field(alias="localName")
    name: str
    global_: bool = Field(alias="global")
    counties: list[str] | None = None
    types: list[str] = []


def holidays_url(base_url: str, year: int, country_code: str) -> str:
    return f"{base_url}/PublicHolidays/{year}/{country_code.upper()}"


async def fetch_holidays(deps: CheckDeps, country_code: str, year: int) -> list[Holiday]:
    url = holidays_url(deps.settings.nager_base_url, year, country_code)

    async def fetch() -> list[dict]:
        try:
            r = await deps.http.get(url)
        except httpx.HTTPError as e:
            raise Unavailable(f"Nager.Date request failed: {e!r}") from e
        if r.status_code == 404:
            raise Unavailable(f"Nager.Date has no holidays for country {country_code!r}")
        if r.status_code != 200:
            raise Unavailable(f"Nager.Date returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise Unavailable("Nager.Date returned invalid JSON") from e

    key = f"nager:{country_code.upper()}:{year}"
    ttl = timedelta(days=deps.settings.cache_ttl_holidays_days)
    raw = await deps.cached("nager", key, ttl, fetch)
    return [Holiday.model_validate(h) for h in raw]


def _applies(holiday: Holiday, regions: list[str] | None) -> bool | None:
    """True if it applies to the trip's region, False if not, None if unknown."""
    if holiday.global_ or not holiday.counties:
        return True
    if not regions:
        return None
    return any(r.upper() in holiday.counties for r in regions)


async def check_public_holidays(
    deps: CheckDeps, stops: list[Stop], country_code: str, regions: list[str] | None = None
) -> CheckResult:
    """regions: the ISO 3166-2 codes the trip is in, at any level (e.g. ["FR-GES", "FR-67"]),
    to resolve regional holidays; optional."""
    if not stops:
        return CheckResult(check_name=CHECK_NAME, status="ok")
    try:
        by_date: dict[date, list[Holiday]] = defaultdict(list)
        for year in sorted({s.date.year for s in stops}):
            for h in await fetch_holidays(deps, country_code, year):
                by_date[h.date].append(h)
    except Unavailable as e:
        return CheckResult.unavailable(CHECK_NAME, str(e))

    stops_by_date: dict[date, list[Stop]] = defaultdict(list)
    for s in stops:
        stops_by_date[s.date].append(s)

    violations = []
    for day, day_stops in sorted(stops_by_date.items()):
        for h in by_date.get(day, []):
            applies = _applies(h, regions)
            if applies is False:
                continue
            has_venue = any(s.type == "venue" for s in day_stops)
            severity: Severity = (
                "warning" if applies and has_venue and "Public" in h.types else "info"
            )
            where = "nationwide" if h.global_ else f"in {', '.join(h.counties or [])}"
            message = f"{day.isoformat()} is {h.name} ({h.local_name}), a holiday {where}."
            if applies is None:
                message += " It may not apply to your region."
            elif has_venue:
                message += " Venues may be closed, have reduced hours or be crowded."
            else:
                message += " Expect reduced public transport and some closures."
            violations.append(
                Violation(
                    check_name=CHECK_NAME,
                    severity=severity,
                    stop_ids=[s.id for s in day_stops],
                    message=message,
                    evidence={
                        "date": day.isoformat(),
                        "name": h.name,
                        "local_name": h.local_name,
                        "global": h.global_,
                        "counties": h.counties,
                        "types": h.types,
                        "country_code": country_code.upper(),
                    },
                    source=holidays_url(deps.settings.nager_base_url, day.year, country_code),
                )
            )
    return CheckResult(
        check_name=CHECK_NAME,
        status="ok",
        violations=violations,
        checked_stop_ids=[s.id for s in stops],
    )
