# How trip-feasibility works, and why

This explains what's been built, how the pieces fit, and the reasoning behind each choice,
including the tradeoffs and what was given up. Requirements are in [`PRD.md`](PRD.md);
progress and open decisions are in [`../PLAN.md`](../PLAN.md). Keep this file current when
the design changes.

**Status (Oct 23, 2026):** backend skeleton, database schema, CI, and the first two check
tools (public holidays, opening hours) exist and are tested. The parse pipeline, the
remaining checks, the agent, the API routes, auth and the frontend don't exist yet.

---

## 1. The big picture

A traveller pastes an itinerary. The system turns it into structured **stops**, runs a set
of **checks** over them, and reports **violations** (with evidence) plus proposed **fixes**.

```
 pasted text ──► parse pipeline ──► stops ──► agent ──► check tools (MCP) ──► violations ──► fixes
               (LLM only for step 1)            │        ├─ public_holidays   ✅ built
                                                │        ├─ opening_hours     ✅ built
                                                │        ├─ travel_time       ⏳
                                                │        ├─ daily_load        ⏳
                                                │        └─ hotel_red_flags   ⏳
                                                └──► every external lookup is cached in Postgres
```

The design rule that shapes everything: **the LLM interprets and proposes; code decides.**
The LLM extracts stops from messy text and drafts fixes. Facts (coordinates, opening
hours, holidays, travel times) always come from real data sources through code. That makes
the results checkable, and makes the evals meaningful: when a check is wrong, you can see
whether the data or the logic was at fault.

## 2. Repository map

```
trip-feasibility/
├── compose.yaml              Postgres (pgvector) + API for local runs
├── .github/workflows/ci.yml  lint, migrations, tests, secret scan
├── docs/PRD.md               requirements
├── docs/ARCHITECTURE.md      this file
├── PLAN.md                   progress checklist + open decisions
└── backend/                  Python 3.12 project managed by uv
    ├── Dockerfile, start.sh  container image; runs migrations then the server
    ├── alembic/              database migrations
    ├── scripts/smoke_checks.py   runs the tools once against the real APIs
    ├── src/trip_feasibility/
    │   ├── main.py           FastAPI app: /health, error handlers
    │   ├── config.py         all settings, read from environment variables
    │   ├── db.py             database engine + session helper
    │   ├── errors.py         the single error response shape
    │   ├── models.py         database tables (SQLAlchemy)
    │   ├── itinerary.py      the Stop schema every check takes as input
    │   ├── mcp_server.py     exposes the checks as MCP tools
    │   └── checks/
    │       ├── models.py     CheckResult / Violation / UnknownStop
    │       ├── deps.py       HTTP client, Postgres response cache, Unavailable error
    │       ├── holidays.py   public holidays check (Nager.Date)
    │       └── opening_hours.py  opening hours check (OSM Overpass)
    └── tests/                pytest; real Postgres, mocked HTTP
```

## 3. Backend foundation

### FastAPI app (`main.py`)

- **App factory + lifespan.** `create_app()` builds the app; the database engine is created
  when the app starts and closed when it stops (the "lifespan"), not at import time. Why:
  importing the module has no side effects, so tests and tools can import code freely, and
  the engine always points at whatever `DATABASE_URL` is set when the app actually starts.
- **`GET /health`** runs `SELECT 1` with a 2-second timeout. 200 = app and database are fine,
  503 = database unreachable. The homelab only needs a quick 200; the database check is
  there so a broken deploy shows up as unhealthy instead of looking fine.
- **One error shape** (`errors.py`): every error response looks like
  `{"error": {"code": "not_found", "message": "..."}}`, including FastAPI's own 404s and
  validation errors, which normally use a different format. The frontend only has to handle
  one format. Code that wants to fail raises `AppError(status, code, message)`.

### Configuration (`config.py`)

Everything comes from environment variables via `pydantic-settings`, with safe defaults for
local development. Nothing is hardcoded to a host or path, which is what the homelab
needs. Settings that exist today: database URL, port, the data-source URLs, HTTP timeouts,
cache lifetimes.

### Why uv, ruff, a `src/` layout

- **uv** installs dependencies and pins exact versions in `uv.lock`, so the Mac, CI and the
  Docker image all run identical versions.
