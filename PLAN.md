# Plan and progress

Requirements: `docs/PRD.md`. Dates there are a loose reference only.

## Tier 1

- [x] Repo, Docker Compose, Postgres 17 + pgvector, Alembic schema (all tables except `hotel_reviews`)
- [x] FastAPI skeleton: `/health` with DB check, single error shape
- [x] CI: ruff, pytest against Postgres, `alembic check`, gitleaks
- [x] Tests use a separate `_test` database (guarded), so they never wipe dev data
- [ ] Collect and annotate ~30 itineraries (dev / test split)
- [x] Check contract (`CheckResult`), Postgres response cache, MCP server (SDK 2.x)
- [x] Public holidays tool (Nager.Date)
- [x] Opening hours tool (OSM Overpass, mirror fallback, `opening-hours-py`)
- [ ] Travel time tool (OSRM / OpenRouteService)
- [ ] Daily load tool
- [ ] Eval mode: read the cache regardless of expiry, so eval reruns make no external calls
- [ ] Choose embedding model; `hotel_reviews` table; load reviews and embeddings
- [ ] Hotel red-flags tool
- [ ] Parse pipeline (LLM extraction → validation → normalisation → geocoding)
- [ ] LangGraph agent run from a CLI; first baseline eval on the dev set
- [ ] Fixes with re-checks
- [ ] FastAPI routes, SSE, auth (verify Auth.js JWT), rate limits
- [ ] Judge calibration (20+ hand-rated fixes)
- [ ] Next.js pages, preferences, check settings, saved trips
- [ ] Injection suite
- [ ] Final test-set run on the pinned model; results in README

## Decisions

- Repo is public: never commit itineraries with personal details, API keys or review dumps.
- Local API host port is 8020 (8000 is taken on the Mac); the container listens on `PORT`.

## Open

- Overpass reliability: public servers were down for hours on Oct 23. If it persists, cache a
  snapshot of the test-set cities or self-host Overpass for them.
- `PH` rules use opening-hours-py's holiday calendar, Nager.Date is used by the holiday check;
  they could disagree. Revisit if evals show it.

- Hosting shape (see CLAUDE.md "Hosting"): Auth.js needs a running Next.js server, so a
  single image means Next.js on the public port proxying `/api` to FastAPI on localhost.
- Embedding model / vector dimension; agent and judge models; test-set cities; demo-mode scope.
- Demo runs "save nothing", but `check_runs.version_id` is required: decide where demo
  runs live (in memory, or a separate table with a short TTL).
