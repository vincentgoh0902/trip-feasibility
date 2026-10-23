# trip-feasibility

Trip Feasibility Checker: a traveller pastes an itinerary and an AI agent reports what
will break (opening hours, public holidays, travel times, hotel red flags, daily load)
with evidence, and proposes re-checked fixes. Every reported result is backed by a
logged evaluation on a versioned, hand-annotated dataset.

**Requirements live in `docs/PRD.md`; progress and open decisions in `PLAN.md`; how it all
works and why in `docs/ARCHITECTURE.md`. Read them at the start of every session. Update
PLAN.md as work lands, and ARCHITECTURE.md (including its decision log) whenever a design
choice is made or changed: it is how I follow the project.** The PRD's dates are a loose
reference only.

- **Stack:** backend Python 3.12 + uv, FastAPI, Pydantic, LangGraph, MCP Python SDK,
  SQLAlchemy 2.0 + Alembic, pytest, ruff. Frontend Next.js (App Router) + TypeScript,
  Tailwind, shadcn/ui, Auth.js, openapi-typescript. Postgres 17 + pgvector. Langfuse
  tracing; MLflow for Tier 2 training only (Mac side).
- **Layout:** `backend/` (uv project: `src/trip_feasibility/` main.py app, config.py env
  settings, db.py engine/session, errors.py error shape, models.py schema, itinerary.py
  `Stop` schema, mcp_server.py tools, `checks/` one module per check plus models.py
  (`CheckResult`) and deps.py (HTTP client, Postgres cache, `RateLimiter`, `Unavailable`),
  `checks/legs.py` (legs + travel options, shared by travel_time and daily_load),
  `sources/nominatim.py` (OSM place tags; geocoding goes here too), `sources/routing.py`
  (FOSSGIS OSRM table service); `alembic/`
  migrations; `tests/`; `scripts/smoke_checks.py` live API run; Dockerfile). Planned: `frontend/`, `train/` (Tier 2, Mac only).
  `data/` is gitignored (local Postgres volume, datasets, review dumps).
- **Schema changes:** edit models.py, then `uv run alembic revision --autogenerate -m ...`
  and review the file. CI runs `alembic check`, so models and migrations must match.
- **Errors:** every error response is `{"error": {"code", "message"}}`; raise `AppError`.
- **Parsing:** only step 1 (extraction) uses an LLM. The LLM never produces
  coordinates; geocoding is code. Parse accuracy measures step 1 only.
- **Checks:** each is a plain async function `(CheckDeps, stops, ...) -> CheckResult` in
  `checks/`, wrapped as an MCP tool in mcp_server.py (MCP SDK 2.x: `MCPServer`,
  in-process `mcp.Client(server)`). Never free text. Missing data goes in `unknown`, not
  violations; a failed data source raises `Unavailable` → `status="unavailable"`, never an
  exception out of the check. External lookups go through `CheckDeps.cached` / the
  `api_cache` table; failures are never cached. Tests mock HTTP with respx against the fake
  hosts set in conftest; nothing in the test suite may call a real API.
- **Evals:** every run writes model ID, prompt version, config and dataset version to
  `eval_runs`. The held-out test set is touched only for reported results. The judge
  model is from a different provider than the agent model.
- **Styling:** follows `../Vincent Goh Branding Kit.pdf`. Colours as tokens, not hardcoded.
- **Port:** API listens on 8000 in the container (override with `PORT`); `GET /health` →
  200 with a DB check (503 if the DB is unreachable). Locally Compose publishes it on 8020.
- **Config (env):** `DATABASE_URL`, `PORT`, `HTTP_USER_AGENT`, `HTTP_TIMEOUT_S`,
  `NAGER_BASE_URL`, `NOMINATIM_BASE_URL`, `NOMINATIM_MIN_INTERVAL_S` (1.0),
  `NOMINATIM_TIMEOUT_S`, `ROUTING_BASE_URL`, `ROUTING_MIN_INTERVAL_S` (1.0),
  `ROUTING_TIMEOUT_S`, `RATE_LIMIT_COOLDOWN_S` (60), `CACHE_TTL_HOLIDAYS_DAYS`,
  `CACHE_TTL_OSM_DAYS`, `CACHE_TTL_ROUTES_DAYS`; tests only: `TEST_DATABASE_URL`. Secrets in `.env` / server `app.env`.
- **Memory:** API idles at ~60 MB. Postgres sizing depends on the review vectors (below).
- **Visibility:** homelab starts `private`. GitHub repo is **public**: never commit
  personal itineraries, API keys, review dumps or annotated datasets with personal details.

## Commands

All backend commands run from `backend/`; Postgres comes from Compose at the repo root.