- **ruff** does linting and formatting in one fast tool. CI fails on either.
- **`src/` layout** means tests import the *installed* package, not files that happen to sit
  next to them, which catches packaging mistakes early.

## 4. Database

### Why Postgres + pgvector (not SQLite)

Your other apps use SQLite, so this needs explaining. This app needs vector search over
hotel reviews (for the hotel red-flags check), combined with ordinary filters (this hotel,
this city). pgvector does both in one SQL query, inside the same database as everything
else. SQLite would need a separate vector store; that's a second service to run and keep in
sync. The cost: Postgres is a separate container with more memory than SQLite, which
complicates hosting (section 9).

### Schema (`models.py`, PRD "Data model")

Users own itineraries → itineraries have **versions** (every edit or accepted fix makes a
new version, nothing is overwritten) → each version can have many **check runs** → runs have
**violations** → violations have **fixes**. Plus `preferences` and `check_configs` per user,
`api_cache` for external lookups, and `eval_runs` for evaluation results.

Choices worth knowing:

| Choice | Why | Cost |
| --- | --- | --- |
| UUID primary keys | IDs appear in URLs (`/check/[runId]`); sequential numbers would let someone guess other users' IDs | Bigger than integers; irrelevant at this scale |
| Versions are append-only | You can always see what changed and re-run old versions; reproducibility | More rows |
| `config_snapshot`, `model_id`, `prompt_version` on every run | A result is only meaningful if you know exactly what produced it | A bit of duplication |
| JSONB for stops, evidence, tool outputs | Tool outputs will change shape as checks evolve; JSONB avoids a migration for every change | The database doesn't enforce their structure; Pydantic does instead |
| Allowed values as CHECK constraints, not Postgres ENUM types | Adding a value to a CHECK is a simple migration; changing Postgres ENUMs is awkward | None in practice |
| `ON DELETE CASCADE` from users down | "Delete my account" deletes everything with one statement, so nothing is left behind | A careless delete is far-reaching, so deletes go through specific routes only |
| Naming convention for constraints | Constraint names are predictable, so migrations can refer to them reliably | None |

**Deliberately left out:** `hotel_reviews`. Its `embedding` column needs a fixed vector size,
which depends on the embedding model you pick (open question). Creating it now would mean
a migration to change it later. The pgvector *extension* is already enabled.

**Known gap:** the PRD says demo runs "save nothing", but `check_runs.version_id` is required,
so a demo run currently has nowhere to live. To decide: keep demo runs in memory, or in a
separate table that is cleaned up regularly.

### Migrations (Alembic)

Schema changes are versioned migration files in `backend/alembic/versions/`. Workflow: edit
`models.py` → `alembic revision --autogenerate` writes the migration → review it → commit.

- **`alembic check` in CI** fails if `models.py` and the migrations disagree, so someone
  editing a model without writing a migration is caught before merge.
- **The container runs `alembic upgrade head` on every start.** Tradeoff: deploys need no
  manual step, but a broken migration means the container won't start. At one instance and
  low traffic, that's the right trade; with several instances you'd run migrations as a
  separate deploy step.

## 5. The check tools

### The shared contract

Every check takes a list of `Stop`s and returns a `CheckResult`:

```
CheckResult
├── status            "ok" | "unavailable"
├── violations[]      check_name, severity (info|warning|blocker), stop_ids, message, evidence, source
├── unknown[]         stop_id + reason: stops the check could not evaluate
├── checked_stop_ids  stops it actually evaluated
└── error             why it was unavailable
```

Why this shape:

- **Structured, never free text** (PRD FR-2). The agent, the frontend and the eval harness
  all read the same fields. Evals compare `(check, severity, stop_ids)` against annotations
  directly.
- **`unknown` is separate from violations.** OSM has no opening hours for many places. If
  "no data" counted as a violation, precision would collapse; if it were silently skipped,
  you couldn't tell good coverage from bad. Keeping it separate lets the README report
  coverage honestly (PRD risk: "OSM opening hours missing or stale").
- **`unavailable` instead of an exception.** If a data source is down, that one check says
  so and the run finishes with the other checks (PRD reliability requirement). Today proved
  it: both Overpass servers were down for hours and the tool degraded cleanly.
