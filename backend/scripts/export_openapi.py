"""Write the API's OpenAPI schema to frontend/openapi.json, the contract the frontend's types
are generated from (PRD "API design").

    uv run python scripts/export_openapi.py          # then, in frontend/: npm run gen:api

Needs no database: the schema comes from the routes alone. tests/api/test_openapi.py fails
when the committed file is out of date.
"""

import json
from pathlib import Path

from trip_feasibility.main import create_app

OUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
