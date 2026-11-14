"""Closures check against a fake model client and mocked pages: no real search or fetch."""

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from tests.factories import stop
from trip_feasibility.checks import closures
from trip_feasibility.checks.closures import Target, check_closures, validate_findings
from trip_feasibility.itinerary import OsmRef
from trip_feasibility.sources import web
from trip_feasibility.sources.web import FetchError, check_url, html_text, same_site

LOOKUP = "https://nominatim.test/lookup"
OWN = "https://www.eye.test/visit"
BLOG = "https://blog.test/eye-closed"
JAN_08, JAN_20 = date(2027, 1, 8), date(2027, 1, 20)
PAGE = "<html><head><title>Visit</title><script>x()</script></head><body><p>Closed 6-17 January</p></body></html>"  # noqa: E501


def closed(start="2027-01-06", end="2027-01-17", page=1, about=True, status="closed", hours=()):
    return {
        "page": page,
        "about_this_place": about,
        "periods": [{"status": status, "start": start, "end": end, "hours": list(hours)}],
    }


def search_reply(*urls, searches=1):
    results = [SimpleNamespace(type="web_search_result", url=u, title=u) for u in urls]
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="server_tool_use"),
            SimpleNamespace(type="web_search_tool_result", content=results),
            SimpleNamespace(type="text", text="Ignored."),
        ],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=500,
            output_tokens=20,
            server_tool_use=SimpleNamespace(web_search_requests=searches),
        ),
    )


def extract_reply(*pages, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps({"pages": list(pages)}))],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=3000, output_tokens=100),
    )


class FakeClient:
    """Answers search calls (those with tools) and extraction calls from separate queues."""

    def __init__(self, searches=(), extractions=()):
        self.searches, self.extractions = list(searches), list(extractions)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = (self.searches if "tools" in kwargs else self.extractions).pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Fake hosts don't resolve: every name is a public address unless a test says not."""

    async def resolve(host):
        return ["10.0.0.5"] if host.startswith("internal") else ["93.184.216.34"]

    monkeypatch.setattr(web, "resolve", resolve)


@pytest.fixture
def eye(respx_mock):
    """way/1, with its own website on eye.test."""
    return respx_mock.get(LOOKUP).respond(
        json=[
            {
                "osm_type": "way",
                "osm_id": 1,
                "category": "tourism",
                "type": "attraction",
                "name": "London Eye",
                "extratags": {"website": "https://www.eye.test/"},
            }
        ]
    )


def pages(respx_mock, *urls, body=PAGE):
    for u in urls:
        respx_mock.get(u).respond(200, html=body)


def with_llm(deps, client):
    return replace(deps, llm=client)


async def run(deps, stops, **kwargs):
    return await check_closures(deps, stops, "London", **kwargs)


# --- the guarded fetch ---


@pytest.mark.parametrize(
    "url",
    [
        "http://eye.test/",
        "https://eye.test:8443/",
        "https://user:pw@eye.test/",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://169.254.169.254/latest",
        "https://internal.test/",
    ],
)
async def test_only_https_to_public_addresses(url):
    with pytest.raises(FetchError):
        await check_url(url)


async def test_redirects_are_checked_again(check_deps, respx_mock):
    respx_mock.get(OWN).respond(302, headers={"location": "https://internal.test/admin"})
    with pytest.raises(FetchError, match="not a public address"):
        await web.fetch_html(check_deps, OWN)


