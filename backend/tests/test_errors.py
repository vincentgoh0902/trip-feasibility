from fastapi import Query

from trip_feasibility.errors import AppError
from trip_feasibility.main import app


@app.get("/_test/app-error")
async def _raise_app_error() -> None:
    raise AppError(409, "duplicate_itinerary", "already saved")


@app.get("/_test/validated")
async def _validated(n: int = Query()) -> dict[str, int]:
    return {"n": n}


async def test_unknown_route_uses_error_shape(client):
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_app_error_uses_error_shape(client):
    r = await client.get("/_test/app-error")
    assert r.status_code == 409
    assert r.json() == {"error": {"code": "duplicate_itinerary", "message": "already saved"}}


async def test_validation_error_uses_error_shape(client):
    r = await client.get("/_test/validated", params={"n": "x"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error"
    assert body["message"].startswith("query.n:")
