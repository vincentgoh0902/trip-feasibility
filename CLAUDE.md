# trip-feasibility

Trip Feasibility Checker: a traveller pastes an itinerary and an AI agent reports what
will break (opening hours, public holidays, travel times, hotel red flags, daily load)
with evidence, and proposes re-checked fixes. Every reported result is backed by a
logged evaluation on a versioned, hand-annotated dataset.

**Requirements live in `docs/PRD.md`; progress and open decisions in `PLAN.md`. Read both
at the start of every session and update PLAN.md as work lands.** The PRD's dates are a
loose reference only.

- **Stack:** backend Python 3.12 + uv, FastAPI, Pydantic, LangGraph, MCP Python SDK,
  SQLAlchemy 2.0 + Alembic, pytest, ruff. Frontend Next.js (App Router) + TypeScript,
  Tailwind, shadcn/ui, Auth.js, openapi-typescript. Postgres 17 + pgvector. Langfuse
  tracing; MLflow for Tier 2 training only (Mac side).
- **Layout:** `backend/` (uv project: `src/trip_feasibility/` main.py app, config.py env
  settings, db.py engine/session, errors.py error shape, models.py schema; `alembic/`
  migrations; `tests/`; Dockerfile). Planned: `frontend/`, `train/` (Tier 2, Mac only).
  `data/` is gitignored (local Postgres volume, datasets, review dumps).
- **Schema changes:** edit models.py, then `uv run alembic revision --autogenerate -m ...`
  and review the file. CI runs `alembic check`, so models and migrations must match.
- **Errors:** every error response is `{"error": {"code", "message"}}`; raise `AppError`.
- **Parsing:** only step 1 (extraction) uses an LLM. The LLM never produces
  coordinates; geocoding is code. Parse accuracy measures step 1 only.
- **Checks:** each is an MCP tool returning structured JSON violations, never free text.
  External lookups are cached in Postgres (`api_cache`); eval reruns make no new calls.
- **Evals:** every run writes model ID, prompt version, config and dataset version to
  `eval_runs`. The held-out test set is touched only for reported results. The judge
  model is from a different provider than the agent model.
- **Styling:** follows `../Vincent Goh Branding Kit.pdf`. Colours as tokens, not hardcoded.
- **Port:** API listens on 8000 in the container (override with `PORT`); `GET /health` →
  200 with a DB check (503 if the DB is unreachable). Locally Compose publishes it on 8020.
- **Config (env):** `DATABASE_URL`, `PORT`. Secrets in `.env` / server `app.env`.
- **Memory:** API idles at ~60 MB. Postgres sizing depends on the review vectors (below).
- **Visibility:** homelab starts `private`. GitHub repo is **public**: never commit
  personal itineraries, API keys, review dumps or annotated datasets with personal details.

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

Not yet a homelab project. Once it runs in Docker locally and is pushed, the server
session adds it with `new-project.sh`. Ship a change: commit and push, then (after asking
me) `ssh homelab '~/homelab/scripts/deploy.sh trip-feasibility'`.
