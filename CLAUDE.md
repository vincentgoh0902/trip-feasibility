# trip-feasibility

Trip Feasibility Checker: a traveller pastes an itinerary and an AI agent reports what will
break (opening hours, public holidays, travel times, hotel red flags, daily load) with
evidence, and proposes re-checked fixes. Every reported result is backed by a logged
evaluation on a versioned, hand-annotated dataset.

**Read at the start of every session:** `docs/PRD.md` (requirements; its dates are a loose
reference only), `PLAN.md` (progress, decisions, open items), `docs/ARCHITECTURE.md` (how it
works and why, with the decision log), `docs/ANNOTATION.md` (dataset rules). **Update PLAN.md
as work lands, and ARCHITECTURE.md (including the decision log) whenever a design choice is
made or changed: it's how I follow the project.**

## Stack

Backend: Python 3.12 + uv, FastAPI, Pydantic, LangGraph, MCP Python SDK 2.x, SQLAlchemy 2.0
(async, asyncpg) + Alembic, pytest, ruff. Postgres 17 + pgvector. Claude (Anthropic SDK) for
extraction and fixes. Frontend: Next.js 16 (App Router) + TypeScript, Tailwind 4, shadcn/ui
(Base UI), openapi-typescript + openapi-fetch, vitest; Auth.js planned. Langfuse tracing;
MLflow for Tier 2 training (Mac only).

## Layout

```
backend/                        uv project; Dockerfile, start.sh, alembic/, tests/
  src/trip_feasibility/
    main.py config.py db.py     FastAPI app (lifespan wires Services), env settings, engine/session
    errors.py models.py         error shape, database schema
    api/                        routes under /api/v1: itineraries.py, checks.py (runs, SSE,
                                demo), fixes.py, me.py; schemas.py, common.py (ownership)
    auth.py                     verifies the per-call token the Next.js server signs
    runs.py                     RunService: background runs, LiveRun event log, saving
    ratelimit.py tracing.py     hourly limits; Langfuse + TracedClient/TracedMcp + Usage
    pricing.py demo/            model prices; demo samples (samples.yaml)
    itinerary.py                the Stop schema every check takes
    mcp_server.py               checks as MCP tools; create_server(deps) for in-process use
    checks/                     one module per check; models.py (CheckResult), deps.py (HTTP
                                client, Postgres cache, RateLimiter, Unavailable, locks),
                                config.py (CheckConfig, FR-5), legs.py (shared by travel_time
                                and daily_load), hotel_red_flags.py + hotel_themes.json
    sources/                    nominatim.py (OSM tags, coordinates, search), routing.py (OSRM)
    parse/                      pipeline.py end to end; clean.py (step 0: chat debris out,
                                length limits), extract.py (Claude), schema.py,
                                normalise.py, geocode.py
    agent/                      graph.py (LangGraph: checks in parallel, then fixes),
                                fixes.py (propose → apply → re-check → retry), models.py
    dataset/                    annotation format (models.py), split rules, validation + gold labels
    evals/                      runner.py, align.py (parsed ↔ gold stops), score.py,
                                injection.py (prompt-injection suite)
  scripts/                      see Commands
frontend/                       Next.js (npm); openapi.json (exported from the backend)
  src/app/                      pages; api/v1/[...path]/route.ts proxies every call to FastAPI
  src/components/run/ ui/       results page pieces; shadcn/ui components (owned, editable)
  src/lib/                      api/ (generated schema.d.ts, clients, SSE event types), run/
                                (state reducer, timeline layout), format.ts, config.ts
docs/                           PRD, ARCHITECTURE, ANNOTATION, TEST_CITIES; annotation/ holds
                                the template, worked example and unlabelled hotels.yaml
data/  (gitignored)             postgres/ volume; raw/hotel-reviews/Hotel_Reviews.csv (Kaggle,
                                CC0); dataset/ (hotels.yaml + itineraries/<id>.yaml +
                                texts/<id>.md, the chatbot replies as copied); evals/runs/
                                (eval details); embeddings/; calibration/ (theme-labelled
                                reviews from non-dataset hotels)
```

