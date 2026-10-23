from datetime import date

import httpx
import pytest

from tests.factories import stop
from trip_feasibility.checks.holidays import check_public_holidays

NAGER = "https://nager.test/api/v3/PublicHolidays"


def holiday(on: str, name: str, *, global_=True, counties=None, types=("Public",)):
    return {
        "date": on,
        "localName": f"local {name}",
        "name": name,
        "countryCode": "XX",
        "fixed": False,
        "global": global_,
        "counties": counties,
        "launchYear": None,
        "types": list(types),
    }


FR_2026 = [holiday("2026-07-14", "Bastille Day"), holiday("2026-12-25", "Christmas Day")]
DE_2026 = [
    holiday("2026-01-06", "Epiphany", global_=False, counties=["DE-BW", "DE-BY", "DE-ST"]),
    holiday("2026-08-15", "Assumption Day", global_=False, counties=["DE-BY"]),
]


@pytest.fixture
def fr(respx_mock):
    return respx_mock.get(f"{NAGER}/2026/FR").respond(json=FR_2026)


async def test_venue_on_national_holiday_is_a_warning(check_deps, fr):
    stops = [
        stop("a", date(2026, 7, 14), "10:00", "12:00"),
        stop("b", date(2026, 7, 14), "13:00", "14:00", type="meal"),
        stop("c", date(2026, 7, 15), "10:00", "12:00"),
    ]
    result = await check_public_holidays(check_deps, stops, "fr")
    assert result.status == "ok"
    assert result.checked_stop_ids == ["a", "b", "c"]
    [v] = result.violations
    assert v.severity == "warning"
    assert v.stop_ids == ["a", "b"]
    assert v.evidence["name"] == "Bastille Day"
    assert v.source == f"{NAGER}/2026/FR"


async def test_holiday_without_venue_stops_is_info(check_deps, fr):
    stops = [stop("a", date(2026, 12, 25), "19:00", "21:00", type="meal")]
    [v] = (await check_public_holidays(check_deps, stops, "FR")).violations
    assert v.severity == "info"


async def test_no_holiday_no_violation(check_deps, fr):
    result = await check_public_holidays(check_deps, [stop("a", date(2026, 7, 16))], "FR")
    assert result.status == "ok"
    assert result.violations == []


@pytest.mark.parametrize(
    ("region", "expected"),
    [("DE-BY", "warning"), ("de-by", "warning"), ("DE-BE", None), (None, "info")],
)
async def test_regional_holidays(check_deps, respx_mock, region, expected):
    respx_mock.get(f"{NAGER}/2026/DE").respond(json=DE_2026)
    stops = [stop("a", date(2026, 1, 6), "10:00", "12:00")]
    result = await check_public_holidays(check_deps, stops, "DE", region)
    severities = [v.severity for v in result.violations]
    assert severities == ([expected] if expected else [])
    if region is None:
        assert "may not apply" in result.violations[0].message


async def test_bank_only_holiday_is_info(check_deps, respx_mock):
    respx_mock.get(f"{NAGER}/2026/GB").respond(
        json=[holiday("2026-08-31", "Summer Bank Holiday", types=["Bank"])]
    )
    stops = [stop("a", date(2026, 8, 31), "10:00", "12:00")]
    [v] = (await check_public_holidays(check_deps, stops, "GB")).violations
    assert v.severity == "info"


async def test_trip_over_new_year_fetches_both_years(check_deps, respx_mock):
    y26 = respx_mock.get(f"{NAGER}/2026/FR").respond(json=FR_2026)
    y27 = respx_mock.get(f"{NAGER}/2027/FR").respond(json=[holiday("2027-01-01", "New Year")])
    stops = [stop("a", date(2026, 12, 31)), stop("b", date(2027, 1, 1))]
    result = await check_public_holidays(check_deps, stops, "FR")
    assert y26.call_count == y27.call_count == 1
    assert [v.stop_ids for v in result.violations] == [["b"]]


async def test_second_check_uses_cache(check_deps, fr):
    stops = [stop("a", date(2026, 7, 14))]
    await check_public_holidays(check_deps, stops, "FR")
    result = await check_public_holidays(check_deps, stops, "FR")
    assert fr.call_count == 1
    assert len(result.violations) == 1


@pytest.mark.parametrize(
    ("response", "error_part"),
    [
        (httpx.Response(404), "no holidays for country"),
        (httpx.Response(500), "HTTP 500"),
        (httpx.ConnectError("boom"), "request failed"),
        (httpx.Response(200, text="not json"), "invalid JSON"),
    ],
)
async def test_source_failures_make_the_check_unavailable(
    check_deps, respx_mock, response, error_part
):
    route = respx_mock.get(f"{NAGER}/2026/ZZ")
    if isinstance(response, Exception):
        route.side_effect = response
    else:
        route.return_value = response
    result = await check_public_holidays(check_deps, [stop("a", date(2026, 7, 14))], "ZZ")
    assert result.status == "unavailable"
    assert error_part in result.error
    assert result.violations == []


async def test_failures_are_not_cached(check_deps, respx_mock):
    route = respx_mock.get(f"{NAGER}/2026/FR")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json=FR_2026)]
    stops = [stop("a", date(2026, 7, 14))]
    assert (await check_public_holidays(check_deps, stops, "FR")).status == "unavailable"
    assert len((await check_public_holidays(check_deps, stops, "FR")).violations) == 1
