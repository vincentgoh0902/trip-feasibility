"""The frontend's types are generated from frontend/openapi.json: it must match the API."""

import json
from pathlib import Path

from trip_feasibility.main import create_app

SCHEMA = Path(__file__).resolve().parents[3] / "frontend" / "openapi.json"


def test_committed_openapi_schema_is_current():
    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert committed == create_app().openapi(), (
        "frontend/openapi.json is stale: run `uv run python scripts/export_openapi.py`, "
        "then `npm run gen:api` in frontend/"
    )