Planned: `train/` (Tier 2, Mac only).

## Rules

- **The LLM proposes, code decides.** Only parse step 1 (extraction) and fixes use an LLM.
  The LLM never produces coordinates (geocoding is code, and never guesses: no match in the
  city box or within 60 km → unresolved). Fixes are operations
  (move/remove/replace/add_transport_mode), never a new itinerary; code applies them and
  re-runs every check, and only valid fixes (targets gone, no new blocker) are shown. Hotel
  review text never goes into a prompt.
- **Checks:** each is a plain async function `(CheckDeps, stops, ...) -> CheckResult`, wrapped
  as an MCP tool. Never free text. Missing data goes in `unknown`, not violations; a failed
  data source raises `Unavailable` → `status="unavailable"`, never an exception out of the
  check. External lookups go through `CheckDeps.cached` / the `api_cache` table; failures are
  never cached. Which checks run is configuration, not a model's choice.
- **Tests never call a real API:** HTTP is mocked with respx against the fake hosts set in
  conftest; the Anthropic client is a fake object.
- **Vienna is held out** (PLAN.md "Decisions"): dev = 5 each from Paris, London, Barcelona;
  test = the other 15 plus all 10 Vienna. **Never show me Vienna outputs, results or traces
  while building**; only the final test run touches them. If anything changes after Vienna
  results were seen, record it in the README (Vienna is then no longer unseen). Variants stay
  in their parent's split; `dataset.split_errors` checks this. Cached Vienna lookups are fine.
