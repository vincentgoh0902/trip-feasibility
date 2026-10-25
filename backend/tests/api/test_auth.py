import time

import jwt
import pytest

from tests.api.conftest import ANA, auth, token
from trip_feasibility.config import Settings, get_settings


async def test_no_token_is_401(api):
    r = await api.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_valid_token_creates_the_user_once(api):
    first = await api.get("/api/v1/me", headers=ANA)
    assert first.status_code == 200
    assert first.json()["email"] == "ana@example.com"
    assert first.json()["auth_provider"] == "github"
    again = await api.get("/api/v1/me", headers=auth("ANA@example.com "))  # same person
    assert again.json()["id"] == first.json()["id"]


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 120},  # expired
        {"aud": "someone-else"},
        {"iss": "someone-else"},
        {"email": None},
        {"email": "not-an-email"},
        {"exp": int(time.time()) + 7200},  # lives longer than an hour
    ],
)
async def test_bad_claims_are_rejected(api, claims):
    r = await api.get("/api/v1/me", headers=auth(**claims))
    assert r.status_code == 401


async def test_wrong_secret_and_algorithms_are_rejected(api):
    s = get_settings()
    now = int(time.time())
    claims = {"email": "a@b.c", "iss": s.api_jwt_issuer, "aud": s.api_jwt_audience,
              "iat": now, "exp": now + 60}  # fmt: skip
    forged = jwt.encode(claims, "another-secret-that-is-long-enough!!", algorithm="HS256")
    unsigned = jwt.encode(claims, None, algorithm="none")
    for bad in (forged, unsigned, "garbage"):
        r = await api.get("/api/v1/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, bad
    r = await api.get("/api/v1/me", headers={"Authorization": f"Token {token()}"})
    assert r.status_code == 401


async def test_a_bad_token_never_becomes_a_demo_call(api, nominatim):
    r = await api.post(
        "/api/v1/checks", json={"sample_id": "paris-one-day"}, headers=auth(aud="nope")
    )
    assert r.status_code == 401


async def test_without_a_secret_signed_in_routes_are_unavailable(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_jwt_secret", None)
    r = await api.get("/api/v1/me", headers=ANA)
    assert r.status_code == 503
    assert (await api.get("/api/v1/demo/samples")).status_code == 200  # the demo still works


def test_a_short_secret_is_refused_at_startup():
    with pytest.raises(ValueError, match="at least 32"):
        Settings(api_jwt_secret="short")
