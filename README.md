# Trip Feasibility Checker

Paste an itinerary; an AI agent reports what will break (closed venues, public holidays,
travel times that don't fit, hotel red flags, overloaded days), shows the evidence, and
proposes fixes that are re-checked before you see them.

**Status:** early build. The backend skeleton and database schema are in place; the
agent, check tools and frontend are next. Evaluation results will be reported here,
whatever they turn out to be. Requirements: [`docs/PRD.md`](docs/PRD.md).

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

After changing `src/trip_feasibility/models.py`:
`uv run alembic revision --autogenerate -m "<what changed>"`, then review the file.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://trip:trip@localhost:5432/trip` | Postgres connection |
| `PORT` | `8000` | Port the API listens on inside the container |
| `TEST_DATABASE_URL` | `DATABASE_URL` + `_test` | Database the tests use (created if missing; must end in `_test`) |

Secrets go in `.env` (gitignored), never in the repo. CI runs gitleaks on every push.