async def test_only_html_and_not_too_large(check_deps, respx_mock):
    respx_mock.get(OWN).respond(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    with pytest.raises(FetchError, match="not HTML"):
        await web.fetch_html(check_deps, OWN)
    small = replace(check_deps, settings=check_deps.settings.model_copy(
        update={"closures_fetch_max_bytes": 10}))  # fmt: skip
    respx_mock.get(BLOG).respond(200, html=PAGE)
    with pytest.raises(FetchError, match="too large"):
        await web.fetch_html(small, BLOG)


def test_page_text_has_no_markup_or_scripts():
    assert html_text(PAGE, 1000) == "Visit\n\nClosed 6-17 January"
    assert len(html_text(PAGE, 5)) == 5


@pytest.mark.parametrize(
    ("url", "site", "expected"),
    [
        ("https://www.eye.test/a", "eye.test", True),
        ("https://tickets.eye.test/a", "https://www.eye.test/", True),
        ("https://eye.test.evil.test/a", "https://eye.test/", False),
        ("https://noteye.test/a", "https://eye.test/", False),
    ],
)
def test_same_site(url, site, expected):
    assert same_site(url, site) is expected


# --- validating what the extractor returns ---


def target():
    return Target(osm=OsmRef(type="way", id=1), month=date(2027, 1, 1), name="Eye", website=None)


@pytest.mark.parametrize(
    "page",
    [
        closed(page=3),  # a page we never fetched
        closed(start="6 January"),
        closed(start="2027-01-17", end="2027-01-06"),
        closed(start="2027-02-01", end="2027-02-05"),  # outside the month
        closed(start="2025-01-01", end="2027-01-10"),  # implausibly long
        closed(status="altered_hours", hours=["10am-4pm"]),
        closed(status="open"),
    ],
)
def test_invalid_findings_are_rejected(page):
    sources = [{"url": OWN, "official": True}]
    assert validate_findings({"pages": [page]}, sources, target()) == ([], 1)


def test_pages_about_another_place_are_ignored():
    sources = [{"url": OWN, "official": True}]
    assert validate_findings({"pages": [closed(about=False)]}, sources, target()) == ([], 0)


# --- the check ---


async def test_official_closure_is_a_blocker_with_its_source(check_deps, respx_mock, eye):
    pages(respx_mock, BLOG, OWN)
    client = FakeClient([search_reply(BLOG, OWN)], [extract_reply(closed(page=1))])
    result = await run(with_llm(check_deps, client), [stop("a", JAN_08, "10:00", "11:00")])
    assert result.status == "ok" and result.checked_stop_ids == ["a"]
    [v] = result.violations
    assert v.check_name == "closures" and v.severity == "blocker"
    assert v.source == OWN  # own website fetched first, so it's page 1
    assert v.evidence["confidence"] == "official"
    assert (v.evidence["from"], v.evidence["to"]) == ("2027-01-06", "2027-01-17")
    assert "says eye.test" in v.message
    # the extractor saw page text, never tools; the search saw no page text
    search, extract = client.calls
    assert search["tools"][0]["max_uses"] == 1
    assert "tools" not in extract
    assert "Closed 6-17 January" in extract["messages"][0]["content"]


async def test_web_only_closure_is_a_warning(check_deps, respx_mock, eye):
    pages(respx_mock, BLOG)
    client = FakeClient([search_reply(BLOG)], [extract_reply(closed())])
    [v] = (await run(with_llm(check_deps, client), [stop("a", JAN_08)])).violations
    assert v.severity == "warning" and v.evidence["confidence"] == "web"
    assert "Worth checking" in v.message


async def test_dates_outside_the_closure_are_fine(check_deps, respx_mock, eye):
    pages(respx_mock, OWN)
    client = FakeClient([search_reply(OWN)], [extract_reply(closed())])
    result = await run(with_llm(check_deps, client), [stop("a", JAN_20)])
    assert result.violations == [] and result.checked_stop_ids == ["a"]


@pytest.mark.parametrize(
    ("start", "end", "expected"), [("11:00", "13:00", []), ("15:00", "17:00", ["warning"])]
)
async def test_special_hours_warn_only_outside_them(
    check_deps, respx_mock, eye, start, end, expected
):
    pages(respx_mock, OWN)
    special = closed(status="altered_hours", start="2027-01-08", end="2027-01-08",
                     hours=["10:00-16:00"])  # fmt: skip
    client = FakeClient([search_reply(OWN)], [extract_reply(special)])
    result = await run(with_llm(check_deps, client), [stop("a", JAN_08, start, end)])
    assert [v.severity for v in result.violations] == expected


async def test_special_hours_without_hours_are_not_held_against_a_visit(
    check_deps, respx_mock, eye
):
    pages(respx_mock, OWN)
    client = FakeClient([search_reply(OWN)], [extract_reply(closed(status="altered_hours"))])
    result = await run(with_llm(check_deps, client), [stop("a", JAN_08, "11:00", "12:00")])
    assert result.violations == [] and result.checked_stop_ids == ["a"]


async def test_findings_are_cached_per_place_and_month(check_deps, respx_mock, eye):
    pages(respx_mock, OWN)
    client = FakeClient([search_reply(OWN)], [extract_reply(closed())])
    deps = with_llm(check_deps, client)
    two_days = [stop("a", JAN_08), stop("b", JAN_20)]  # same place, same month: one lookup
    await run(deps, two_days)
    assert len(client.calls) == 2
    again = await run(deps, [stop("c", JAN_08)])
    assert len(client.calls) == 2  # served from the cache
    assert [v.severity for v in again.violations] == ["blocker"]


async def test_rechecks_use_the_cache_only(check_deps, respx_mock, eye):
    client = FakeClient()
    result = await run(with_llm(check_deps, client), [stop("a", JAN_08)], search=False)
    assert client.calls == []
    [u] = result.unknown
    assert "not verified" in u.reason


async def test_no_results_is_an_answer_but_unreadable_pages_are_not_cached(
    check_deps, respx_mock, eye
):
    respx_mock.get(OWN).respond(500)
    client = FakeClient([search_reply(OWN), search_reply()], [])
    deps = with_llm(check_deps, client)
    [u] = (await run(deps, [stop("a", JAN_08)])).unknown
    assert "could be read" in u.reason
    result = await run(deps, [stop("a", JAN_08)])  # searched again: nothing was cached
    assert result.violations == [] and result.checked_stop_ids == ["a"]
    assert len(client.calls) == 2


async def test_only_the_first_places_are_checked(check_deps, respx_mock, eye):
    settings = check_deps.settings.model_copy(update={"closures_max_places": 1})
    pages(respx_mock, OWN)
    client = FakeClient([search_reply(OWN)], [extract_reply(closed())])
    deps = replace(check_deps, settings=settings, llm=client)
    result = await run(deps, [stop("a", JAN_08), stop("b", JAN_08, osm=("way", 2))])
    assert [v.stop_ids for v in result.violations] == [["a"]]
    [u] = result.unknown
    assert u.stop_id == "b" and "first 1 places" in u.reason


async def test_unlocated_stops_hotels_and_transfers(check_deps, respx_mock, eye):
    client = FakeClient()
    stops = [
        stop("a", JAN_08, osm=None),
        stop("h", JAN_08, type="hotel"),
        stop("t", JAN_08, type="transfer"),
    ]
    result = await run(with_llm(check_deps, client), stops)
    assert [u.stop_id for u in result.unknown] == ["a"] and client.calls == []


async def test_areas_are_not_looked_up(check_deps, respx_mock, eye):
    client = FakeClient()
    soho = stop("s", JAN_08).model_copy(update={"area": True})
    result = await run(with_llm(check_deps, client), [soho])
    [u] = result.unknown
    assert u.stop_id == "s" and "an area" in u.reason and client.calls == []


async def test_without_a_key_it_is_unavailable(check_deps, eye):
    result = await run(check_deps, [stop("a", JAN_08)])
    assert result.status == "unavailable" and "no key" in result.error


async def test_model_failures_are_unavailable_not_exceptions(check_deps, respx_mock, eye):
    pages(respx_mock, OWN)
    client = FakeClient([search_reply(OWN)], [extract_reply(closed(), stop_reason="refusal")])
    result = await run(with_llm(check_deps, client), [stop("a", JAN_08)])
    assert result.status == "unavailable" and "declined" in result.error


async def test_page_failures_dont_stop_the_others(check_deps, respx_mock, eye):
    respx_mock.get(OWN).mock(side_effect=httpx.ConnectTimeout("slow"))
    pages(respx_mock, BLOG)
    client = FakeClient([search_reply(OWN, BLOG)], [extract_reply(closed())])
    [v] = (await run(with_llm(check_deps, client), [stop("a", JAN_08)])).violations
    assert v.source == BLOG


async def test_without_osm_tags_nothing_is_official_or_cached(check_deps, respx_mock):
    respx_mock.get(LOOKUP).respond(503)
    pages(respx_mock, OWN)
    client = FakeClient([search_reply(OWN)] * 2, [extract_reply(closed())] * 2)
    deps = with_llm(check_deps, client)
    result = await run(deps, [stop("a", JAN_08)])
    [v] = result.violations
    assert v.severity == "warning" and "unavailable" in result.notes[0]
    await run(deps, [stop("a", JAN_08)])
    assert len(client.calls) == 4  # looked up again


def test_prompt_version_is_in_the_cache_key():
    assert closures.PROMPT_VERSION in target().key