- **Don't look at the hotel check's output on the 16 dataset hotels** before I've labelled
  them (`check_itinerary.py --example` skips it, as the example's hotel is one of them), and
  never run it on Vienna hotels. Calibration uses hotels outside the dataset.
- **Dataset annotations:** per itinerary, `texts/<id>.md` (the reply exactly as copied, saved
  first with `save_reply.sh`; the only copy of the text) and `itineraries/<id>.yaml`
  (metadata and labels, no `text`; format in `dataset/models.py`, example in
  `docs/annotation/example.yaml` + `example.md`). Gold stops are what extraction should
  return (`place_name` verbatim from the cleaned text, `show_clean.py`); they extend
  `parse.schema.ExtractedStop`, so changing that schema changes the dataset format. Hand labels: opening_hours (by stop), public_holidays and
  daily_load (by day). travel_time labels are derived from real leg times (blocker if gap <
  minutes, warning if gap < minutes + 10; overlaps always blockers; every leg under 60 min
  needs a real time). hotel_red_flags labels come from hotels.yaml (red flag at about 4+/40
  sampled reviews for noise and cleanliness, 3+ location, 2+ safety), one per hotel per
  itinerary. warning/blocker only. Truth is reality (official sites, Google Maps), never our
  own checks or data sources.
- **API:** every query for user data filters by the caller (another user's thing is a 404).
  Sign-in routes take `CurrentUser`; demo-capable ones `OptionalUser` (a bad token is still
  401). The demo checks samples only and saves nothing. Runs go through `RunService`, never
  the request: tests swap `services.runs.stream` for `tests/api/conftest.fake_stream`.
- **Third-party text never reaches a prompt:** review excerpts, free-text OSM tags and
  opening-hours comments are filtered by `agent.fixes.safe_evidence`. Changing what the fix
  prompt sees bumps `FIX_PROMPT_VERSION`; the injection suite measures it.
- **Evals:** eval mode (`make_check_deps(..., eval_mode=True)`) serves expired cache entries,
  so reruns make no external calls. Every run writes model ID, prompt version, config and
  dataset version to `eval_runs`. Two modes, reported apart: gold stops (checks alone) and
  parsed (end to end; stops aligned to gold by day + name). A prediction matches a label on
  check + target; severity agreement is separate. The test split needs `--final` and is
  touched only for reported results. The judge model comes from a different provider than
  the agent model.
- **Schema changes:** edit models.py, then `uv run alembic revision --autogenerate -m ...`
  and review the file. CI runs `alembic check`.
- **Errors:** every error response is `{"error": {"code", "message"}}`; raise `AppError`.
- **Styling:** matches the hero illustration (`frontend/public/hero.png`): only colours sampled
  from it, darkened where text needs it. Colours as tokens, not hardcoded: they live in
  `frontend/src/app/globals.css`, components use the semantic ones (`bg-card`, `text-heading`,
  `text-blocker-ink` ...). Text pairings must stay WCAG AA (4.5:1). Flat 3D look: cards
  (`card-raised`) and buttons sit on a solid side face, no blur shadows, gradients or glows.
- **Frontend:** the browser only calls its own origin; the proxy route adds what FastAPI needs
  (the sign-in token, once it exists). Pages show OSM attribution and a "Fix the map" link
  wherever OSM-derived results appear. Work at 375 px.
- **Visibility:** homelab starts `private`. The GitHub repo is **public**: never commit
  itineraries, raw replies, annotations, API keys or review dumps.

## Runtime and config

- **Frontend port:** `next dev`/`next start` on 3000 locally; `API_INTERNAL_URL` (server-side,
  default `http://localhost:8020`) is where it reaches the API.
- **Port:** the API listens on 8000 in the container (`PORT`); `GET /health` → 200 with a DB
  check (503 if unreachable). Compose publishes it on 8020 locally (8000 is taken on this Mac).
- **Memory:** the API idles at ~145 MB (the agent stack loads at startup), ~155 MB after runs;
  under the 256 MB default. Langfuse adds ~12 MB when on. Postgres holds ~300 MB of reviews.
- **Env vars:** `DATABASE_URL`, `PORT`, `HTTP_USER_AGENT`, `HTTP_TIMEOUT_S`, `NAGER_BASE_URL`,
  `NOMINATIM_BASE_URL`, `NOMINATIM_MIN_INTERVAL_S` (1.0), `NOMINATIM_TIMEOUT_S`,
  `ROUTING_BASE_URL`, `ROUTING_MIN_INTERVAL_S` (1.0), `ROUTING_TIMEOUT_S`,
  `RATE_LIMIT_COOLDOWN_S` (60), `CACHE_TTL_HOLIDAYS_DAYS`, `CACHE_TTL_OSM_DAYS`,
  `CACHE_TTL_ROUTES_DAYS`, `ANTHROPIC_API_KEY` (secret), `EXTRACTION_MODEL`
  (claude-haiku-5-5), `EXTRACTION_EFFORT` (low), `FIX_MODEL` (claude-haiku-5-5), `FIX_EFFORT`
  (medium), `API_JWT_SECRET` (secret, shared with the Next.js server), `API_JWT_ISSUER`,
  `API_JWT_AUDIENCE`, `RATE_LIMIT_CHECKS_PER_HOUR` (20), `RATE_LIMIT_PARSES_PER_HOUR` (20),
  `RATE_LIMIT_DEMO_PER_HOUR` (10), `MAX_CONCURRENT_RUNS` (4), `DEMO_RUN_TTL_S` (3600),
  `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (secret; tracing off without both),
  `LANGFUSE_BASE_URL`, `TRACE_CONTENT` (false: no itinerary text in traces),
  `FORWARDED_ALLOW_IPS` (uvicorn: trust the proxy's X-Forwarded-For); tests only:
  `TEST_DATABASE_URL`. Empty values count as unset.
- **Secrets** go in the repo-root `.env` locally (read as `../.env` from backend/, then
  `backend/.env`; real env vars win) and in `app.env` on the server.

## Commands

All backend commands run from `backend/`; Postgres comes from Compose at the repo root.

```sh
# setup and checks
docker compose up -d db                      # Postgres 17 + pgvector on 127.0.0.1:5432
uv sync && uv run alembic upgrade head
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run uvicorn trip_feasibility.main:app --reload --port 8020
docker compose up -d --build                 # full stack; API on 127.0.0.1:8020

# dataset
scripts/save_reply.sh paris-03                        # clipboard → data/dataset/texts/paris-03.md
uv run python scripts/validate_dataset.py --partial   # drop --partial once all 40 are done
uv run python scripts/show_clean.py paris-03          # the reply as extraction sees it
uv run python scripts/sample_reviews.py "Hotel Cavendish" -n 40   # csv_name from hotels.yaml

# running and evaluating
uv run python scripts/check_itinerary.py --example    # checks on the example (no key needed)
uv run python scripts/check_itinerary.py trip.txt --city Paris --start 2027-04-24
uv run python scripts/run_eval.py [--mode gold|parsed|both] [--fixes]   # dev split
uv run python scripts/run_eval.py --split test --final  # reported results only
uv run python scripts/run_injection_suite.py          # needs the key; --defended-only, --no-save
uv run python scripts/smoke_checks.py                 # each check once against the real APIs
uv run python scripts/smoke_parse.py                  # live geocoding (+ extraction with key)

# frontend (from frontend/)
npm ci && npm run dev                                 # http://localhost:3000
npm run typecheck && npm run lint && npm test && npm run build
uv run python scripts/export_openapi.py               # (backend/) after API changes, then
npm run gen:api                                       # (frontend/) regenerate the types

# hotel reviews (Mac only, embed group)
uv run --group embed python scripts/load_reviews.py           # embed + load reviews
uv run --group embed python scripts/embed_hotel_themes.py     # after editing THEMES phrasings
uv run --group embed python scripts/calibrate_hotel_themes.py # theme matcher P/R/F1
uv run python scripts/hotel_theme_base_rates.py              # per-theme min_share
```

Compose host ports are overridable: `API_HOST_PORT` (default 8020), `DB_HOST_PORT` (5432).

## CI (`.github/workflows/ci.yml`)

On push to `main` and on PRs: ruff check + format check, `alembic upgrade head`,
`alembic check`, pytest against a `pgvector/pgvector:pg17` service (including a check that
`frontend/openapi.json` is current), the frontend job (generated types match openapi.json,
eslint, typecheck, vitest, `next build`), and gitleaks over full history. Keep it green; Tier 3 adds the 10-itinerary eval regression gate.

## Gotchas

**Tests and tooling**
- Tests use their own database: `TEST_DATABASE_URL`, default `DATABASE_URL` + `_test`
  (`trip_test`), created and migrated per session by `tests/conftest.py`. `test_migrations.py`
  drops the whole schema, so conftest refuses any database not ending in `_test`. Don't weaken
  that guard.
- `uv run --group embed` leaves numpy etc. in the venv, so tests can pass locally and fail in
  CI: run `uv sync` before pytest, and never import numpy in `src/` or `tests/`.
- The `anthropic` SDK (2.x) runs on `httpx2`, which respx doesn't intercept: tests pass a fake
  client (`tests/parse/test_extract.py`).
- `backend/alembic/` shadows the `alembic` package for ruff's import sorting, so pyproject sets
  `known-third-party = ["alembic"]`. `alembic.ini` post-write hooks lint new migrations.
- Autogenerated migrations write `pgvector.sqlalchemy.halfvec.HALFVEC(...)` without importing
  it: add `from pgvector.sqlalchemy import HALFVEC` by hand (CI's `alembic upgrade` would fail).
- The API container runs `alembic upgrade head` on every start (`backend/start.sh`).
- Tests blank `ANTHROPIC_API_KEY` and the Langfuse keys in conftest, so a real `.env` can't
  leak into them; API tests set `services.llm` / `services.runs.llm` to a `FakeClient`.
- Run state (live runs, demo runs, rate-limit counts) is per process: one API container.
- `astral-sh/setup-uv` has no floating major tag (`@v10` fails); pin an exact release.
- Before committing, check `git status` for stray files (an empty `.Rhistory` slipped in once).

**Frontend**
- This is Next.js 16: read `frontend/node_modules/next/dist/docs/` before using an API from
  memory (`AGENTS.md`). Cache Components is on: request-time reads (params, fetch) go inside
  `<Suspense>`; `middleware` is now `proxy`.
- After an API change: export openapi.json and run `gen:api`, or CI fails.
- The SSE event types in `lib/api/types.ts` are hand-written; keep them in step with `runs.py`.
- `next dev` may re-add the managed block in `frontend/AGENTS.md`; that's expected.

**External services**
- **Don't use public Overpass:** it's at capacity and bans clients that retry into rate
  limits. OSM tags come from Nominatim `/lookup` (ARCHITECTURE "Why Nominatim").
- **Nominatim:** ≤1 request/second, identifying User-Agent, cache everything, no bulk use. All
  calls go through `deps.nominatim_limiter` (pauses 60 s after 429/403/503); never add retries
  that bypass it. Untagged/unnamed objects aren't indexed (→ "not found").
- **Routing (FOSSGIS OSRM):** same rules via `deps.routing_limiter`; the frontend must show OSM
  attribution with a "fix the map" link. One table request per travel mode per itinerary.
- **Parallel checks share lookups:** cache-check-and-fetch must hold a lock
  (`deps.routing_lock`), or both checks fetch and the service sees a burst.
- **Transit and taxi times are estimates** (constants in `checks/legs.py`): label them as
  estimated, and keep the 25% margin before an estimate becomes a blocker. They and the pace
  limits in `checks/daily_load.py` are untuned; tune on the dev set, not by guessing.
- `opening-hours-py` rejects common-but-invalid hours like `Mo-Fr 9-17` (→ unknown) and uses
  its own `PH` calendar, separate from Nager.Date.
- Fake routing in tests: `tests/fake_osrm.py` (0.01° = 1 km; 5/15/30 km/h).

**Data**
- The 50,000 / 15,000 limits live in `parse/clean.py`; `scripts/save_reply.sh` repeats the
  raw one. Bash's `${var//pattern/}` takes minutes on long strings in macOS bash 3.2: use
  `[[ $var =~ ... ]]` in scripts.
- Dataset YAML is read with `dataset.validate.load_yaml`, not `yaml.safe_load`: YAML 1.1 reads
  `10:30` as 630 (base 60).
- The Kaggle CSV mangles accents (`H tel Regina`); hotels.yaml keeps the real `name` and the
  CSV's `csv_name` separately.
- Hotel theme keywords must mean the same in Python's `re` and Postgres `~*`: no `\b`, use
  `(^|[^a-z])`; a test compares both. Changing THEMES' phrasings needs
  `embed_hotel_themes.py`, or the check refuses the stale JSON.
- `data/dataset/` (texts included) exists only on this Mac until backed up.

## Open decisions

Tracked in `PLAN.md` ("Open"): judge model (another provider, decided when fixes are
judged); more demo samples; whether parse cost counts toward the per-check cost target;
hosting shape (below).

## Hosting (open questions)

The PRD's shape doesn't match the default homelab project (one Dockerfile, one port,
256 MB). To settle before the first deploy:

- Two services + Postgres. Auth.js needs a running Next.js server (no static export), so a
  single image means Next.js on `PORT` proxying `/api` to FastAPI on localhost; or a
  multi-container project type on the server. Postgres is separate either way.
- Review data: `hotels` + `hotel_reviews` are 295 MB (296K halfvec(384) rows, no vector index).
  The server gets them from a dump of those tables, not by re-embedding.
- Langfuse: Langfuse Cloud rather than self-hosting (optional; no traveller content sent).
- `app.env` needs `API_JWT_SECRET` (same value as the Next.js server) and `ANTHROPIC_API_KEY`;
  the Next.js proxy must be trusted via `FORWARDED_ALLOW_IPS` for per-IP demo limits.
- Email magic links need an email provider secret (not in the stack yet).

## Deployment

GitHub: https://github.com/vincentgoh0902/trip-feasibility (public, default branch `main`).

Not yet a homelab project. The Dockerfile is in `backend/`, so the server needs
`APP_BUILD_CONTEXT=backend` until the frontend lands and the hosting shape is settled. The
server session adds it with `new-project.sh`. Ship a change: commit and push, then (after
asking me) `ssh homelab '~/homelab/scripts/deploy.sh trip-feasibility'`.