- **`evidence` + `source` on every violation** back the UX principle "evidence first": every
  card shows what triggered it and links to where the data came from.
- **`message`** isn't in the PRD's list; it's a human-readable sentence so the UI and the agent
  don't each have to build one from the evidence.

### The `Stop` schema (`itinerary.py`)

`id, day, date, start_time, end_time, place_name, type (venue|transfer|hotel|meal), osm
(type + id), lat, lon`. Times are optional because itineraries often don't state them; each
check decides what to do without a time (e.g. opening hours then checks the whole day).
Times are local to the city: "10:00 in Paris" is what matters for a museum, not UTC.

### Caching and dependencies (`checks/deps.py`)

`CheckDeps` bundles what every check needs: an HTTP client, the cache, settings. Checks take
it as an argument instead of creating their own, so tests can hand in a mocked HTTP client
and a test database.

The cache is the `api_cache` table: `key → response, expires_at`.

- **Why Postgres and not Redis:** it's already running, it survives restarts, and it's
  backed up on the server. Redis would be one more service for no real gain at this scale.
- **Failures are never cached.** If Overpass times out, the next run tries again instead of
  remembering the failure for a week.
- **"Not found" *is* cached** (OSM elements that don't exist), because that's a real answer.
- **Lifetimes:** holidays 30 days (they're fixed a year ahead), OSM tags 7 days (hours change
  occasionally). Both configurable.
- **Known gap:** the PRD says eval reruns make no new external calls. With lifetimes, an old
  cache entry eventually expires and gets refetched. The fix (planned) is a setting that
  makes evals use cached data regardless of age, so eval numbers don't drift as OSM changes.

### Public holidays check (`checks/holidays.py`)

Source: [Nager.Date](https://date.nager.at), free, no API key. One request per country per
year (a trip over New Year makes two), cached.

Severity rules, and why:

| Situation | Severity | Reasoning |
| --- | --- | --- |
| Public holiday (nationwide, or in your region) on a day with a venue stop | warning | It *might* close or crowd the venue, but most holidays don't close most venues; a blocker would be wrong far more often than right |
| Same day, no venue stops (meals, transfers) | info | Transport and restaurants are affected less, but it's worth knowing |
| Bank/optional/observance-only holiday | info | Shops and museums are rarely affected |
| Regional holiday, and the trip's region is unknown | info, "may not apply" | Can't tell, so it says so instead of guessing |
| Regional holiday in a different region | not reported | It doesn't apply |

One violation per holiday per day, listing all that day's stops, rather than one per stop.
That's less noise in the UI, and it matches how a person thinks ("the 14th is a holiday").

**Whether a specific venue closes** is the opening-hours check's job: OSM hours can say
`PH off` (closed on public holidays). The two checks complement each other: holidays gives a
general heads-up, opening hours a precise answer where the data exists.

### Opening hours check (`checks/opening_hours.py`)

Source: OpenStreetMap via the Overpass API. Each stop already carries its OSM ID (from
geocoding, which comes later), so the check looks up that exact element, not a name search.

**Parsing the hours.** OSM's `opening_hours` format is complex (`Tu-Su 09:30-23:45; Jun
21-Sep 02: 09:00-00:45; Jul 14,Jul 15 off; PH off`). Writing a parser would be a project in
itself, so it uses `opening-hours-py`, a maintained library (Rust core, Python bindings) with
arm64 builds for both the Mac and the server, and its own public-holiday calendar for `PH`
rules.

Tradeoff: that calendar is separate from Nager.Date, so in rare cases the two checks could
disagree about whether a date is a holiday. Accepted for now; if it shows up in evals, the
fix is to feed Nager's dates into the hours evaluation.

Hours that the library can't parse (e.g. `Mo-Fr 9-17` without minutes, which is common in
real data but not valid) are reported as **unknown**, not guessed at.

Severity rules:

| Situation | Severity |
| --- | --- |
| Closed for the whole visit | blocker |
| No time given, and closed all day | blocker |
| Only an arrival time, and closed at that moment | blocker |
| Open for only part of the visit | warning |
| OSM tags say the place closed down (`disused:*`, `demolished:*`, … and no current use) | blocker |
| The OSM element doesn't exist any more | warning (it may have closed, or been re-mapped under a new ID; that's not certain enough for a blocker) |
| No hours, unparseable hours, hours marked unknown, or no OSM ID | unknown |

Hotels and transfers aren't checked; their "opening hours" aren't meaningful for a visit.
Visits that cross midnight (23:00-01:00) are handled as running into the next day.

**Overpass reliability.** The public Overpass servers are often overloaded. Today the main
one returned 504s and timeouts for hours. So:

- Several servers are tried in order (`OVERPASS_URLS`). Mirrors must cover the whole planet;
  `overpass.osm.ch` looked healthy in testing but only has Swiss data.
- A short 10-second timeout per server (lookups by ID take 1-2 s when healthy), so two dead
  servers cost 20 s instead of a minute. The PRD budget is 60 s for a whole check.
- Overpass sometimes reports errors as HTTP 200 with a `remark`; those count as failures.
- All stops are looked up in **one** batched query, and only the ones not already cached.

If Overpass stays unreliable, the fallback is a cached snapshot of the cities in the test
set, or a self-hosted Overpass for those cities.

### Why MCP (`mcp_server.py`)

Each check is exposed as an MCP tool (PRD ADR-4). MCP is a standard protocol for giving
tools to an AI model: the tool declares its inputs and outputs as JSON Schema, and any MCP
client can call it.

- **Why not plain Python functions:** with MCP, the tools are testable on their own, usable
  from other clients (including Claude Desktop or Claude Code, for poking at them by hand),
  and the input/output schemas are generated from the same Pydantic models the code uses.
  It's also a skill worth showing.
- **It stays cheap:** the check logic is plain async functions; `mcp_server.py` is a thin
  wrapper. The agent (and the tests) connect **in-process** (`mcp.Client(server)`), so
  there's no subprocess or network hop. `python -m trip_feasibility.mcp_server` runs it
  over stdio for external clients.
- **SDK version:** this uses MCP Python SDK 2.x (`MCPServer`, `mcp.Client`), which is newer
  than most tutorials (they show `FastMCP` from 1.x).

## 6. Testing

- **Real Postgres, not mocks.** Tests run against a real database, because the cache upsert,
  constraints and migrations are exactly the things mocks get wrong.
- **A separate, guarded test database.** Tests use `trip_test` (created automatically).
  One test drops the whole schema to check migrations both ways, so `conftest.py` refuses
  to run against any database whose name doesn't end in `_test`. This exists because the
  first version wiped the dev database on every test run.
- **HTTP is mocked with respx, against fake hosts.** Tests point the tools at
  `nager.test` / `overpass-a.test`, so even a mistake can't hit the real services. CI is
  then fast and deterministic, and doesn't fail when Overpass is having a bad day.
- **What tests cover:** each severity rule, past-midnight visits, holiday rules, regional
  holidays, unknown handling, cache hits, batching, failover between Overpass servers,
  "unavailable" on every kind of failure, and the MCP round trip.
- **What they don't cover:** the real APIs. `scripts/smoke_checks.py` runs the tools once
  against them by hand. Today, Nager.Date worked live; Overpass was down, so the
  opening-hours logic was checked by replaying a real Overpass response for the Eiffel
  Tower (correctly a blocker on 14 July, which its OSM hours mark as closed).

## 7. CI (`.github/workflows/ci.yml`)

On every push to `main` and every PR: ruff lint + format check → apply migrations →
`alembic check` → pytest against a pgvector Postgres service → gitleaks over full git
history. Actions are pinned to specific versions. In Tier 3 this gains the eval regression
gate (fail a PR if precision or recall drops).

## 8. Docker

- **Multi-stage build:** the first stage installs dependencies with uv; the final image is
  `python:3.12-slim` with only the installed environment, no uv or build tools (327 MB on
  disk, mostly Python itself).
- **Runs as a non-root user** (uid 1001, matching your other apps, so files it writes on the
  server belong to your host user).
- **Listens on `PORT`** (default 8000) on 0.0.0.0, per the homelab rules. Locally, Compose
  publishes it on 8020 because 8000 is in use on the Mac.
- **Memory:** the API idles around 60 MB.

## 9. Hosting: the unresolved part

The homelab default is one image, one port, 256 MB. The PRD describes Next.js + FastAPI +
Postgres. Three things to settle before the first deploy:

1. **Two app services.** Auth.js needs a running Next.js server, so FastAPI can't just serve
   static files. Either one image runs Next.js on `PORT` and proxies `/api` to FastAPI on
   localhost, or the server gets a multi-container project type.
2. **Postgres size.** All 515K reviews at 1024 dimensions is ~2 GB of vectors before the
   index. Cheapest fix: load only the hotels in the test-set cities. Alternatives: a smaller
   embedding model, or `halfvec` (half the size).
3. **Langfuse:** use Langfuse Cloud's free tier rather than self-hosting it; self-hosted
   Langfuse needs several more services.

## 10. Decision log

| # | Decision | Alternatives | Why | Cost |
| --- | --- | --- | --- | --- |
| 1 | Backend first, frontend once the API works | Build both in parallel | PRD risk table: Tier 1 is overfull, and evals need only the backend | Nothing visible until later |
| 2 | Postgres + pgvector | SQLite + separate vector store | One database for relational data, vectors and cache | Heavier to host |
| 3 | Async SQLAlchemy + asyncpg | Sync SQLAlchemy | FastAPI, SSE streaming and many parallel HTTP calls are all async | Slightly more complex code |
| 4 | Migrations run on container start | Separate migration step | No manual deploy steps at one instance | A broken migration blocks startup |
| 5 | Checks return `unknown` separately | Treat missing data as pass or fail | Honest precision and coverage numbers | One more field to handle |
| 6 | Data-source failure → `unavailable`, not an error | Fail the whole run | One bad API shouldn't sink a check run | Results can be partial; the UI must say so |
| 7 | Cache in Postgres | Redis, in-memory | Already running, persistent, backed up | Slower than Redis (irrelevant here) |
| 8 | `opening-hours-py` for OSM hours | Write a parser | The format is too complex to do well quickly | Separate holiday calendar from Nager |
| 9 | Holidays mostly *warn*, never block | Block on any holiday | Most holidays don't close most venues; blocking would hurt precision | A real holiday closure only blocks via OSM `PH off` |
| 10 | Missing OSM element → warning | Blocker | It may be re-mapped, not closed | A truly closed place is only a warning |
| 11 | Several Overpass servers, 10 s timeout each | One server, long timeout | Public Overpass is unreliable; latency budget is 60 s | More config |
| 12 | MCP tools, connected in-process | Plain functions; separate MCP process | Standard protocol and schemas without network overhead | A dependency on a fast-moving SDK |
| 13 | Tests on a guarded `_test` database | Tests on the dev database | A test drops the schema; the guard makes data loss impossible | Two databases locally |
| 14 | Mocked HTTP against fake hosts in tests | Live API calls in tests | Deterministic, fast CI; no accidental real calls | Real API format changes are only caught by the smoke script |

## 11. Known gaps and next steps

- Eval runs should ignore cache expiry (section 5, caching).
- Where demo runs are stored (section 4).
- The holiday calendar used for `PH` rules differs from Nager.Date (section 5).
- Overpass reliability for the test-set cities (section 5).
- Next tools: travel time (OSRM / OpenRouteService), daily load, then the parse pipeline,
  hotel reviews and the agent. Order and status are in `PLAN.md`.

## Glossary

- **MCP (Model Context Protocol):** a standard way to give an AI model tools: each tool
  declares JSON Schemas for input and output, and any MCP client can call it.
- **OSM / Overpass:** OpenStreetMap is the open map database; Overpass is its read-only query
  API, used here to fetch a place's tags, including `opening_hours`.
- **Nager.Date:** a free public-holidays API, by country and year.
- **pgvector / HNSW:** a Postgres extension for storing embeddings and finding similar ones;
  HNSW is the index type that makes that search fast.
- **Alembic:** the migration tool for SQLAlchemy; each schema change is a versioned script.
- **respx:** a library that intercepts HTTP calls in tests and returns canned responses.
- **SSE (server-sent events):** a one-way stream from server to browser, planned for the live
  progress feed.