```sh
docker compose up -d db                      # Postgres 17 + pgvector on 127.0.0.1:5432
uv sync                                      # in backend/
uv run alembic upgrade head
uv run uvicorn trip_feasibility.main:app --reload --port 8020
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
docker compose up -d --build                 # full stack; API on 127.0.0.1:8020
```

Compose host ports are overridable: `API_HOST_PORT` (default 8020), `DB_HOST_PORT` (5432).

## CI (`.github/workflows/ci.yml`)

On push to `main` and on PRs: ruff check + format check, `alembic upgrade head`,
`alembic check`, pytest against a `pgvector/pgvector:pg17` service, and gitleaks over
full history. Keep it green; Tier 3 adds the 10-itinerary eval regression gate.

## Gotchas

- **Tests use their own database:** `TEST_DATABASE_URL`, default = `DATABASE_URL` with
  `_test` appended (`trip_test`), created if missing and migrated per session by
  `tests/conftest.py`. `test_migrations.py` drops the whole schema, so conftest refuses any
  database whose name doesn't end in `_test`. Don't weaken that guard.
- 8000 is taken on this Mac by another process, hence local port 8020.
- `backend/alembic/` (the migrations folder) shadows the `alembic` package for ruff's
  import sorting, so pyproject sets `known-third-party = ["alembic"]`.
- `alembic.ini` has post-write hooks that run `ruff check --fix` and `ruff format` on new
  migrations; autogenerated files come out lint-clean.
- The API container runs `alembic upgrade head` on every start (`backend/start.sh`).
- `astral-sh/setup-uv` has no floating major tag (`@v10` fails); pin an exact release.
- **Don't use public Overpass.** It's at capacity, asks automated clients to self-host, and
  bans clients that retry into rate limits (all public servers failed on Oct 23, 2026).
  OSM tags come from Nominatim `/lookup` instead; see ARCHITECTURE.md "Why Nominatim".
- **Nominatim policy:** ≤1 request/second, identifying User-Agent, cache everything, no bulk
  use. All Nominatim calls (including future geocoding) must go through
  `deps.nominatim_limiter`, which also pauses calls for 60 s after a 429/403/503. Never add
  retries that bypass it. Nominatim doesn't index untagged/unnamed objects (→ "not found").
- **Routing (FOSSGIS OSRM) has the same rules:** ≤1 request/second via
  `deps.routing_limiter`, no heavy use, and the frontend must show OSM attribution with a
  "fix the map" link. One table request per travel mode covers a whole itinerary.
- **Transit and taxi times are estimates** (constants in `checks/legs.py`): always label them
  as estimated, and keep the 25% margin before an estimate becomes a blocker. No free
  service routes public transport. Pace limits in `checks/daily_load.py` are untuned
  defaults; tune both from the dev set, not by guessing.
- Fake routing in tests: `tests/fake_osrm.py` (0.01° = 1 km; 5/15/30 km/h).
- `opening-hours-py` rejects common-but-invalid hours like `Mo-Fr 9-17` (→ unknown), and uses
  its own holiday calendar for `PH`, separate from Nager.Date.
- Before committing, check `git status` for stray files (an empty `.Rhistory` slipped in once).
- The pgvector extension is created in the initial migration; `hotel_reviews` is not yet
  in the schema because its vector dimension depends on the embedding model.

## Open decisions

Tracked in `PLAN.md` ("Open"). Summary: embedding model and dimension; agent and judge
models (different providers); test-set cities; demo-mode scope, and where demo runs live
(`check_runs.version_id` is required, but demo mode must save nothing); hosting shape below.

## Hosting (open questions)

The PRD's shape doesn't match the default homelab project (one Dockerfile, one port,
256 MB). To settle before the first deploy:

- Two services + Postgres. Auth.js needs a running Next.js server (no static export), so
  a single image means Next.js on `PORT` proxying `/api` to FastAPI on localhost.
  Alternatively a multi-container project type on the server. Postgres is separate either way.
- Postgres with ~515K 1024-dim review vectors is ~2 GB of vectors before the HNSW index.
  Options: load only hotels in the test-set cities, a smaller embedding model, `halfvec`.
- Langfuse: use Langfuse Cloud rather than self-hosting it on the server.
- Email magic links need an email provider secret (not in the stack yet).

## Deployment

GitHub: https://github.com/vincentgoh0902/trip-feasibility (public, default branch `main`).

Not yet a homelab project. The Dockerfile is in `backend/`, not the repo root, so the
server needs `APP_BUILD_CONTEXT=backend` until the frontend lands and the hosting shape
is settled. The server session adds it with `new-project.sh`. Ship a change: commit and push, then (after asking
me) `ssh homelab '~/homelab/scripts/deploy.sh trip-feasibility'`.
