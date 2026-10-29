# Trip Feasibility Checker

Paste an itinerary; an AI agent reports what will break (closed venues, public holidays,
travel times that don't fit, hotel red flags, overloaded days), shows the evidence, and
proposes fixes that are re-checked before you see them.

## Why I built this

For a trip with friends, I used an AI-generated itinerary that took in the spots we wanted
to see and the food places we wanted to go. Only after we'd booked accommodation in the
different cities did we realise that, on the days we'd be in town, a restaurant we really
wanted to go to would be closed.

Nothing in the itinerary had told us. This project is so that doesn't happen again: check a
plan against real opening hours, holidays and travel times before you book around it.

**Status:** in development, not yet hosted. Built: all five checks, the parse pipeline, the
agent with re-checked fixes, the API (live progress over SSE, a demo mode), a prompt-injection
suite, the eval runner and the first frontend pages (landing, demo, results). The evaluation
dataset (40 chatbot itineraries across four cities, hand-verified labels) is being annotated;
no evaluation results yet. They will be reported here, whatever they turn out to be. Requirements: [`docs/PRD.md`](docs/PRD.md). How it works and why:
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

Evaluation dataset: how to build it is in [`docs/ANNOTATION.md`](docs/ANNOTATION.md); the
files live in `data/dataset/` (not in the repo). Check them with
`uv run python scripts/validate_dataset.py --partial`.

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
| `ROUTING_BASE_URL` | `https://routing.openstreetmap.de` | Walking/cycling/driving routes (FOSSGIS OSRM) |
| `ROUTING_MIN_INTERVAL_S` | `1.0` | Minimum gap between routing requests (its usage policy) |
| `RATE_LIMIT_COOLDOWN_S` | `60` | Pause after a service answers 429/403/503 |
| `HTTP_USER_AGENT` | `trip-feasibility/0.1 (+repo URL)` | Identifies the app to OSM services |
| `ANTHROPIC_API_KEY` | none | Claude API key for parse step 1 (secret; `.env` only) |
| `EXTRACTION_MODEL` | `claude-haiku-5-5` | Model for parse step 1 |
| `EXTRACTION_EFFORT` | `low` | Effort level for parse step 1 |
| `FIX_MODEL` | `claude-haiku-5-5` | Claude model that proposes fixes |
| `FIX_EFFORT` | `medium` | Effort level for fix proposals |
| `TEST_DATABASE_URL` | `DATABASE_URL` + `_test` | Database the tests use (created if missing; must end in `_test`) |

Secrets go in `.env` (gitignored), never in the repo. CI runs gitleaks on every push.

## Data sources

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via
[Nominatim](https://nominatim.org) and the [FOSSGIS routing servers](https://routing.openstreetmap.de).
Public holidays from [Nager.Date](https://date.nager.at).
