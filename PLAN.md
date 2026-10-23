# Plan and progress

Requirements: `docs/PRD.md`. Dates there are a loose reference only.

## Tier 1

- [x] Repo, Docker Compose, Postgres 17 + pgvector, Alembic schema (all tables except `hotel_reviews`)
- [x] FastAPI skeleton: `/health` with DB check, single error shape
- [x] CI: ruff, pytest against Postgres, `alembic check`, gitleaks
- [x] Tests use a separate `_test` database (guarded), so they never wipe dev data
- [x] Test-set cities and hotels picked (`docs/TEST_CITIES.md`)
- [ ] **Vincent: collect** 40 itineraries from ChatGPT, Claude and Gemini (10 per city: Paris,
      London, Barcelona, Vienna), using the hotels in `docs/TEST_CITIES.md`
- [x] Validator: dev/test split check (`dataset.py`, `split_errors`)
- [ ] Annotation format, rest of the validator, annotation guide (next)
- [ ] **Vincent: annotate** the itineraries (once the format exists; must be done before the
      first baseline eval, i.e. by the time the agent runs from a CLI)
- [x] Check contract (`CheckResult`), Postgres response cache, MCP server (SDK 2.x)
- [x] Public holidays tool (Nager.Date)
- [x] Opening hours tool (OSM tags via Nominatim `/lookup`, `opening-hours-py`)
- [x] Switched from Overpass to Nominatim; 1 req/s throttle and 60 s back-off on 429/403/503
- [x] Travel time tool (FOSSGIS OSRM table service; transit estimated)
- [x] Daily load tool (pace limits, walking distance)
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
- Dataset cities: Paris, London, Barcelona, Vienna (all in the Kaggle hotel reviews set), with
  16 hotels, 2 high- and 2 low-scoring per city (`docs/TEST_CITIES.md`).
- Transit: keep the estimate; annotations record the real transit time per leg, and one global
  set of constants is fitted on the dev set (no per-city tuning). Try Transitous only if the
  baseline shows transit dominates travel-time errors.
- **Vienna is held out (decided 2026-10-24).** Dev: 15 itineraries, 5 each from Paris, London
  and Barcelona. Test: the other 15 from those cities plus all 10 Vienna ones (25). Test
  results are reported twice: seen cities (Paris, London, Barcelona) and unseen city (Vienna).
  Rules:
  1. Synthetic variants from the injection script stay in the same set as their parent.
  2. Never show Vienna outputs, results or traces while building; only the final test run
     touches them.
  3. Anything changed after Vienna results have been seen is recorded in the README, and
     Vienna then no longer counts as unseen.
  4. Vienna results are reported as counts alongside percentages ("17 of 21"): small sample.
  5. Cached Nominatim or routing lookups for Vienna are fine; they don't leak labels.
  The validator enforces the counts, Vienna-in-test and variant-with-parent
  (`dataset.split_errors`).

## Open

- Tune the travel-mode constants (`checks/legs.py`) and pace limits (`checks/daily_load.py`)
  on the dev set once it exists.
- The frontend must show OSM attribution with a "fix the map" link (FOSSGIS policy).

- If traffic outgrows Nominatim's public policy: keyed service (NextGIS, Overpass.ch free
  tier) or self-hosted Nominatim/Overpass for the test-set cities (server session).
- `PH` rules use opening-hours-py's holiday calendar, Nager.Date is used by the holiday check;
  they could disagree. Revisit if evals show it.

- Hosting shape (see CLAUDE.md "Hosting"): Auth.js needs a running Next.js server, so a
  single image means Next.js on the public port proxying `/api` to FastAPI on localhost.
- Embedding model / vector dimension; agent and judge models; demo-mode scope.
- Demo runs "save nothing", but `check_runs.version_id` is required: decide where demo
  runs live (in memory, or a separate table with a short TTL).
