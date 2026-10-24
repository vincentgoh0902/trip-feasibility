# trip-feasibility

Trip Feasibility Checker: a traveller pastes an itinerary and an AI agent reports what
will break (opening hours, public holidays, travel times, hotel red flags, daily load)
with evidence, and proposes re-checked fixes. Every reported result is backed by a
logged evaluation on a versioned, hand-annotated dataset.

**Requirements live in `docs/PRD.md`; progress and open decisions in `PLAN.md`; how it all
works and why in `docs/ARCHITECTURE.md`; dataset rules in `docs/ANNOTATION.md`. Read them at the start of every session. Update
PLAN.md as work lands, and ARCHITECTURE.md (including its decision log) whenever a design
choice is made or changed: it is how I follow the project.** The PRD's dates are a loose
reference only.

- **Stack:** backend Python 3.12 + uv, FastAPI, Pydantic, LangGraph, MCP Python SDK,
  SQLAlchemy 2.0 + Alembic, pytest, ruff. Frontend Next.js (App Router) + TypeScript,
  Tailwind, shadcn/ui, Auth.js, openapi-typescript. Postgres 17 + pgvector. Langfuse
  tracing; MLflow for Tier 2 training only (Mac side).
- **Layout:** `backend/` (uv project: `src/trip_feasibility/` main.py app, config.py env
  settings, db.py engine/session, errors.py error shape, models.py schema, itinerary.py
  `Stop` schema, `dataset/` eval dataset (models.py annotation file format, split.py dev/test
  split rules, validate.py loading + validation + gold labels), mcp_server.py tools,
  `checks/` one module per check plus models.py (`CheckResult`) and deps.py (HTTP client,
  Postgres cache, `RateLimiter`, `Unavailable`),
  `checks/legs.py` (legs + travel options, shared by travel_time and daily_load),
  `checks/hotel_red_flags.py` + `hotel_themes.json` (theme query vectors, made on the Mac),
  `parse/` (pipeline.py end to end, extract.py, schema.py extraction schema + validation,
  normalise.py, geocode.py), `agent/` (graph.py LangGraph: enabled checks in parallel through
  `mcp_server.create_server(deps)`, then a fix node; fixes.py propose → apply → re-check →
  retry; models.py CheckInput/CheckReport), `checks/config.py` (CheckConfig, FR-5),
  `evals/` (runner.py, align.py parsed ↔ gold stops, score.py),
  `sources/nominatim.py` (OSM place tags and search), `sources/routing.py`
  (FOSSGIS OSRM table service); `alembic/`
  migrations; `tests/`; `scripts/` (smoke_checks.py live API run, validate_dataset.py,
  sample_reviews.py, smoke_parse.py live geocoding run, load_reviews.py, embed_hotel_themes.py,
  calibrate_hotel_themes.py, hotel_theme_base_rates.py, check_itinerary.py CLI, run_eval.py);
  Dockerfile). `docs/annotation/` holds the annotation template, a worked
  example and the unlabelled hotels.yaml. Planned: `frontend/`, `train/` (Tier 2, Mac only).
  `data/` is gitignored: local Postgres volume, `raw/hotel-reviews/Hotel_Reviews.csv` (Kaggle,
  CC0), `dataset/` (hotels.yaml + itineraries/<id>.yaml, the hand-annotated dataset),
  `evals/raw/<id>.md` (each chatbot reply exactly as copied, saved by `scripts/save_reply.sh`;
  the validator checks the YAML text matches it), `evals/runs/` (eval run details),
  `embeddings/` (cached review vectors) and `calibration/` (reviews hand-labelled by theme, from
  hotels outside the dataset, for the hotel check).
- **Schema changes:** edit models.py, then `uv run alembic revision --autogenerate -m ...`
  and review the file. CI runs `alembic check`, so models and migrations must match.
- **Errors:** every error response is `{"error": {"code", "message"}}`; raise `AppError`.
- **Fixes:** the LLM returns operations (move/remove/replace/add_transport_mode), never a new
  itinerary; code applies them and re-runs every check. Only valid fixes (targets gone, no new
  blocker) are shown. Hotel review text never goes into the fix prompt.
- **Parsing:** only step 1 (extraction) uses an LLM. The LLM never produces
  coordinates; geocoding is code. Parse accuracy measures step 1 only. The dataset's gold
  stops extend `parse.schema.ExtractedStop`, so changing that schema changes the dataset
  format. Geocoding never guesses: no match in the city box or within 60 km → unresolved.
