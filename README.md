# Trip Feasibility Checker

Paste an itinerary; an AI agent reports what will break (closed venues, public holidays,
travel times that don't fit, hotel red flags, overloaded days), shows the evidence, and
proposes fixes that are re-checked before you see them.

**Status:** early build. The backend skeleton, database schema and the first check tools
(public holidays, opening hours) are in place; the agent and frontend are next. Evaluation results will be reported here,
whatever they turn out to be. Requirements: [`docs/PRD.md`](docs/PRD.md). How it works and why:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Run locally

Needs Docker and [uv](https://docs.astral.sh/uv/).

```sh
cp .env.example .env
docker compose up --build        # Postgres 17 + pgvector, and the API on :8020
curl localhost:8020/health       # {"status":"ok","database":"ok"}
```

The API container applies database migrations on start.

## Develop the backend

```sh
docker compose up -d db
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn trip_feasibility.main:app --reload --port 8020
uv run pytest                    # uses its own trip_test database
uv run ruff check . && uv run ruff format --check .
```

Run the check tools once against the real APIs: `uv run python scripts/smoke_checks.py`.

After changing `src/trip_feasibility/models.py`:
`uv run alembic revision --autogenerate -m "<what changed>"`, then review the file.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://trip:trip@localhost:5432/trip` | Postgres connection |
| `PORT` | `8000` | Port the API listens on inside the container |
| `NAGER_BASE_URL` | `https://date.nager.at/api/v3` | Public holidays API |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` | OSM place lookups (opening hours) |
| `NOMINATIM_MIN_INTERVAL_S` | `1.0` | Minimum gap between Nominatim requests (its usage policy) |
| `RATE_LIMIT_COOLDOWN_S` | `60` | Pause after a service answers 429/403/503 |
| `HTTP_USER_AGENT` | `trip-feasibility/0.1 (+repo URL)` | Identifies the app to OSM services |
| `TEST_DATABASE_URL` | `DATABASE_URL` + `_test` | Database the tests use (created if missing; must end in `_test`) |

Secrets go in `.env` (gitignored), never in the repo. CI runs gitleaks on every push.
