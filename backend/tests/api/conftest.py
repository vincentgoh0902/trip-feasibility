"""API tests: signed tokens, a clean database, and a stand-in for the agent so runs are fast
and make no tool calls. Nominatim (city lookup, geocoding) is mocked with respx as usual."""

import json
import time
from datetime import date

import jwt
import pytest
from sqlalchemy import text

from tests.parse.test_geocode import LOUVRE, PARIS, SEARCH, search_route
from trip_feasibility.checks.models import CheckResult, Violation
from trip_feasibility.config import get_settings
from trip_feasibility.main import app

TRIP = date(2026, 12, 8)


def token(email="ana@example.com", **claims):
    s = get_settings()
    now = int(time.time())
    body = {"sub": email, "email": email, "name": "Ana", "provider": "github"}
    body |= {"iss": s.api_jwt_issuer, "aud": s.api_jwt_audience, "iat": now, "exp": now + 300}
    body.update(claims)
    secret = s.api_jwt_secret.get_secret_value()
    return jwt.encode({k: v for k, v in body.items() if v is not None}, secret, algorithm="HS256")


def auth(email="ana@example.com", **claims):
    return {"Authorization": f"Bearer {token(email, **claims)}"}


ANA, BOB = auth("ana@example.com"), auth("bob@example.com")


def stop_in(place, start, end, *, day=1, type="venue", at=(48.8611, 2.3358)):
    return {"day": day, "start_time": start, "end_time": end, "place_name": place,
            "type": type, "lat": at[0], "lon": at[1]}  # fmt: skip


ITINERARY = {
    "title": "Paris in a day",
    "city": "Paris",
    "start_date": TRIP.isoformat(),
    "raw_text": "9-12 Louvre, 13-14 lunch",
    "stops": [
        stop_in("Louvre Museum", "09:00", "12:00"),
        stop_in("Café Marly", "12:15", "13:30", type="meal"),
    ],
}


def violation(check, severity, stop_ids, message="m"):
    return Violation(check_name=check, severity=severity, stop_ids=stop_ids, message=message,
                     evidence={"x": 1}, source="test")  # fmt: skip


def fake_stream(violations_by_check=None, fixes=None):
    """Stands in for agent.graph.stream_agent: every enabled check returns ok with the given
    violations, then `fixes(inp, report)` (or None)."""
    violations_by_check = violations_by_check or {}
    calls = []

    async def stream(mcp, deps, inp, fixer):
        calls.append({"input": inp, "fixer": fixer})
        for name in inp.config.enabled:
            yield ("check", CheckResult(check_name=name, status="ok",
                                        violations=violations_by_check.get(name, []),
                                        checked_stop_ids=[s.id for s in inp.stops]))  # fmt: skip
        yield ("fixes", fixes(inp) if fixes else None)

    stream.calls = calls
    return stream


def sse(body: str) -> list[dict]:
    """The events of an SSE response body, in order."""
    events = []
    for frame in body.strip().split("\n\n"):
        data = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
        if data:
            events.append(json.loads("".join(data)))
    return events


@pytest.fixture
async def api(client):
    """The API client on an empty database (users and everything they own, the cache)."""
    async with app.state.sessionmaker() as session:
        await session.execute(text("TRUNCATE users, api_cache CASCADE"))
        await session.commit()
    return client


@pytest.fixture
def services(api):
    return app.state.services


@pytest.fixture
def nominatim(respx_mock):
    """Paris is found; every other place search finds the Louvre."""
    search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[PARIS])
    respx_mock.get(SEARCH).respond(json=[LOUVRE])
    return respx_mock