- **Eval runs** use `make_check_deps(..., eval_mode=True)`: expired cache entries are served,
  so reruns make no external calls.
- **Checks:** each is a plain async function `(CheckDeps, stops, ...) -> CheckResult` in
  `checks/`, wrapped as an MCP tool in mcp_server.py (MCP SDK 2.x: `MCPServer`,
  in-process `mcp.Client(server)`). Never free text. Missing data goes in `unknown`, not
  violations; a failed data source raises `Unavailable` → `status="unavailable"`, never an
  exception out of the check. External lookups go through `CheckDeps.cached` / the
  `api_cache` table; failures are never cached. Tests mock HTTP with respx against the fake
  hosts set in conftest; nothing in the test suite may call a real API.
- **Vienna is held out** (PLAN.md "Decisions"): dev = 5 each from Paris, London, Barcelona;
  test = the other 15 plus all 10 Vienna. **Never show me Vienna outputs, results or traces
  while building**; only the final test run touches them. If anything changes after Vienna
  results were seen, record it in the README (Vienna is then no longer unseen). Variants stay
  in their parent's split; `dataset.split_errors` checks all of this. Cached Vienna
  Nominatim/routing lookups are fine.
- **Dataset annotations:** one YAML file per itinerary (format in `dataset/models.py`, example
  in `docs/annotation/example.yaml`). Gold stops are what parse step 1 should return;
  `place_name` must be copied verbatim from the text. Only opening_hours (by stop),
  public_holidays and daily_load (by day) are hand-labelled. travel_time labels are derived
  from the real leg times (blocker if gap < minutes, warning if gap < minutes + 10 min
  buffer; overlaps are always blockers), and every leg under 60 min must have a real time.
  hotel_red_flags labels come from hotels.yaml, one per hotel per itinerary. Labels use
  warning/blocker only; info isn't counted. Truth is reality (official sites, Google Maps),
  never our own checks or data sources.
- **Evals:** every run writes model ID, prompt version, config and dataset version to
  `eval_runs`. Two modes, reported apart: gold stops (checks alone) and parsed (end to end,
  stops aligned to gold by day + name). A prediction matches a label on check + target;
  severity agreement is separate. Details per itinerary in gitignored `data/evals/`. The held-out test set is touched only for reported results. The judge
  model is from a different provider than the agent model.
- **Styling:** follows `../Vincent Goh Branding Kit.pdf`. Colours as tokens, not hardcoded.
- **Port:** API listens on 8000 in the container (override with `PORT`); `GET /health` →
  200 with a DB check (503 if the DB is unreachable). Locally Compose publishes it on 8020.
- **Config (env):** `DATABASE_URL`, `PORT`, `HTTP_USER_AGENT`, `HTTP_TIMEOUT_S`,
  `NAGER_BASE_URL`, `NOMINATIM_BASE_URL`, `NOMINATIM_MIN_INTERVAL_S` (1.0),
  `NOMINATIM_TIMEOUT_S`, `ROUTING_BASE_URL`, `ROUTING_MIN_INTERVAL_S` (1.0),
  `ROUTING_TIMEOUT_S`, `RATE_LIMIT_COOLDOWN_S` (60), `ANTHROPIC_API_KEY` (secret),
  `EXTRACTION_MODEL` (claude-haiku-5-5), `EXTRACTION_EFFORT` (low), `FIX_MODEL`
  (claude-haiku-5-5), `FIX_EFFORT` (medium), `CACHE_TTL_HOLIDAYS_DAYS`,
  `CACHE_TTL_OSM_DAYS`, `CACHE_TTL_ROUTES_DAYS`; tests only: `TEST_DATABASE_URL`. Secrets in `.env` / server `app.env`.
- **Memory:** API idles at ~60 MB. Postgres holds ~300 MB of review data (below).
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
scripts/save_reply.sh paris-03                # clipboard → data/evals/raw/paris-03.md
uv run python scripts/validate_dataset.py --partial   # drop --partial once all 40 are done
uv run python scripts/sample_reviews.py "Hotel Cavendish" -n 40   # csv_name from hotels.yaml
uv run python scripts/smoke_parse.py               # live geocoding (+ extraction if key set)
uv run python scripts/check_itinerary.py --example  # all checks on the example (no key)
uv run python scripts/check_itinerary.py trip.txt --city Paris --start 2027-04-24
uv run python scripts/run_eval.py [--mode gold|parsed|both]   # dev split; logs to eval_runs
uv run python scripts/run_eval.py --split test --final         # reported results only
uv run --group embed python scripts/load_reviews.py   # embed + load hotel reviews (Mac only)
uv run --group embed python scripts/embed_hotel_themes.py      # after editing THEMES phrasings
uv run --group embed python scripts/calibrate_hotel_themes.py  # theme matcher P/R/F1
uv run python scripts/hotel_theme_base_rates.py               # per-theme min_share
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
- The agent runs checks in parallel. Shared external lookups need the cache-check-and-fetch
  under a lock (`deps.routing_lock`), or both checks fetch and the service sees a burst.
- `opening-hours-py` rejects common-but-invalid hours like `Mo-Fr 9-17` (→ unknown), and uses
  its own holiday calendar for `PH`, separate from Nager.Date.
- Before committing, check `git status` for stray files (an empty `.Rhistory` slipped in once).
- The pgvector extension is created in the initial migration. Autogenerated migrations
  write `pgvector.sqlalchemy.halfvec.HALFVEC(...)` without importing it: add
  `from pgvector.sqlalchemy import HALFVEC` by hand (CI's `alembic upgrade` would fail).
- The `anthropic` SDK (2.x) runs on `httpx2`, which respx doesn't intercept. Extraction tests
  pass a fake client object instead (`tests/parse/test_extract.py`); no test calls the API.
- Settings read the repo-root `.env` (`../.env` from backend/) and then `backend/.env`; real
  environment variables win. Put `ANTHROPIC_API_KEY` in the root `.env`.
- Embedding libraries (sentence-transformers, torch) are in the `embed` dependency group:
  `uv run --group embed ...`. The Docker image (`uv sync --no-dev`) never installs them, and
  the API never embeds at request time. `uv run --group embed` leaves numpy etc. in the venv,
  so tests can pass locally and fail in CI: run `uv sync` before pytest, and never import
  numpy in `src/` or `tests/`.
- Dataset YAML is read with `dataset.validate.load_yaml`, not `yaml.safe_load`: plain YAML 1.1
  reads `10:30` as the number 630 (base 60). The custom loader keeps times as text.
- The Kaggle CSV mangles accents (`H tel Regina`, `Batty Langley s`); hotels.yaml keeps the
  real `name` and the CSV's `csv_name` separately. All 16 csv_names were checked against the CSV.
- `data/dataset/` and `data/evals/raw/` are gitignored, so the annotations and raw replies
  exist only on this Mac until backed up.
- Hotel theme keywords must mean the same in Python's `re` (calibration) and Postgres `~*`
  (the check): no `\b`; use `(^|[^a-z])`. A test compares both engines. Changing THEMES'
  phrasings needs `embed_hotel_themes.py`, or the check refuses the stale JSON.
- Never tune the hotel check on the 16 dataset hotels' outputs before Vincent labels them, and
  never run it on Vienna hotels while building; calibration uses hotels outside the dataset.

## Open decisions

Tracked in `PLAN.md` ("Open"). Summary: embedding model and dimension; agent and judge
models: agent is Claude (decided), judge from another provider (later); demo-mode scope, and where demo runs live
(`check_runs.version_id` is required, but demo mode must save nothing); hosting shape below.

## Hosting (open questions)

The PRD's shape doesn't match the default homelab project (one Dockerfile, one port,
256 MB). To settle before the first deploy:

- Two services + Postgres. Auth.js needs a running Next.js server (no static export), so
  a single image means Next.js on `PORT` proxying `/api` to FastAPI on localhost.
  Alternatively a multi-container project type on the server. Postgres is separate either way.
- Review data: `hotels` + `hotel_reviews` are 295 MB in Postgres (296K halfvec(384) rows, no
  vector index). The server will get them from a dump of those tables, not by re-embedding.
- Langfuse: use Langfuse Cloud rather than self-hosting it on the server.
- Email magic links need an email provider secret (not in the stack yet).

## Deployment

GitHub: https://github.com/vincentgoh0902/trip-feasibility (public, default branch `main`).

Not yet a homelab project. The Dockerfile is in `backend/`, not the repo root, so the
server needs `APP_BUILD_CONTEXT=backend` until the frontend lands and the hosting shape
is settled. The server session adds it with `new-project.sh`. Ship a change: commit and push, then (after asking
me) `ssh homelab '~/homelab/scripts/deploy.sh trip-feasibility'`.
