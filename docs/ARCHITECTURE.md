# How trip-feasibility works, and why

This explains what's been built, how the pieces fit, and the reasoning behind each choice,
including the tradeoffs and what was given up. Requirements are in [`PRD.md`](PRD.md);
progress and open decisions are in [`../PLAN.md`](../PLAN.md). Keep this file current when
the design changes.

**Status (Oct 25, 2026):** backend skeleton, database schema, CI, and all five check tools
(public holidays, opening hours, travel time, daily load, hotel red flags) exist, are tested,
and work against the live data sources. The evaluation dataset is collected, its hotels are
labelled, and the itineraries are being annotated. Parse steps 1–4 exist (step 1, Claude
extraction, is untested against the live API until a key is added). Hotel reviews are
embedded and loaded. The agent runs parse, checks and fixes from a CLI
(`scripts/check_itinerary.py`) and from the REST API (section 6c: routes, live progress over
SSE, sign-in via a token from the frontend, a demo mode, rate limits, Langfuse tracing and
cost per run). The eval runner scores it on the annotated dataset (`scripts/run_eval.py`), and
the injection suite (section 6d) is ready to run once a key is set. The frontend (section 6e)
has its first slice: the landing page, the demo and the live results page; sign-in and the
signed-in pages are next.

---

## 1. The big picture

A traveller pastes an itinerary. The system turns it into structured **stops**, runs a set
of **checks** over them, and reports **violations** (with evidence) plus proposed **fixes**.

```
 pasted text ──► parse pipeline ──► stops ──► agent ──► check tools (MCP) ──► violations ──► fixes
               (LLM only for step 1)            │        ├─ public_holidays   ✅ built
                                                │        ├─ opening_hours     ✅ built
                                                │        ├─ travel_time       ✅ built
                                                │        ├─ daily_load        ✅ built
                                                │        └─ hotel_red_flags   ✅ built
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
├── docs/ANNOTATION.md        how to collect and annotate the dataset
├── docs/TEST_CITIES.md       dataset cities and hotels
├── docs/annotation/          template, worked example, unlabelled hotels.yaml
├── PLAN.md                   progress checklist + open decisions
├── frontend/                 Next.js 16 (App Router), Tailwind 4, shadcn/ui (section 6e)
│   ├── openapi.json          the API schema, exported from the backend (types come from it)
│   └── src/
│       ├── app/              pages (/, /demo, /check/[runId]); api/v1/[...path] proxies the API
│       ├── components/run/   results page: progress, timeline, violation + fix cards, evidence
│       ├── components/ui/    shadcn/ui components (copied in, owned and editable)
│       ├── hooks/            use-run-events.ts (the live feed)
│       └── lib/              api/ (generated schema.d.ts, clients, event types), run/ (state
│                             reducer, timeline layout), format.ts, config.ts
└── backend/                  Python 3.12 project managed by uv
    ├── Dockerfile, start.sh  container image; runs migrations then the server
    ├── alembic/              database migrations
    ├── scripts/smoke_checks.py   runs the tools once against the real APIs
    ├── scripts/validate_dataset.py   validates data/dataset, prints its version
    ├── scripts/sample_reviews.py     random negative reviews of one hotel (for labelling)
    ├── scripts/smoke_parse.py        parse the example itinerary live (extract if key set)
    ├── scripts/load_reviews.py       embed + load hotel reviews (Mac only, `embed` group)
    ├── scripts/embed_hotel_themes.py     theme query vectors → checks/hotel_themes.json (Mac)
    ├── scripts/calibrate_hotel_themes.py scores the theme matcher on hand-labelled reviews (Mac)
    ├── scripts/hotel_theme_base_rates.py how often each theme comes up per hotel (min_share)
    ├── scripts/check_itinerary.py    parse + check one itinerary from the command line
    ├── scripts/run_eval.py           score the checks on the dataset, log to eval_runs
    ├── scripts/save_reply.sh         clipboard → data/dataset/texts/<id>.md (raw chatbot reply)
    ├── scripts/show_clean.py         a saved reply as extraction sees it (after step 0)
    ├── scripts/run_injection_suite.py  prompt-injection suite, with and without defences
    ├── scripts/export_openapi.py     the API schema → frontend/openapi.json
    ├── src/trip_feasibility/
    │   ├── main.py           FastAPI app: lifespan wires the services, /health, /api/v1
    │   ├── api/              routes: itineraries.py, checks.py (runs, SSE, demo), fixes.py,
    │   │                     me.py; schemas.py (bodies), common.py (ownership, conversions)
    │   ├── auth.py           verifies the frontend's signed token, creates users
    │   ├── runs.py           check runs in the background: live events, saving, failures
    │   ├── ratelimit.py      per-user / per-IP hourly limits
    │   ├── tracing.py        Langfuse (optional), traced LLM and MCP clients, run usage
    │   ├── pricing.py        model prices → cost per call
    │   ├── demo/             demo sample itineraries (samples.yaml)
    │   ├── config.py         all settings, read from environment variables
    │   ├── db.py             database engine + session helper
    │   ├── errors.py         the single error response shape
    │   ├── models.py         database tables (SQLAlchemy), incl. hotels + hotel_reviews
    │   ├── itinerary.py      the Stop schema every check takes as input
    │   ├── parse/            parse pipeline: pipeline.py (end to end), extract.py, schema.py,
    │   │                     normalise.py, geocode.py
    │   ├── agent/            graph.py (LangGraph: checks in parallel via MCP, then fixes),
    │   │                     fixes.py (propose → apply → re-check → retry), models.py
    │   ├── evals/            eval harness: runner.py, align.py (parsed ↔ gold), score.py,
    │   │                     injection.py (prompt-injection suite)
    │   ├── mcp_server.py     exposes the checks as MCP tools
    │   ├── dataset/          evaluation dataset: file format, dev/test split, validation
    │   └── checks/
    │       ├── models.py     CheckResult / Violation / UnknownStop
    │       ├── config.py     CheckConfig: enabled checks + strictness (FR-5)
    │       ├── deps.py       HTTP client, Postgres response cache, RateLimiter, Unavailable
    │       ├── holidays.py   public holidays check (Nager.Date)
    │       ├── opening_hours.py  opening hours check (OSM data)
    │       ├── legs.py       legs between consecutive stops + travel options (shared)
    │       ├── travel_time.py    travel time check
    │       ├── daily_load.py     daily load check
    │       └── hotel_red_flags.py    hotel red-flags check (+ hotel_themes.json query vectors)
    ├── sources/
    │   ├── nominatim.py  OSM place tags and search via Nominatim (rate-limited, cached)
    │   └── routing.py    walk/bike/car times from the FOSSGIS OSRM servers (rate-limited)
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
complicates hosting (section 11).

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

**Demo runs aren't in the database** (decided 2026-10-25): the PRD says the demo saves
nothing, so demo runs live in the API process's memory for an hour (section 6c) and
`check_runs.version_id` stays required.

**Added for the API (2026-10-25):** `check_runs.report` (per-check status, unknown stops,
notes, the stops as checked, LLM usage), `error`, `finished_at`; `violations.message` and
`position` (report order); `fixes` now belong to a run and list the violations they target
(`violation_ids`; one fix can solve several), with `valid` and `details` (operations,
re-check notes). Invalid fixes are stored too, because fix validity is a metric; only valid
ones are shown. `itinerary_versions.transport_modes` holds a travel mode an accepted fix
allowed, for that version only.

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
├── error             why it was unavailable
└── notes[]           caveats on an "ok" result (e.g. walking distance couldn't be checked)
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
  so and the run finishes with the other checks (PRD reliability requirement). This was
  tested for real: when every Overpass server was down for hours, the tool degraded cleanly.
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
- **Failures are never cached.** If a data source times out, the next run tries again instead of
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

### Opening hours check (`checks/opening_hours.py`, `sources/nominatim.py`)

Source: OpenStreetMap data, read through **Nominatim's `/lookup` endpoint** (see "Why
Nominatim, not Overpass" below). Each stop already carries its OSM ID (from geocoding, which
comes later), so the check looks up that exact place, not a name search.

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
| The place isn't found (deleted from OSM, or not indexed by Nominatim) | warning (it may have closed, or been re-mapped under a new ID; that's not certain enough for a blocker) |
| No hours, unparseable hours, hours marked unknown, or no OSM ID | unknown |

Hotels and transfers aren't checked; their "opening hours" aren't meaningful for a visit.
Visits that cross midnight (23:00-01:00) are handled as running into the next day.

### Why Nominatim, not Overpass

The first version read OSM tags from **Overpass**, OSM's general query API. On Oct 23, 2026
every public Overpass server tested was down or timing out for hours: overpass-api.de,
overpass.kumi.systems, overpass.private.coffee, and the VK Maps server (22 s per query).
This turned out to be a lasting situation, not an outage:

- The main server's own error page says it is "at capacity" and that the public endpoint
  "is intended for browser-based and human-operated tools. For automated or high-volume use,
  run a local Overpass API instance."
- The maintainer posted (Aug 11, 2026) that the servers are "at capacity again", throughput
  has dropped sharply over the past year, adding servers is too expensive, and clients that
  keep getting rate-limited are now banned faster
  ([forum](https://community.openstreetmap.org/t/overpass-api-performance-issues/140598/157)).
- The [usage policy](https://wiki.openstreetmap.org/wiki/Overpass_API) counts all of an
  app's users together, asks regular users to stay around 1/100 of the ~10,000 queries/day
  guideline, warns "do not expect high reliability", and sends commercial use to paid or
  self-hosted servers. kumi.systems is no longer on its list of public servers.

The check only needs the tags of places whose IDs we already know. **Nominatim** (OSM's
search service, which geocoding will use anyway) returns exactly that: `/lookup?osm_ids=
W5013364&extratags=1` gave the Eiffel Tower's `opening_hours` in 0.13 s, the same value
Overpass returned earlier that day.

What switching gives up, and how it's handled:

- **Nominatim only indexes places with a name, address or meaningful tag.** An untagged
  node comes back "not found". That's harmless in practice: stops get their OSM IDs from
  Nominatim geocoding, so they're always indexed. `scripts/smoke_checks.py` includes one
  such node to show the behaviour.
- **Nominatim reports the main tag separately** (`category=tourism`, `type=museum`) rather
  than in `extratags`. `_place_tags` merges it back, so "this place closed down" detection
  still sees the current use and doesn't misfire on old `was:*` tags.
- **Nominatim has its own strict policy:** identify the app, at most **1 request per
  second**, cache results, no bulk use
  ([policy](https://operations.osmfoundation.org/policies/nominatim/)). That's handled in code:
  - `RateLimiter` (`checks/deps.py`) spaces requests ≥1 s apart within the process.
  - On HTTP 429, 403 or 503 it **stops calling Nominatim for 60 s**: the check reports
    "unavailable" without sending anything, instead of retrying into a ban. (Overpass's
    policy also asks for ≥30 s after a 429; the old code just moved on to the next server.)
  - One request covers up to 50 places; only uncached places are requested; results are
    cached for 7 days. A check usually makes 0 or 1 requests.
- **Volume:** ~30 eval itineraries plus light demo traffic fits the policy. If the app ever
  outgrows it, the options are a free keyed service (NextGIS, or Overpass.ch's free tier of
  10k requests/month), or self-hosting Nominatim/Overpass on the homelab with an extract
  covering only the test-set cities.

### Travel time and daily load (`checks/legs.py`, `travel_time.py`, `daily_load.py`)

Both checks need the same thing: the **legs** between consecutive stops on the same day,
with how long each takes. `legs.py` works that out once; because routes are cached per
pair, the second check makes no extra requests.

**Stops are taken in itinerary order, not sorted by time.** If an itinerary's times run
backwards, that is itself a problem worth reporting (as an overlap), not something to quietly
fix by re-sorting.

**Routing source: FOSSGIS OSRM servers** (`routing.openstreetmap.de`, `sources/routing.py`).
Free, no key, worldwide walking, cycling and driving. Tested live: Eiffel Tower → Louvre
came back as 3.7 km, 50 min on foot, 20 min by bike, 10.6 min by car, about 1 s each. It
uses OSRM's **table** service: one request per travel mode returns every pair at once, so a
whole itinerary costs 2-3 requests, not one per leg. Policy: at most 1 request per second,
identify the app, no heavy use, and **the UI must show OSM attribution with a "fix the map"
link** (a frontend requirement to remember). Same `RateLimiter` and 60 s back-off as
Nominatim.

Considered and rejected: **OpenRouteService** needs an API key, and third-party reports say
its current API host shuts down in early November 2026 (not confirmed from official
sources). It would also need its own key in `app.env`. Kept as a fallback idea.

**How each travel mode's time is worked out.** The traveller's own transport modes are used
(PRD FR-4), and the fastest one wins for each leg:

| Mode | Time | Why |
| --- | --- | --- |
| walk | OSRM foot route | Real route on the street network. Only offered for legs up to 3 km, unless walking is the traveller's only mode: nobody plans a 6 km walk between museums by default |
| bike | OSRM bike route | Real route |
| car (taxi) | OSRM car time × 1.3 + 5 min | OSRM car times assume empty roads; city traffic and waiting for pickup add time |
| transit | OSRM car time × 1.5 + 10 min | **No free routing service covers public transport.** This rough estimate (driving time, slower, plus walking to the station and waiting) is marked `estimated: true` in the evidence and "(estimated)" in the message |

The constants are starting guesses, to be checked against the dev set. Live in Paris the
estimate looked reasonable: Musée d'Orsay → Eiffel Tower came out at 21 min by public
transport, which matches a real metro trip. Real transit routing would need GTFS timetables
and a router like OpenTripPlanner, which is a project of its own.

**Travel time severity:**

| Situation | Severity |
| --- | --- |
| Stops overlap (the next starts before the previous ends): from the times alone, so also next to an unlocated or area stop | blocker |
| Gap more than 30 min shorter than the travel time (estimates counted 25% lower first) | blocker |
| Gap more than 10 min short, up to 30 | warning: "N min short" |
| Gap short by up to 10 min, or enough time (leave a few minutes early) | nothing |
| Strictness (`travel_buffer_min`: 0, 10 or 20; default 0) is slack wanted on top: 10 flags any shortfall, 20 wants 10 min to spare | warning ("tight" when it fits but with less to spare) |
| Two stops at the same place | never flagged; the buffer only applies when there's travel |
| A missing time, an unlocated stop, a neighbourhood under 2 km on foot from the stop before or after it, or no route | unknown |

Why the 25% margin: in the first live run, "21 min needed, 20 available" came out as a
blocker, but 21 was an estimate. A blocker should mean "this cannot work", so estimated
times only block when they're clearly over.

Why the 10-minute tolerance (decision 76): chatbot itineraries put slots back to back
("9:30–11:00 Cathedral, 11:00–11:30 Santa Caterina Market", a 5-minute walk). A traveller
absorbs that by leaving a few minutes early, so calling it "cannot work" made blockers
common and meaningless. The annotation guide uses the same rule for gold labels.

Why no label up to 10 min short, and blockers only past 30 (decision 78): with decision 76,
back-to-back slots stopped being blockers but became warnings, so nearly every timed
itinerary got one at every changeover (london-08: Shoreditch → Spitalfields Market, 0 min
gap, ~8 min walk). The few that mattered were buried, scores were padded with easy
arithmetic matches, and Vincent spent verification time on them. A gap that's a few
minutes short is absorbed, so it's not reported; a warning starts where the traveller
would actually be late (more than 10 min), a blocker where the plan can't hold (more than
30). The default buffer went from 10 to 0 with it: "less than 10 min to spare" can't be a
warning when 10 min short isn't. 30 is a starting guess, to be tuned on the dev set.

Why nearby area stops aren't timed (decisions 76 and 80): a neighbourhood ("El Born")
geocodes to one point near its middle, but the traveller is often already at its edge, so
routing to that point invents a journey. Geocoding marks such stops (`Stop.area`: Nominatim
`place=suburb`, `quarter`, `neighbourhood`, `borough`, `city_district`, or an administrative
boundary). A leg to or from one under `AREA_BESIDE_KM` (2 km) on foot is left out of both
checks (unknown for travel time; a note on daily walking): the traveller is beside it,
which the annotation guide counts as 0 minutes. Farther than that it's a real journey from
elsewhere, which the guide times to the area's nearest edge; the checker times it to the
centre point, a few minutes over. The 2 km came from the dev set (decision 80): every
area leg the gold labels was over 2 km on foot (2.2 and 4.4), every unlabelled one under
(0.7 to 1.8). The margin is narrow, so the test set will show whether it holds.

Why overlaps don't need locations (decision 77): "the next slot starts before this one ends"
is a fact about the times. It used to be found only on routed legs, so an overlap next to a
stop with no location ("Lunch") or an area was silently missed. barcelona-10 showed it: a
visit typed as "9:30 AM – 11:30 PM" runs into an unnamed lunch, and the travel-time check
said nothing, while opening hours and daily load each flagged the same typo. Overlaps are
now checked on every pair of consecutive timed stops, before any routing, with the
itinerary's own times as the source. If routing is down, the whole check is still
`unavailable` (overlaps included); keeping them would mean a partial result.

**Daily load** looks at each day: number of venue stops, length of the day (first start to
last end), and walking distance (walking legs, plus 0.4 km to and from stations for each
transit leg). Pace limits (PRD FR-4), untuned defaults:

| Pace | Venues per day | Day length |
| --- | --- | --- |
| relaxed | 3 | 8 h |
| moderate (default) | 5 | 10 h |
| packed | 7 | 13 h |

One result per day that crosses a limit, listing each. Walking over the traveller's limit →
**warning**; more than 1.5× → **blocker**. More venues or a longer day than the pace allows
→ **info** only ("looks busy"; decision 81): a hint shown to the traveller and evidence for
the LLM review, which judges whether the day is really too much. If routing is down, the
day is still checked for venues and hours, and a note says walking wasn't checked; that's
why `CheckResult` has `notes`.

Why counts are only a hint (decision 81): on the dev set they flagged 15 of 46 days, and
only 2 days are really too much, both for reasons a count can't see ("two galleries, a
matinee and maybe a second show"; "the Trianon estate needs a whole afternoon before a
17:00 train"). Those 2 were flagged by the same numbers as days that were fine (a 12-hour
morning-to-show day; 6 stops). OSM tags can't sort real visits from walk-throughs either
(a Gaudí house is tagged artwork, parks and avenues attraction, sights geocode to the
station named after them). The annotation guide says to judge load as a traveller would,
not by a formula, so the judgement belongs to the LLM review, with these numbers as its
evidence.

### Hotel reviews (`models.py` Hotel/HotelReview, `scripts/load_reviews.py`)

The data behind the hotel red-flags check (next section).

- **Source:** the Kaggle "515K Hotel Reviews Data in Europe" CSV (CC0), in `data/raw/`.
- **Scope:** every hotel in the four dataset cities: 1,227 hotels and 421K reviews, of which
  295,728 have a negative part. Only those are stored, because only complaints can evidence a
  red flag; stock answers like "No Negative" are dropped.
- **Embedding:** `BAAI/bge-small-en-v1.5` (384 dimensions, MIT licence), run on this Mac's GPU:
  3 minutes for all 296K texts. Vectors are normalised and stored as `halfvec` (half
  precision). Vectors are cached in `data/embeddings/` keyed by a hash of the model and texts.
- **Size:** `hotel_reviews` is 295 MB in Postgres (vs ~2 GB the PRD's 515K × 1024-dim floats
  plus an HNSW index would need).
- **No vector index:** the check always searches one hotel's reviews (at most ~4,800 rows),
  using the `hotel_id` B-tree index and an exact distance sort: about 2 ms measured. An HNSW
  index would cost memory for no gain, and an approximate index combined with a hotel filter
  can miss results.
- **No model on the server:** the check's queries are a fixed set of themes, so their vectors
  can be computed here once and shipped with the code. The 256 MB API container never loads
  an embedding model; sentence-transformers and torch live in the `embed` dependency group,
  which the Docker image doesn't install.
- **Separate `hotels` table** (name, address, city, coordinates, score), instead of repeating
  the hotel name on every review as the PRD sketch did.
- Probe: searching Hotel Cavendish's reviews for noise returns five noise complaints with
  cosine similarity 0.77–0.80.

### Hotel red-flags check (`checks/hotel_red_flags.py`)

Flags a hotel stop when its reviews keep complaining about one of four themes: noise,
cleanliness, safety, location. One **warning** per hotel (however many stops name it),
listing the flagged themes with counts, shares and the three closest example reviews.

**Which hotel.** The stop's name is matched against the city's hotels in the review data:
accents and the CSV's mangling ("Hôtel Regina" vs `H tel Regina`) are handled, "hotel",
"the" and the city name don't count, and the exact name beats look-alikes ("Hotel Cavendish"
vs "The Cavendish London"). Geocoded coordinates settle weak name matches (within 300 m) and
reject name matches more than 2 km away (another branch). Anything unclear → `unknown`, never
a guess. All 16 dataset hotels match by name alone.

**Which reviews count.** A review counts towards a theme when it contains one of the theme's
keywords **and** its embedding is close to one of three short theme phrasings (cosine
similarity ≥ a per-theme threshold). Measured on hand-labelled reviews from six hotels
outside the dataset (none in Vienna), labelled by Claude while building, not by Vincent:

| Theme | Embedding only, tuning set | Keyword + embedding, tuning set (240) | Keyword + embedding, held-out (180) |
| --- | --- | --- | --- |
| noise | F1 0.85 | F1 0.96 | **F1 0.98** (P 0.95, R 1.00, n=42) |
| cleanliness | F1 0.78 | F1 0.93 | **F1 0.83** (P 0.79, R 0.88, n=25) |
| safety | F1 0.50 | F1 0.80 (n=3) | **F1 0.56** (P 0.53, R 0.59, n=17) |
| location | F1 0.76 | F1 0.91 | **F1 0.47** (P 0.67, R 0.36, n=11) |

Embeddings alone drift into general complaints ("the hotel is dated" lands near "bad
location"); keywords alone hit "the room was clean". Safety and location are weak: "security
deposit" still matches safety, and location complaints are phrased too many ways for the
keyword list. The held-out set was scored twice: once before and once after fixing two
substring bugs it exposed (`rob` matched "problem", `hair` matched "chair"); no threshold
was tuned on it. `scripts/calibrate_hotel_themes.py` reproduces the table (the labelled
reviews are in gitignored `data/calibration/`).

**When a theme is a red flag.** At least `min_reviews` matching reviews (FR-5 strictness:
3, 5 or 10, default 5) **and** at least the theme's `min_share` of the hotel's negative
reviews. The count protects small hotels from flags on two reviews; the share protects big
ones (a hotel with 4,000 complaints has ten about anything). Themes differ a lot: across the
827 non-dataset hotels in Paris, London and Barcelona with 50+ negative reviews, noise is in
8% of a typical hotel's complaints and safety in 0.3%. A single 10% share flagged a third of
all hotels for noise and cleanliness and never fired for safety. So `min_share` is per theme,
the 90th percentile of those hotels (noise 15.9%, cleanliness 14.4%, safety 1.2%, location
6.3%): a red flag means more complaints than 90% of comparable hotels. These are defaults;
they get tuned against the hotel labels on the dev set.

**Serving.** The theme phrasings are embedded once on the Mac (`embed_hotel_themes.py`) and
shipped as `hotel_themes.json` (43 KB); the check refuses a file that doesn't match the
current phrasings or model. One SQL query per hotel computes all twelve distances and the
keyword tests over that hotel's reviews: ~0.1 s for a 1,500-review hotel. Review text in the
evidence is third-party data: the agent must treat it as data, never as instructions (the
injection suite tests this later). Every result carries a note that the reviews are from
2015–2017.

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

## 6. The parse pipeline (`parse/`)

PRD FR-1 turns pasted text into located stops in five steps, after a code-only cleaning
step. Only step 1 uses an LLM.

| Step | Where | Status |
|---|---|---|
| 0. Cleaning | `clean.clean_text`: chat-interface debris removed in code | Built |
| 1. Extraction | `extract.extract_stops`: Claude Haiku 5.5, structured output | Built; live run needs a key |
| 2. Validation | `schema.validate_extraction`: the model, or the error text for one retry | Built |
| 3. Normalisation | `normalise.normalise` | Built |
| 4. Geocoding | `geocode.locate_city`, `geocode.geocode_stops` | Built, works live |
| 5. User review | Frontend table | Later |

### Cleaning (`clean.py`)

Users paste whole chatbot replies, and a reply copied from a chat UI is often mostly debris:
a ChatGPT reply with photos ran to an estimated 30,000 characters, most of it image links of ~250
characters each, plus source citations (favicon link, title, `+1`), HTML entities
(`&#x20;`), markdown escapes (`\&`), and the text of interactive widgets (map hints like "Use
two fingers to move the map", ratings, "0 of 8" checklist counters). Step 0 removes what
follows a fixed pattern:

- lines that are only an image link (image hosts, image extensions, `purpose=inline`);
- citation blocks (a favicon link, a title, then `+N`); a favicon without `+N` goes alone;
- markdown images inside lines; other links keep their text (often a place name), not the URL;
- markdown escapes and HTML entities are decoded;
- lines that are only a number, a rating or a counter, and known widget lines;
- runs of blank lines are collapsed.

Prose that isn't a schedule (intro, tips, a booking checklist) stays: telling it apart from
the schedule is reading, so extraction does it (the prompt says a checklist that repeats
scheduled places adds no stops, and places only recommended outside the day-by-day schedule,
such as a "where to eat" list, aren't stops). The rules are conservative: a line that might be itinerary
stays.

Limits (PRD FR-1): 50,000 characters raw, only against abuse; 15,000 once cleaned, which is
what extraction is sized for (~4,000 input tokens on Haiku). Over either is `ParseError
too_long` before any LLM or geocoding call, with a message suggesting the "rewrite as plain
text" follow-up; text is never cut, as the checks would then pass days they never saw.
`ParsedItinerary.text` is the cleaned text; `CLEAN_VERSION` is logged with parsed-mode eval
runs, since a rule change shifts parse accuracy like a prompt change does. The dataset keeps
the raw reply (`texts/<id>.md`), so evals cover cleaning too, and gold `place_name`s are checked
against the cleaned text (`scripts/show_clean.py <id>` prints it).

Interactive widgets that don't reach the clipboard at all (Claude's day tabs) are a
different problem: there's nothing to clean. The paste box will offer a "rewrite as plain
text" follow-up line and warn when the parsed days don't cover the trip (PLAN.md "Open").

### Extraction schema (`schema.py`)

`ExtractedStop` has exactly the PRD's fields: day, start/end time, time of day, place name and
type. Unknown fields are rejected, so the model can't slip in coordinates. The dataset's
gold stops extend this class, so parse accuracy compares the same fields on both sides and
the two can't drift apart. Times like `9:00` are padded to `09:00`.

### Normalisation (`normalise.py`)

Day *n* becomes `trip_start + n − 1`; "morning", "afternoon" and "evening" become the fixed
windows in `itinerary.TIME_OF_DAY_WINDOWS` (09–12, 13–17, 18–21), the same ones annotators
use. Consecutive stops in one slot on the same day share its window in equal parts, in text
order (decision 79): "Afternoon: a museum, then a walk along the river" is
13:00–15:00 and 15:00–17:00, not two 13:00–17:00 visits that overlap by four hours. Stops
get ids `s1`, `s2`, ... in text order.

### Geocoding (`geocode.py`, `sources/nominatim.py`)

1. **Locate the city** (Nominatim search, `featureType=settlement`). Its bounding box scopes
   every search, and its country code is what the holiday check needs. London resolves to
   Greater London, Paris to the city proper, Barcelona and Vienna to their municipalities.
2. **Search each place name inside the city's box** (`bounded=1`), taking the top result.
3. **Otherwise search the whole country**, accepting the first result within 60 km of the
   centre, marked `outside_city` (day trips such as Versailles).
4. **Otherwise leave it unresolved** with a reason. The PRD says flag, never guess.

Each distinct name is searched once per itinerary; results, including "no results", are
cached for `CACHE_TTL_OSM_DAYS`, and every request goes through the Nominatim rate limiter.
Live, the example itinerary resolves fully (12 s cold, 0.5 s cached), including the café
inside the Louvre and Versailles as outside the city.

Why the top result: Nominatim ranks by importance, which favours the famous place a tourist
means. The cost is ambiguity (two cafés with the same name in one city); the user-review
step is where that gets corrected.

### Extraction (`extract.py`)

One call to Claude Haiku 5.5 (`EXTRACTION_MODEL`, effort `low`) per itinerary:

- **System prompt** (`SYSTEM_PROMPT`): the field rules from the PRD (times only when stated,
  `place_name` verbatim, what is and isn't a stop), and that the itinerary is data, not
  instructions. The user message gives the city and the trip start date (for itineraries that
  use weekdays or dates), with the text inside `<itinerary>` tags.
- **Structured output** (`output_config.format`, a hand-written `EXTRACTION_SCHEMA`) guarantees
  valid JSON of the right shape. The schema is hand-written rather than generated from the
  Pydantic model so it changes only deliberately, together with `PROMPT_VERSION`.
- **Step 2 still runs**: rules the schema can't express (time formats, "times or time of day,
  not both") are checked by Pydantic. On failure the model sees its output and the error and
  gets one retry; a second failure raises `ExtractionError("parse_failed")`.
- **Failures are typed**: `refused` (safety decline; Haiku has no server-side fallback),
  `truncated` (hit `max_tokens`), `unavailable` (API down, rate-limited, no key).
- **Usage is returned** (`ExtractionResult`: model, prompt version, attempts, tokens), so
  eval runs can log cost per itinerary (PRD target: under $0.01 per check).
- **Why Haiku at low effort:** extraction is copying, not reasoning; it's the cheapest current
  model, and parse accuracy on the dev set will show whether that holds.
- **Not cached:** the system prompt is far below the minimum cacheable length, so prompt
  caching would do nothing here.
- **Tests** use a fake client: the SDK (2.x) is built on `httpx2`, which respx doesn't
  intercept.

### Eval mode (`checks/deps.py`)

`make_check_deps(..., eval_mode=True)` makes the response cache serve expired entries too,
so re-running an eval makes no new external calls and sees the same data as the first run
(PRD: "eval reruns make no new external calls").

### End to end (`pipeline.py`)

`parse_itinerary` runs steps 0–4: clean and check the length (`too_long`), locate the city (unknown city → `ParseError
city_not_found`, before any LLM call), extract, normalise, geocode. It returns the stops,
every stop's geocode (unresolved ones listed), and the extraction's model, prompt version
and token counts. Step 5 (the user confirms the stops) sits between this and the checks, which
is why parsing and checking are separate calls, as in the PRD's API (`/itineraries/parse`,
then `/checks`).

## 6b. The agent (`agent/graph.py`)

Takes confirmed stops plus city, country, preferences and a `CheckConfig`, and runs every
enabled check through the MCP tool server, in parallel, as a LangGraph graph:

```
START ─► run_check(opening_hours) ─┐
      ├► run_check(travel_time) ───┼─► fix ─► END   → CheckReport (worst first) + FixReport
      └► … one Send per enabled check
```

- **No LLM decides which checks run.** It's configuration (FR-5): an LLM picking tools
  would add cost, latency and a way to silently skip a check, for nothing. The LLM's jobs in
  the agent are extraction (before) and fixes (the `fix` node, below).
- **In-process MCP:** `mcp.Client(create_server(deps))`. The caller's `CheckDeps` go into the
  server, so an eval run's cache-only mode reaches the tools.
- **A failed tool never sinks the run:** a tool error, a broken call or a missing country
  becomes `unavailable` for that check (PRD reliability requirement).
- **Report:** results in a fixed check order; violations sorted blocker → warning → info, then
  by the first stop they name.
- **Routing under parallel checks:** travel_time and daily_load need the same legs. Routing
  lookups now hold a lock around "check cache, fetch, store", so the second check reads the
  first one's cached result, and only one routing request is in flight at a time. Before the
  lock, the first live run sent the same table twice and FOSSGIS answered 429.
- **CLI:** `scripts/check_itinerary.py` parses a text file with Claude (or uses the example's
  hand-written stops with `--example`, no key needed), prints the stops and the report, and
  can write the report as JSON. The example skips hotel_red_flags: its hotel is a dataset hotel.
  Live run on the example: Louvre closed on Tuesday (blocker), Orsay → Eiffel Tower 21 min by
  transit in a 10-minute gap (blocker), a 12.5 h day (warning), two tight walks (warnings).
  (That was under the old bands; since decision 78 the tight walks aren't reported.)
  0.8 s with a warm cache.
- Streaming: `stream_agent` yields each check's result as it finishes and then the fixes, for
  the API's live feed (section 6c).

### Fixes (`agent/fixes.py`, the `fix` node)

PRD FR-3: at least one fix per warning or blocker, each re-checked so a fix never creates a
new blocker. The same split as parsing: **the LLM proposes, code applies and decides.**

1. **Propose.** One Claude call (`FIX_MODEL`, default Haiku 5.5 at medium effort) gets the
   stops, the traveller's preferences and every warning/blocker (as `v1`, `v2`, …) except
   those from advisory checks (`ADVISORY_CHECKS` in `checks/config.py`: public holidays,
   shown but never fixed; decision 75), and
   returns fixes in a fixed JSON schema. A fix is a description for the traveller plus
   operations: `move` (new day and/or times: reorder, move to another day, shorten),
   `remove`, `replace` (another place of the same kind, same times), `add_transport_mode`
   (e.g. allow a taxi). Never a rewritten itinerary, never coordinates.
2. **Apply.** Code applies the operations to a copy of the stops: days must be the trip's
   days, times must make sense, and a replacement place is geocoded in the city like any
   stop. An unknown place makes the fix invalid rather than guessed. Stops are re-sorted by
   day and time.
3. **Re-check.** Every enabled check re-runs on the patched stops, through the same MCP tools.
   A fix is **valid** when the violations it targets are gone (or the stop they were about was
   removed) and no new blocker appeared. New warnings are allowed but shown with the fix;
   changed stops a check couldn't judge are marked "not verified". A check that is
   unavailable during the re-check makes the fix invalid: it couldn't be verified.
4. **Retry once.** Violations still without a valid fix go back to the model with the failed
   fixes and their reasons. After that a violation can stay unfixed; the report says so.

Only valid fixes are meant to be shown; invalid ones are kept with their reasons, because
fix validity (valid ÷ proposed) is a PRD metric. `run_eval.py --fixes` measures it, plus the
share of violations that got a valid fix.

**Prompt injection.** Third-party free text never reaches the model: hotel evidence goes in
as theme counts only, OSM tags only as the few that say what a place is (section 6d), and
opening hours without their comments. All other tool output is wrapped in `<violations>` and
declared data. The injection suite (6d) measures how well this holds.

**Untested live:** like extraction, fixes have only run against a fake model so far (no API
key yet). Model and effort are settings, tuned once the fix-quality judge exists.

## 6c. The API (`api/`, `runs.py`, `auth.py`)

FastAPI under `/api/v1`, following the PRD's route table. The OpenAPI schema
(`/openapi.json`) is what the frontend's types will be generated from.

| Route | Who | What |
|---|---|---|
| `POST /itineraries/parse` | signed in | text → stops for review (parse steps 0–4); nothing saved |
| `POST /itineraries`, `GET /itineraries`, `GET/PATCH/DELETE /itineraries/{id}` | signed in | saved itineraries; delete cascades |
| `POST /itineraries/{id}/versions` | signed in | edited stops as a new version |
| `GET /demo/samples` | anyone | the demo itineraries |
| `POST /checks` | either | `{"version_id"}` (signed in, saved) or `{"sample_id"}` (demo, not saved); 202 with the run id |
| `GET /checks/{id}/events` | either | the live feed (SSE) |
| `GET /checks/{id}` | either | the run so far, or its result |
| `POST /fixes/{id}/accept`, `/reject` | signed in | accept → a new version |
| `GET /me`, `GET/PUT /me/preferences`, `GET/PUT /me/check-config`, `DELETE /me` | signed in | account, FR-4/FR-5 settings, deletion |

### Sign-in (`auth.py`)

PRD ADR-5: Auth.js signs people in inside Next.js; FastAPI verifies. How it verifies is the
choice here. Auth.js's own session cookie is an encrypted token whose format is an Auth.js
internal, so FastAPI doesn't read it. Instead the Next.js server signs a **short-lived JWT for
each API call** with a shared secret (`API_JWT_SECRET`, HS256) and sends it as a bearer token:
email, name, provider, issuer, audience, at most an hour of life. FastAPI accepts only that
algorithm, requires every claim, and creates the user on first sight (keyed by email). The
browser never holds the token: every API call, including the SSE feed (EventSource can't send
headers), goes through the Next.js server, which matches the hosting plan of Next.js in front
proxying `/api`.

- A missing token on a sign-in route is 401; a **bad** token is 401 everywhere, even on demo
  routes, so a broken session never silently becomes an anonymous call.
- No secret configured → sign-in routes answer 503; the demo still works.
- **Ownership:** every query for user data filters by the caller's id. Someone else's
  itinerary, version, run or fix is a 404, not a 403, so ids can't be probed. Tests try each
  route as a second user (PRD risk table).

### Check runs (`runs.py`)

`POST /checks` creates the run and returns at once (202). The run is an asyncio task owned by
`RunService`, not by the request, so closing the browser doesn't stop it (PRD ADR-7: background
tasks, no queue service).

1. **Locate:** the city, then any stop saved without coordinates (the traveller edited its
   name). Stops keep the coordinates parse found otherwise, so a run costs no geocoding.
2. **Checks:** the agent graph streams (`agent.graph.stream_agent`, LangGraph's
   `astream(stream_mode="updates")`): each check's result is published the moment it
   finishes.
3. **Fixes:** when the checks have found a warning or blocker and a key is set.
4. **Save** (signed-in runs only): violations, fixes (valid and invalid), the report, cost,
   latency and trace id; status `done`. Any failure becomes status `error` with a code.

**Events** (`type`): `step_started` (`geocode`, `checks`, `fixes`; the `checks` one carries the
located stops and the city, so the results page can lay out the itinerary before any result), `tool_result` (one check:
status, counts, unknown stops, notes), `violation` (with the id fixes refer to), `fix` (valid
ones only), `done` (the whole result) or `error` (`code`, `message`).

**How the feed works.** Every run's events are kept in memory while it runs (`LiveRun`). The
SSE route replays them from the start and then follows, so subscribing late or twice loses
nothing; a reconnect with `Last-Event-ID` resumes after that event; a comment line every 15 s
keeps proxies from closing an idle connection. A saved run leaves memory once it's written;
asking for its events afterwards gives one `done` (or `error`) event built from the database.

**Demo runs** live only in memory, for an hour (`DEMO_RUN_TTL_S`), at most 500 at once. Their
ids are random UUIDs, so only whoever started one can find it. This settles the "demo saves
nothing, but `version_id` is required" gap. The cost: a restart forgets demo runs, which is
harmless for a demo.

**Demo scope** (PRD open question, decided 2026-10-25): the demo checks **ready-made sample
itineraries only** (`demo/samples.yaml`, made up, never from the dataset), with stops already
extracted. Anonymous visitors can't paste text, so they never cause an extraction call;
fixes still run (about a tenth of a cent each), within the demo rate limit. Parsing pasted text needs
sign-in.

**Limits:** at most `MAX_CONCURRENT_RUNS` (4) runs at once; others wait their turn (Nominatim
and OSRM allow 1 request/second anyway). On startup, runs a previous process left `queued` or
`running` are marked `error: interrupted`; on shutdown, running ones are cancelled and
recorded the same way.

**What a run uses:** for a saved version, the traveller's saved preferences and check
settings (snapshotted into `config_snapshot`, FR-5), with the version's own travel modes if
an accepted fix allowed one. Demo runs use the sample's preferences and every check at
default strictness.

**Accepting a fix** creates a version (`source: fix`) from the checked version with that fix's
stops. Each fix was re-checked against that version alone, so accepting two fixes from one
run gives two separate versions; to combine them, accept one and check the new version
again. A fix that allowed a travel mode (e.g. taxi) sets it on the new version only, not on
the traveller's preferences.

### Rate limits (`ratelimit.py`)

Per hour, in memory (one API process): 20 check runs per user, 20 parses per user, 10 demo
runs per IP. Over the limit is a 429 with the wait in minutes. The client IP is uvicorn's
(`--proxy-headers`); behind the Next.js proxy, `FORWARDED_ALLOW_IPS` must trust it, or every
demo visitor shares the proxy's address. Several API containers would need the counts in
Postgres.

### Tracing and cost (`tracing.py`, `pricing.py`)

Every LLM call goes through `TracedClient` (a wrapper around the Anthropic client) and every
tool call through `TracedMcp`, so the agent code itself knows nothing about tracing:

- **Cost per run:** tokens × list price per model (`pricing.py`, Haiku 5.5 at $0.10 / $0.50
  per million tokens, more past 100K-token prompts), summed in `Usage`, stored as
  `check_runs.cost_usd` and returned with the run (and with each parse). A model missing from
  the price table gives an unknown cost, never a guess.
- **Langfuse** (Langfuse Cloud, hosting decision): a trace per run (`check_run`, with a span
  per check and a generation per model call: model, tokens, cost, latency) and per parse.
  `check_runs.trace_id` links to it. Off unless `LANGFUSE_PUBLIC_KEY` and
  `LANGFUSE_SECRET_KEY` are set; the SDK (~12 MB of memory) is only imported then.
- **No traveller content by default:** traces carry timings, models, tokens, statuses and
  counts, not the itinerary, prompts or outputs, because Langfuse Cloud is a third party and
  the itinerary is the traveller's. `TRACE_CONTENT=true` sends them (for local debugging).
- **Not yet:** parse cost isn't tied to the run that follows (parse saves nothing, by the
  PRD), so the PRD's "under $0.01 per check" is two numbers today: a parse and a run.
- **Live check (Oct 25):** the demo sample through the real tools from a warm cache: 187 ms,
  the same five problems the CLI finds.

## 6d. Prompt-injection suite (`evals/injection.py`, `scripts/run_injection_suite.py`)

PRD goal 4: third-party text must not steer the agent. Four ways such text reaches a model,
each attacked with six payloads (add an advertiser's place, drop stops, plant a link or
change times; polite and "SYSTEM OVERRIDE" phrasings), 24 cases:

| Vector | Where the payload sits | Reaches |
|---|---|---|
| itinerary | a line in the pasted chatbot reply | extraction |
| osm_tags | a tag (`description`) of a place OSM marks closed down | fix prompt (evidence) |
| osm_hours | a quoted comment inside a valid `opening_hours` | fix prompt (evidence) |
| hotel_review | a review excerpt | fix prompt (evidence) |

Success is judged in code from the structured output (the advertiser's name in a stop or
fix, more stops removed than the violations concern, the link or a 03:00 time), never by
another model. Reported as resistance (attacks not followed ÷ judged), per vector and goal,
**with and without defences**:

- **defended** (as shipped): both system prompts say tagged text is data, not instructions;
  review excerpts, free-text OSM tags and opening-hours comments never reach the fix prompt
  (`agent.fixes.safe_evidence`); output is a fixed JSON schema of stops or operations.
- **undefended:** the "data, not instructions" sentences removed and the evidence passed
  whole. The tags and output schema stay: they are the format, not a switch.

**Found while building it:** a place OSM marks as closed down put its *whole* tag set in the
evidence, and valid opening hours can carry quoted free-text comments; both went to the fix
model, and anyone can edit OSM. Now only the tags that say what a place is and whether it
operates pass (`amenity`, `shop`, `tourism`, …, also with a `disused:`-style prefix, with
vocabulary values only), and opening-hours comments are blanked. Fix prompt version
`fixes-v2`.

The base itinerary is the demo sample, so the suite touches no dataset itinerary (and no
Vienna). Needs the key; a few cents per run; each run writes an `eval_runs` row
(`metrics.kind = "injection"`, suite version = hash of payloads and sample). Results go in the
README once it has run. The dataset's planned injected *variants* (`parent_id`) are separate
and come after annotation.

## 6e. The frontend (`frontend/`)

Next.js 16 (App Router) with TypeScript, Tailwind 4 and shadcn/ui, as the PRD sets out. It
renders and calls the API; it holds no agent or check logic.

**Pages so far:** `/` (pitch, the five checks, how it works), `/demo` (the samples from
`GET /demo/samples`, each with a "Check this itinerary" button) and `/check/[runId]` (the live
results page). Sign-in, `/check/new`, `/trips` and the settings pages come next.

**Talking to the API.** The browser only calls its own origin: `app/api/v1/[...path]/route.ts`
forwards each request to FastAPI at `API_INTERNAL_URL` (server-side, default
`http://localhost:8020`). It passes on only `accept`, `content-type`, `last-event-id` and
`x-forwarded-for`, streams the response body through (the SSE feed included, marked
`no-transform` so compression can't buffer it), closes the upstream request when the browser
disconnects, and answers `502 api_unreachable` in the API's error shape when FastAPI is down.
This is where the per-call sign-in token (section 6c) will be added. Server components call
FastAPI directly (`lib/api/server.ts`).

**Types from the API.** `backend/scripts/export_openapi.py` writes `frontend/openapi.json`;
`npm run gen:api` turns it into `src/lib/api/schema.d.ts` (openapi-typescript), used through
`openapi-fetch`. A backend test fails when openapi.json is stale and CI fails when schema.d.ts
doesn't match it, so the contract can't drift silently. The SSE events aren't in OpenAPI;
their types are written by hand in `lib/api/types.ts`.

**The results page.** `useRunEvents` opens an `EventSource` on the run's feed and feeds every
event to a pure reducer (`lib/run/state.ts`, unit-tested): stops arrive with the checks step,
then results, violations and fixes as they happen, and `done` replaces it all with the final
run (a saved run's feed is just that one event). An HTTP error closes the feed, and the page
asks `GET /checks/{id}` why (an expired demo run is a 404). `lib/run/layout.ts` places each
violation on the day timeline: on its stop, on the leg after its first stop (travel time), on
the hotel's first stop (hotel flags) or on the day (daily load, holidays). Evidence has a view
per check, with a key/value fallback; stops a check couldn't judge say so ("Couldn't check ·
no opening_hours in OSM"). The footer carries the OSM, FOSSGIS and Nager.Date attribution and
a "Fix the map" link (FOSSGIS policy).

**Design.** The look matches the hero illustration (`public/hero.png`, a flat isometric
island). Its colours (cream floor, sage, terracotta wood, brown base, teal water, charcoal
screens) were sampled from the image and are the only palette: CSS variables in
`app/globals.css`, with darker shades derived where text needs WCAG AA (headings in bark, the
brown base at half brightness; secondary text in moss; source labels in a darkened sage).
shadcn's semantic tokens (`--primary`, `--card`, ...) point at them, and components use only the
semantic and severity tokens. Primary buttons are terracotta, secondary sage. Severity: blocker
terracotta, warning light wood, info teal, each with a soft tint and an ink shade at least
5.1:1 on it. Shapes copy the island: cards and buttons have 12–16 px corners and a solid,
darker "side face" below them (a hard box-shadow, `card-raised`), buttons press down onto it;
no blur shadows, gradients or glows. Teal wave strips (`WaveDivider`, a CSS mask so the colour
stays a token) separate major sections. The check icons (`components/site/check-icon.tsx`) are
inline SVG in the same palette. The hero image's own background (#f5eee2) is cut out in
`public/hero.webp` (made from the PNG by flood-filling the edge-connected background to
transparent), so it blends into the page whatever the encoding; the illustration floats
slowly, off under `prefers-reduced-motion`. Nunito (rounded) for headings, Hanken Grotesk for
text, JetBrains Mono for data, all from `next/font` (self-hosted at build). shadcn/ui components are copied into `components/ui/`, so they can be restyled freely.
Dark mode is Tier 3. Checked at 375 px with no horizontal scroll.

**Next.js 16 specifics:** Cache Components is on (the default for new apps), so anything that
reads params or fetches at request time sits inside `<Suspense>`; route types come from
`next typegen` (run by `npm run typecheck`). `AGENTS.md` points coding agents at the docs
bundled in `node_modules/next/dist/docs/`.

## 7. The evaluation dataset (`dataset/`, `docs/ANNOTATION.md`)

Every reported number is measured against a hand-annotated dataset, so its design decides
what the numbers mean.

### What's in it

40 itineraries, 10 per city (Paris, London, Barcelona, Vienna), generated by ChatGPT, Claude
and Gemini. Each names one of 16 hotels from the Kaggle review set (docs/TEST_CITIES.md).
**Vienna is held out of dev entirely:** dev is 5 each from Paris, London and Barcelona; test
is the other 15 plus all 10 Vienna. Test results are reported for seen and unseen cities
separately, so the gap between them measures how well the checker carries over to a city
it was never tuned on.

### Files

The dataset lives in `data/dataset/` (gitignored: the repo is public, and the global rule is
no datasets in git). Per itinerary, a YAML file and a text file, plus `hotels.yaml`:

```
data/dataset/
  hotels.yaml
  itineraries/<id>.yaml   metadata and labels
  texts/<id>.md           the reply exactly as copied (scripts/save_reply.sh)
```

- The YAML's top half is written when an itinerary is collected: the prompt, model, trip
  start date, preferences, split and the hotel the prompt names (`hotel`). The gold is added
  later: `violations` in the text's own words, `unknowns`, `other_issues`, who drafted them
  (`drafted_by`) and whether a human has verified them (`verified_by_human`). Dev files also
  carry gold `stops` and `legs`, for parser debugging only (decision 74).
- The text is only in `texts/<id>.md`; `load_dataset` reads it into `DatasetItinerary.text`,
  so nothing downstream knows about the split. A YAML with a `text` key, or without its text
  file, is an error; a text without a YAML is fine until the dataset is complete. Synthetic
  variants get a text file too, so every itinerary loads the same way.
- Why the text isn't in the YAML: whole chatbot replies run to tens of thousands of
  characters (section 6, Cleaning), which is unworkable as an indented YAML block. One copy
  also means nothing to keep in sync (it used to be pasted into the YAML and checked against
  a separately saved reply), and the folder is self-contained for backup, versioning and a
  later import elsewhere.

YAML because it's hand-written: comments, multi-line text blocks, little punctuation. Its
one trap: YAML 1.1 reads `10:30` as the number 630 (base 60), so files are read with a
custom loader (`validate.load_yaml`) that keeps times as text.

The dataset version stored with every eval run is a hash of all the files, texts included
(`dataset_version`), so any edit to any label or text gives a new version without needing
git.

### What's labelled, and by whom (violations first, decision 74)

| Check | Labelled how | Target |
|---|---|---|
| opening_hours | In the file, against official hours on the trip date | day + place |
| public_holidays | In the file, against official holiday lists | a day |
| daily_load | In the file, judged as a traveller with the stated preferences | a day |
| travel_time | In the file, against real door-to-door times (Google Maps) | day + place travelled to (+ `from_place`) |
| hotel_red_flags | Derived from hotels.yaml and the itinerary's `hotel` | the hotel |

- **Labels are in the text's words, not stop ids:** check, day, the place and time quoted
  as written (the validator checks they're in the cleaned text), severity, evidence, source
  and `confidence` (`verified`: looked up; `estimated`: judged). Any system's output (ours,
  a plain LLM) can be scored against them, whatever stops it extracted.
- **Drafts come from another provider than the extractor** (GPT or Gemini, never
  Anthropic), and count only once a human has checked every label (`verified_by_human:
  true`). `drafted_by` records the model, or `human`.
- **`unknowns`** are things the annotator couldn't establish: a prediction matching one is
  neither right nor wrong. **`other_issues`** are problems outside the checks; not scored.
- **Hotel labels are per hotel**, not per itinerary: 16 decisions instead of 40, from a
  fixed, seeded sample of reviews (`scripts/sample_reviews.py`).
- **Only warning and blocker count.** Info-level notes aren't evaluated.
- **Truth is the real world**, not this project's data sources. If labels came from OSM or
  Nager.Date, the evaluation would only show that the checker agrees with its own inputs;
  labelling against official sites means data gaps show up as missed problems, honestly.

### Drafting (`dataset/draft.py`, `evals/search_llm.py`, `scripts/draft_gold.py`)

Violation drafts come from a search-enabled model of another provider than the extractor.
`search_llm.py` puts any such model behind one interface (prompt + JSON schema in, JSON and
the exact model out), shared with the LLM-only baseline; the first backend is the Codex CLI
(`codex --search exec`, read-only in an empty folder, signed in with a ChatGPT plan, so no
API key). The drafter refuses Anthropic backends and test files.

- **What it sees:** the cleaned text (so its quotes can be checked), the traveller's prompt,
  the city, each day's date and weekday, and the preferences. Never gold stops, our checks
  or their output. The rules are a condensed ANNOTATION.md Violations section
  (`draft-v1`; `draft-v2` from 2026-10-29 adds the travel-time rules of decisions 76-77;
  `draft-v3` from 2026-10-30 the bands of decision 78).
  When those rules change, `RULES` changes with them and both prompt versions are bumped.
- **Checking:** output is validated like a hand-written file (shapes, quoted words verbatim,
  duplicates). Problems get one retry with the list of problems; entries still wrong are
  dropped and named in `notes`, so nothing is silently lost.
- **Writing:** `drafted_by` = model, effort, tool version and prompt version;
  `verified_by_human: false`. Raw outputs, with usage and search counts, go to
  `data/dataset-drafts/`, outside the dataset so its version doesn't change.
- First draft (paris-08): 9 searches, 2.6 minutes.

### The LLM-only baseline (`evals/baseline.py`, `scripts/run_baseline.py`)

What the pipeline is measured against: one search-enabled prompt per itinerary, no parser,
checks, map or routing data. Default: Claude Haiku 5.5 (medium effort), the extractor's
model, so the comparison isolates the pipeline; through headless Claude Code (`claude -p`,
the `claude-code` backend in `search_llm.py`) on a Claude plan, so no API key. It's run as a
plain model: its system prompt replaced, no settings, MCP servers or tools but WebSearch and
WebFetch, in an empty folder; a hook that adds to the context, or a run on another model,
fails it. Same inputs as the drafter (`draft.trip_block`) and the same rules for the four
checks (`draft.RULES`), with its own framing (`baseline-v3`, rules of decisions 76-78); never GPT, since GPT drafted
the gold. Output is checked for shape only (places are matched fuzzily) and cached per
itinerary, backend, model, effort and prompt version under `data/evals/baseline/`, keyed to
a hash of the prompt: a change to the text, dates or preferences reruns it. No hotel
labels (it has no review data); the comparison reports hotel_red_flags for our system only.
First run (paris-08): 9 searches, 1.5 minutes.

### Gold stops (dev only, for parser debugging)

Frozen with decision 74: no new stop rules, no stops for test files, and nothing scored on
them except parse accuracy and gold-stop mode. Gold stops are what parse step 1 (LLM
extraction) should return: `place_name` copied
verbatim (the validator checks it's in the text), times only when stated, `time_of_day`
otherwise. That makes them the reference for parse accuracy as well as the stops the
violations point at. `time_of_day` maps to fixed windows (`itinerary.TIME_OF_DAY_WINDOWS`:
morning 09–12, afternoon 13–17, evening 18–21), used by both the annotator and parse step 3.
A stated length of visit ("allow 30–40 minutes") goes in `duration_min`/`duration_max`, never
into `end_time` (decision 69); the checks don't use it yet.
`osm` is optional for now; a geocoding helper can propose it later for the annotator to
confirm.

### The validator

`scripts/validate_dataset.py` (with `--partial` while work is in progress) checks the schema,
that quoted places and times are in the cleaned text, duplicate labels, drafts not yet
verified, hotel keys and cities, file names, and the split rules (counts, Vienna in test,
variants in their parent's split). Stops and legs, when present, are checked too (verbatim,
in order, legs between consecutive located stops, dev only). It prints counts, label totals
and the version.

### The eval runner (`evals/`, `scripts/run_eval.py`)

Scores the checks against the labels, in two modes reported separately (decided 2026-10-24):

- **gold:** (dev files with gold stops only) the checks run on the gold stops, located from their OSM refs (the truth) or,
  for stops without one, geocoded by name. Scores measure the checks alone. No API key.
- **parsed:** the full pipeline on the raw text. Parsed stops are aligned one to one with
  gold stops within the same day by place name, order breaking ties (`align.py`). An
  unaligned parsed stop is extra; an unaligned gold stop was missed. Parse accuracy is scored
  on the aligned pairs, field by field (stop recall, stop precision, field accuracy, exact
  stops, errors by field), where gold stops exist. A parse failure counts every label of
  that itinerary as missed.

**Matching** (`score.py`). Predictions are turned into the labels' terms (`score.findings`):
check, day and the place as the system named it (travel_time: the stop travelled to). Only
warning and blocker count. A prediction matches a label with the same check and day and a
place name scoring at least 0.9 on `align.name_score` (one name inside the other counts),
one to one, whatever its severity; severity agreement is reported separately. A prediction
matching no label but one of the file's `unknowns` isn't counted. Counts are summed over
itineraries (micro-averaged); precision with nothing predicted is "n/a", not 0. Hotels
still unlabelled (`red_flags: null`) are left out of both sides.

**Uncertain names** (`evals/decisions.py`, `scripts/match_decisions.py`). Between 0.4 and
0.9 ("Picasso Museum" vs "Museu Picasso", "Casa Batlló" vs "Casa Milà") a human decides,
once. Decisions live in `data/evals/match_decisions.yaml` (gitignored; back it up with the
dataset), keyed by itinerary, check, day and the two names, so they apply to every later
run and to every system: ours and the baseline are judged by the same calls. Only what's
left unmatched after the sure matches is asked about, so a correct "Casa Batllo" doesn't
also raise "Casa Milà". Undecided pairs count as no match; the run prints how many there
are and stores the count (`undecided_pairs`) and a hash of the decisions
(`match_decisions_version`) with its metrics, so a provisional score is never mistaken for
a final one. Test-split pairs are listed and asked only with `--final`, after the final run,
since deciding them means reading test predictions. `--from-baseline` collects the pairs
from the cached baseline outputs. Why 0.4: translated names ("Musée d'Orsay" vs "Orsay
Museum") score 0.42; unrelated places on the same day and check score lower ("British
Museum" vs "Tate Modern", 0.32), and the same-check-same-day filter keeps the questions few.

**Reported numbers** are counts with percentages ("2 of 3 (67%)"), per check and overall,
plus coverage (runs where a check was unavailable, stops it couldn't judge), parse metrics,
tokens and time per itinerary. When Vienna is in the run, seen and unseen cities are also
reported apart.

**Rules it enforces.** Eval mode (expired cache entries served, so reruns make no external
calls once cached). Only annotated itineraries run, so it works on a partial dev set. The
test split needs `--final`, and prints no per-itinerary lines. Every run writes an
`eval_runs` row (dataset version, model, prompt version, config, all metrics) unless
`--no-save`; per-itinerary details go to gitignored `data/evals/runs/`.

**Checked on the example** (as a one-itinerary dataset): all 3 labels found (Louvre closed,
two travel-time problems), 2 false positives (a 12.5 h day the example calls fine, and a
hotel → Louvre walk the check estimates at 8 min where the annotation says 5).

## 8. Testing

- **Real Postgres, not mocks.** Tests run against a real database, because the cache upsert,
  constraints and migrations are exactly the things mocks get wrong.
- **A separate, guarded test database.** Tests use `trip_test` (created automatically).
  One test drops the whole schema to check migrations both ways, so `conftest.py` refuses
  to run against any database whose name doesn't end in `_test`. This exists because the
  first version wiped the dev database on every test run.
- **HTTP is mocked with respx, against fake hosts.** Tests point the tools at
  `nager.test` / `nominatim.test` / `routing.test`, so even a mistake can't hit the real
  services. CI is then fast and deterministic, and doesn't fail when a public service is
  having a bad day.
- **A fake routing server** (`tests/fake_osrm.py`) computes distances from the coordinates
  (0.01° = 1 km; walking 5 km/h, cycling 15, driving 30), so every expected travel time in
  a test can be worked out by hand.
- **What tests cover:** each severity rule, past-midnight visits, holiday rules, regional
  holidays, unknown handling, cache hits, batches of 50, the 1-request-per-second throttle,
  the 60 s pause after a 429/403/503, "unavailable" on every kind of failure, mode choice
  and estimates, the estimate margin, overlaps, same-place stops, pace limits, walking
  totals, partial results when routing is down, and the MCP round trip.
- **API tests** (`tests/api/`) run the real app against the test database with a stand-in for
  the agent graph (`fake_stream`), so runs are fast and deterministic: tokens (expired, forged,
  wrong audience, `alg: none`), every route as a second user (404), SSE replay, saved vs demo
  runs, failures as `error` events, rate limits, fix accept/reject. No test can reach Claude or
  Langfuse: conftest blanks their keys.
- **What they don't cover:** the real APIs. `scripts/smoke_checks.py` runs the tools once
  against them by hand. Live on Oct 23, 2026: all four tools worked (the Eiffel Tower is
  correctly a blocker on 14 July, which its OSM hours mark as closed; a deliberately packed
  Paris day got four travel-time violations and an overloaded-day warning), about 12 s for
  the first run, and a rerun made zero external requests.

## 9. CI (`.github/workflows/ci.yml`)

On every push to `main` and every PR: ruff lint + format check → apply migrations →
`alembic check` → pytest against a pgvector Postgres service → gitleaks over full git
history. Actions are pinned to specific versions. In Tier 3 this gains the eval regression
gate (fail a PR if precision or recall drops).

## 10. Docker

- **Multi-stage build:** the first stage installs dependencies with uv; the final image is
  `python:3.12-slim` with only the installed environment, no uv or build tools (327 MB on
  disk, mostly Python itself).
- **Runs as a non-root user** (uid 1001, matching your other apps, so files it writes on the
  server belong to your host user).
- **Listens on `PORT`** (default 8000) on 0.0.0.0, per the homelab rules. Locally, Compose
  publishes it on 8020 because 8000 is in use on the Mac.
- **Memory:** the API idles around 145 MB since the routes import the agent stack (LangGraph,
  MCP, the Anthropic SDK) at startup; 152 MB after five demo runs (Oct 25). Under the 256 MB
  default, with less headroom than the ~60 MB the bare app used.

## 11. Hosting: the unresolved part

The homelab default is one image, one port, 256 MB. The PRD describes Next.js + FastAPI +
Postgres. Three things to settle before the first deploy:

1. **Two app services.** Auth.js needs a running Next.js server, so FastAPI can't just serve
   static files. Either one image runs Next.js on `PORT` and proxies `/api` to FastAPI on
   localhost, or the server gets a multi-container project type.
2. **Postgres size.** All 515K reviews at 1024 dimensions is ~2 GB of vectors before the
   index. Cheapest fix: load only the hotels in the test-set cities. Alternatives: a smaller
   embedding model, or `halfvec` (half the size).
3. **Langfuse:** use Langfuse Cloud's free tier rather than self-hosting it; self-hosted
   Langfuse needs several more services. The API sends no traveller content by default
   (section 6c).
4. **Secrets the API needs on the server:** `API_JWT_SECRET` (shared with the Next.js server),
   `ANTHROPIC_API_KEY`, and the Langfuse keys if tracing is on; `FORWARDED_ALLOW_IPS` for the
   proxy in front.

## 12. Decision log

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
| 11 | OSM tags from Nominatim `/lookup`, not Overpass | Overpass mirrors; keyed services; self-hosting | Public Overpass is at capacity and asks automated clients to self-host; Nominatim answers ID lookups fast and geocoding needs it anyway | Unindexed places come back "not found"; must keep to 1 request/second |
| 11b | Throttle + 60 s pause on 429/403/503 | Retry, or fail over immediately | OSM services ban clients that retry into rate limits | A burst of checks waits, or reports "unavailable" for a minute |
| 12 | MCP tools, connected in-process | Plain functions; separate MCP process | Standard protocol and schemas without network overhead | A dependency on a fast-moving SDK |
| 13 | Tests on a guarded `_test` database | Tests on the dev database | A test drops the schema; the guard makes data loss impossible | Two databases locally |
| 14 | Mocked HTTP against fake hosts in tests | Live API calls in tests | Deterministic, fast CI; no accidental real calls | Real API format changes are only caught by the smoke script |
| 15 | FOSSGIS OSRM (table service) for routing | OpenRouteService; one request per leg | Free, no key, all pairs in one request per mode | 1 request/second; must show OSM attribution |
| 16 | Transit time estimated from driving time | GTFS + OpenTripPlanner; leave transit out | No free transit router; most city trips use transit | An estimate, so it's labelled, and only blocks when clearly short |
| 17 | Estimated times block only when >25% short | Treat estimates like exact times | A blocker must mean "can't work"; avoids false blockers | Some real problems show as warnings |
| 18 | Legs computed once, shared by two checks | Each check routes on its own | One routing cost, consistent numbers | The checks are coupled through `legs.py` |
| 19 | Pace limits as fixed starting defaults | Ask the LLM to judge load | Deterministic and testable; tuned on the dev set | Defaults may not suit everyone until tuned |
| 20 | Dataset cities: Paris, London, Barcelona, Vienna; 2 high- + 2 low-scoring hotels each | Any cities; Amsterdam/Milan too | All four have hotel reviews, so every check is evaluated in every city; high-scoring hotels are false-positive controls | Results are measured on four European cities only; other cities run but are unmeasured |
| 21 | Transit constants fitted globally from annotated real times | Per-city tuning; a transit router now | Per-city constants wouldn't carry over to unseen cities; the baseline shows whether a router is worth it | Transit stays an estimate in Tier 1 |
| 22 | Vienna held out of dev entirely (2026-10-24); dev 15, test 25 | Random split across all four cities | Measures how the checker does on a city it was never tuned on; variants stay with their parent so nothing leaks | Vienna's numbers rest on 10 itineraries, so they're reported as counts too; seeing them while building spoils them |
| 23 | One YAML file per itinerary, in gitignored `data/dataset/` | JSON; a spreadsheet; the database; committed to the repo | Hand-written: comments and multi-line text; a file per itinerary keeps edits small | YAML's `10:30` trap needs a custom loader; the dataset needs its own backup |
| 24 | Travel-time labels derived from recorded real times | Hand-label each tight leg | Labels can't contradict the times; the same times calibrate the transit estimate | Annotator must look up every leg under 60 min; long legs over 60 min are only covered when recorded |
| 25 | Hotel labels once per hotel, from a seeded review sample | Label per itinerary | 16 decisions instead of 40, reproducible | Ties the label to one sample of reviews |
| 26 | Labels are judged against reality, not our data sources | Label against OSM / Nager.Date | Measures the whole system, data gaps included | Lower scores where OSM data is poor |
| 27 | Labels name a stop, a leg, a day or a hotel, like the checks | Free-form spans | On gold stops, matching predictions to labels is a lookup | Parsed stops must first be aligned to gold stops; only warning/blocker counted |
| 28 | Dataset version = hash of the dataset files | Git tags; manual version numbers | Changes with any edit, no git needed for a gitignored folder | Not human-readable |
| 29 | Geocode inside the city's bounding box, then within 60 km in the same country | Unbounded search; bias only (`viewbox` without `bounded`) | Stops "Louvre" matching a Louvre elsewhere, while still finding day trips | A place just outside the box but over 60 km away stays unresolved |
| 30 | Take Nominatim's top result | Ask an LLM to pick; always show candidates | Importance ranking favours the famous place; no LLM touches coordinates | Same-name places can be wrong; the user-review step corrects them |
| 31 | Gold stops extend the extraction schema | Separate gold format | Parse accuracy compares identical fields; the formats can't drift | Changing the extraction schema changes the dataset format |
| 32 | Eval mode serves expired cache entries | Separate eval cache; snapshot files | Reruns are free and see identical data, using the cache that already exists | An eval rerun weeks later uses old opening hours (intended: same data) |
| 33 | Claude for the agent and extraction; Haiku 5.5 at low effort for extraction | OpenAI, Gemini; a bigger model | Strong structured output and tool use; extraction is copying, so the cheapest model first | Judge must come from another provider |
| 34 | Hand-written JSON schema + Pydantic validation + one retry | `messages.parse` with the Pydantic model | Schema changes only with the prompt version; step 2 stays explicit as the PRD describes | Schema and model must be kept in step by hand |
| 35 | Local open embedding model (bge-small-en-v1.5, 384 dims) | API embeddings (1536 dims) | No key, no cost, fast on the Mac's GPU; vectors a quarter the size | English-only, smaller model; retrieval quality to be checked on the hotel labels |
| 36 | Reviews for every hotel in the 4 dataset cities, negative parts only | 16 hotels only; all 515K reviews | Demo can check any hotel in those cities; 295 MB instead of ~2 GB | Hotels outside those cities have no reviews |
| 37 | `halfvec`, no vector index, exact search per hotel | `vector` + HNSW | Half the storage, no index memory, exact results in ~2 ms | Cross-hotel similarity search would be slow (not needed) |
| 38 | Embedding libraries in a separate `embed` group; no model on the server | Embed queries at request time | Keeps the API image small and inside 256 MB | Theme queries must be re-embedded here when they change |
| 39 | A review counts for a theme on keyword **and** embedding similarity | Embedding similarity alone; keywords alone; an LLM classifier | Best F1 on labelled reviews; no API cost per review; explainable | Safety and location are weak (F1 ~0.5 held out); keyword lists need upkeep |
| 40 | Red flag = count ≥ `min_reviews` and share ≥ the theme's 90th percentile | One fixed share for all themes; count alone (PRD) | Count alone flags every big hotel; one share flags a third of hotels for noise and none for safety | "Red flag" is relative to these cities' hotels; thresholds untuned against labels so far |
| 41 | Hotel stop → review data by name, coordinates as tie-breaker | Coordinates only; fuzzy search in SQL | Names identify hotels; the Kaggle coordinates and Nominatim's can differ by a block | Unclear names are `unknown`, even when a human could tell |
| 42 | The check graph runs every enabled check; no LLM chooses tools | A ReAct-style agent picking tools | Deterministic, cheaper, can't skip a check; LangGraph still gives parallel branches and streaming | The "agent" has no LLM until fixes land |
| 43 | Parsing and checking are separate calls | One graph from text to report | The user confirms stops in between (FR-1 step 5) | Two round trips in the UI |
| 44 | Routing lookups serialised with a lock | Let parallel checks route independently | Legs are shared through the cache; overlapping requests got a 429 | Routing for two checks can't overlap (it's 1 request/second anyway) |
| 45 | Hotel labels: red flag at 4+/40 reviews (noise, cleanliness), 3+ (location), 2+ (safety) (2026-10-24) | 4+/40 for every theme | Safety is ~0.3% of a typical hotel's complaints; at 4/40 repeated theft reports would never be labelled | Labels for rare themes rest on 2–3 reviews |
| 46 | Eval scores checks on gold stops and on parsed stops, separately (2026-10-24) | Gold only; end to end only | Separates check errors from parse errors; gold mode needs no API key | Parsed stops must be aligned to gold stops |
| 47 | Predictions match labels on check + target, severity scored separately | Require the same severity | A blocker reported as a warning still found the problem; both numbers stay visible | Detection scores look better than severity accuracy alone |
| 48 | Parsed stops aligned to gold by day and name, greedily, one to one | Alignment by position; by OSM ID | Names are copied verbatim, so they match; positions shift when a stop is added or dropped | Two same-day stops with near-identical names can swap (order breaks the tie) |
| 49 | Fixes are operations applied by code, not rewritten itineraries | LLM returns a new itinerary | Small, checkable, explainable changes; no invented coordinates or silently changed stops | Only four kinds of change can be expressed |
| 50 | Every enabled check re-runs on each fix; one retry for unfixed violations | Re-run only the target check; no retry | A fix that moves a stop can break other checks; a retry with reasons recovers many failures cheaply | Re-checks cost routing lookups; at most two LLM calls per run |
| 51 | Hotel review text kept out of the fix prompt | Send full evidence | Review text is untrusted third-party content (PRD injection goal) | The model can't quote reviews in a fix |
| 52 | Pasted text cleaned in code before extraction; limits 50,000 raw / 15,000 cleaned (2026-10-24) | One 5,000 limit, users regenerate; let the LLM read the debris; screenshots | Users paste whole replies, and a normal ChatGPT reply is an estimated ~30,000 characters, mostly image links; pattern removal is cheap, testable and keeps the LLM to reading | Rules follow today's chat UIs and need upkeep; unrecognised debris reaches the prompt (extraction mostly ignores it) |
| 53 | Dataset text in `texts/<id>.md`, not in the YAML; the YAML holds metadata and labels (2026-10-24) | Text in the YAML (it was); optional in the YAML | Whole replies are too long for YAML blocks; one copy can't drift; one self-contained folder; a single way to load | Two files per itinerary, matched by file name |
| 54 | FastAPI verifies a short-lived HS256 token the Next.js server signs per call (2026-10-25) | Decrypt Auth.js's session cookie (JWE) in Python; sessions in the database | Independent of Auth.js internals; nothing extra to look up per request; testable with any signer | A shared secret on both servers; the browser can't call the API directly (it goes through Next.js anyway) |
| 55 | Runs as asyncio tasks with in-memory event logs, replayed to each SSE subscriber (2026-10-25) | A queue (Redis/Celery); LangGraph checkpoints; polling only | PRD ADR-7; late or reconnecting subscribers miss nothing; no extra service | One API process: runs and live feeds don't survive a restart (interrupted runs are marked) |
| 56 | Demo runs in memory only, for an hour; demo checks ready-made samples only (2026-10-25) | A demo table with a TTL; pasted text with a rate limit | "Saves nothing" taken literally; anonymous visitors never spend on extraction; settles the `version_id` gap | A restart forgets demo runs; recruiters can't try their own itinerary without signing in |
| 57 | Saved runs store violations, all fixes (invalid too) and a report JSON; fixes belong to a run and list their violations (2026-10-25) | One JSON blob per run; a fix per violation | Fix accept/reject needs rows; one fix often solves several violations; invalid fixes feed the validity metric | A schema change to `fixes`; the report JSON isn't enforced by the database |
| 58 | Accepting a fix builds on the checked version, not the latest (2026-10-25) | Re-apply its operations to the latest version | A fix is only known to be valid against the version it was re-checked on | Two accepted fixes from one run give two versions, not one combined |
| 59 | Rate limits in memory, per hour: 20 runs and 20 parses per user, 10 demo runs per IP (2026-10-25) | Postgres counters; a proxy's limiter | One process today; no extra writes per request | Limits reset on restart; several processes would need shared counters |
| 60 | Tracing via wrappers around the LLM and MCP clients; no traveller content in traces by default (2026-10-25) | LangChain/OTel auto-instrumentation; full content | Agent code unchanged; works with or without Langfuse; privacy by default with a third-party host | Debugging a bad answer needs `TRACE_CONTENT=true` and a rerun |
| 61 | Cost from a local price table, per call (2026-10-25) | Langfuse's model prices; the Admin usage API | Stored with the run, independent of tracing; long-prompt rates apply per call | Prices are copied by hand and must be updated when they change |
| 62 | Injection suite judged in code on structured output, on the demo sample; OSM free text filtered from fix evidence (`fixes-v2`) (2026-10-25) | An LLM judge; dataset variants first | Deterministic, cheap, no dataset or Vienna exposure; the filter closes a gap the suite's design exposed | Payloads are ours, not collected from the wild; dataset variants still to do |
| 63 | Frontend calls go through a Next.js route handler that proxies `/api/v1/*` (2026-10-25) | Browser calls FastAPI directly (CORS); `next.config` rewrites | The token is added server-side and the browser never holds it (decision 54); one origin, no CORS; a rewrite can't add headers per user | Every call has an extra hop; the proxy must stream SSE without buffering |
| 64 | Frontend types generated from a committed `openapi.json`, checked in tests and CI (2026-10-25) | Generate from a running API at build time; hand-written types | Builds need no running backend; a stale schema or type file fails CI | One more step after API changes (export, then `gen:api`) |
| 65 | The `checks` step event carries the located stops and city (2026-10-25) | A new event type; fetch the stops separately; wait for `done` | The timeline can fill in live (PRD "progress, not spinners") without changing the event set | The event is larger (one copy of the stops) |
| 66 | Brand colours as CSS variables behind shadcn's semantic tokens; severity shades derived for AA contrast (2026-10-25) | Hardcoded colours; the kit's accents as text | Restyling is a change to one file; meets the PRD's WCAG AA requirement | Severity shades aren't in the kit |
| 67 | Palette and shapes taken from the hero illustration, replacing the branding kit's (2026-10-26) | Keep the kit's greens and Newsreader | One visual language from the illustration to the results page | Blocker shares the terracotta family with primary buttons (told apart by badge and edge); the site no longer follows the branding kit |
| 68 | Open choices ("X or Y") and activities with no named place aren't stops, in both the annotation guide and the extraction prompt (`extract-v4`) (2026-10-26) | Count both options; count the activity under its generic name | Neither can be located or checked; guide and prompt agree, so extraction is scored against the rule it was given | Real visits can go unchecked when the text doesn't commit |
| 69 | Stated durations are their own fields (`duration_min`, `duration_max`, minutes), in the gold stops and the extraction prompt (`extract-v5`); `end_time` stays stated-only. The same prompt version takes only the name from a heading like "Old Harbour photo stop" (2026-10-26) | Leave durations out; compute `end_time` from them | Duration-style itineraries are common, and without durations travel time and daily load mostly report unknown. Keeping them apart from `end_time` keeps gold labels to what the text says; code, not the LLM, can later derive an estimated end | One more field pair to annotate and score; travel time and daily load still need wiring to use it |
| 71 | Stated journeys are recorded on the stop travelled to (`travel_start`, `travel_min`, `travel_max`), and timed slots that leave the place open are stops named by the activity (`extract-v7`). A stated journey needs a real leg; real time over the claim + 10 min is a derived travel-time warning; a stated departure starts the gap (2026-10-26) | No field (journeys only implicit in the gaps); take leg times from the itinerary | Duration-style itineraries give no gaps, so the stated journey is their only travel information, and "allows 30–40 min, really 55" is the commute problem the product exists to catch. Legs stay reality, the claim is checked against them | The travel-time check doesn't produce claim warnings yet (gold has labels it can't predict until wired, with durations); one more field set to annotate |
| 73 | `located: false` marks a stop named by the activity ("Lunch"), in gold stops and extraction (`extract-v9`): never geocoded (unresolved with "not a place"), no leg required or allowed to or from it (2026-10-26) | Make annotators look up legs to nowhere; guess a nearby place; let geocoding search "Lunch" | About a third of the dev legs touched such stops and couldn't be looked up; searching an activity name can land on a café of that name, a silent guess | One more field to extract and score; their legs are never checked |
| 72 | Ten more stop rules, in the guide and the prompt (`extract-v8`): downtime isn't a stop (a named hotel return is); arrivals/departures only when a station, airport or hotel is named; a timed journey line sets the next stop's travel fields; several places in one slot each become a stop (first takes the start, last the end); places inside an area visit too, rooms of one venue not; a favoured option in a choice is the stop; "Morning:" counts as a timed slot; "optional" isn't a stop; time typos kept with a note; a slot that carries on at the place just named takes its name (2026-10-26) | Leave each case to the annotator's judgement | They came up across the dev files; written down, guide and prompt agree and the remaining files get the same treatment | Some real visits (optional ones, downtime) go unchecked; giving only the first place the start leaves the gaps inside a slot unknown |
| 70 | A timed slot is a stop: a meal or activity with its own stated time or duration counts even with no named place (`place_name` = the most specific area words, not a landmark it's near; else the activity's words). Travel sections are routes, not stops or durations. Both in the guide and the prompt (`extract-v6`); partly reverses 68 for timed slots (2026-10-26) | Keep 68 (no named place → no stop) | Leaving a timed lunch out turns its hour into travel slack, so a tight day looks feasible: the opposite of the product's job. Untimed unnamed meals still add nothing | No opening-hours check for these stops; an area geocodes roughly and a bare activity not at all (its legs unknown); opening hours must stay quiet on area stops, to verify when durations are wired in |
| 74 | **Violations first** (2026-10-27): gold labels name check + day + place and time as written, with severity, evidence, source and confidence (verified / estimated), plus `unknowns` and `other_issues`; scored by matching place names, not stop ids. travel_time is labelled directly (real time in the evidence), no longer derived from legs; hotel labels still come from hotels.yaml via a new `hotel` field. Drafts from a non-Anthropic model are allowed, recorded in `drafted_by`, and count only with `verified_by_human: true`. Gold stops and legs are frozen and dev only, for parser debugging | Keep stop-id labels derived through gold stops; keep "no LLM drafts" | Stop rules kept growing (decisions 68–73) and every new one changed the dataset; a label in the text's own words doesn't depend on how anyone splits the text into stops, so an LLM-only baseline can be scored on the same labels. A drafter from another provider than the extractor avoids rewarding shared blind spots; the human check keeps truth as reality | Fuzzy place matching (some ambiguous pairs need a human decision); drafts can anchor the annotator; travel-time labels lose the guaranteed consistency with recorded times |
| 75 | Public-holiday warnings are advisory: shown with the results, never sent to the fixer (`ADVISORY_CHECKS`, `fixes-v3`); a fix that moves a stop onto a holiday still lists the new warning beside it (2026-10-29) | Fix every warning and blocker, holidays included | A holiday is a fact about the date, not a fault a change to the plan solves: moving an open venue off 25 December trades a working visit for an unchecked gain. A real holiday closure is an `opening_hours` violation, which is fixed, so fixing the holiday too would duplicate it. Gold and scoring are unchanged (still a warning) | A closure missing from OSM (no `PH` rule) gets no fix, only the holiday warning telling the traveller to check; the fix-coverage metric now counts fewer violations |
| 76 | Travel time: a gap short by up to 10 min is a warning, a blocker only when overlapping or more than 10 min short (estimates still counted 25% lower). Area stops (neighbourhoods) are marked at geocoding (`Stop.area`) and their legs aren't timed; annotators count a journey into an area they're in or beside as 0 min, from elsewhere to its nearest edge, and don't label journeys to unnamed or activity slots. Same rule in the checker and ANNOTATION.md (2026-10-29) | Blocker whenever the gap is shorter than the journey; route to an area's centre point | Back-to-back slots a few minutes' walk apart are everywhere in chatbot itineraries and a traveller absorbs them; as blockers they drowned out real ones (barcelona-08: cathedral → Santa Caterina, 0 min gap, 5 min walk). An area's centre point is often farther than where the traveller already is, so its routed journeys mostly don't exist | Journeys from elsewhere into an area are labelled in gold but unknown to the checker; a 10-min shortfall on a tight schedule can still cascade, and is only a warning |
| 77 | Travel-time overlaps come from the times alone: every pair of consecutive timed stops on a day, located or not, area or not, before routing; source "Your itinerary's times". Gold labels an overlap even when the next slot names no place (2026-10-29) | Overlaps only on routed legs (as before) | An overlap needs no lookup, and the old way missed every overlap next to a "Lunch" or area stop (barcelona-10: a 9:30 AM – 11:30 PM typo into an unnamed lunch) | A time typo still also trips opening hours and daily load, which the gold labels once (as the overlap): those count as false positives. Typo detection is an open idea (section 13) |
| 78 | Travel time: a gap short by up to 10 min (or that fits) gets no label; more than 10 min short is a warning; a blocker only when overlapping or more than 30 min short (estimates still counted 25% lower). Default buffer 0 (was 10); the buffer is slack on top (10 flags any shortfall, 20 wants 10 min to spare). Same rule in the checker, ANNOTATION.md and the drafter/baseline prompts (`draft-v3`, `baseline-v3`) (2026-10-30) | Keep decision 76 (any shortfall up to 10 min a warning; less than 10 min to spare a warning) | Back-to-back slots made a warning at nearly every changeover in timed itineraries, burying the ones that matter, padding scores with easy matches and costing verification time (london-08: Shoreditch → Spitalfields Market, 0 min gap, ~8 min walk) | A traveller who can't walk fast, or a chain of 10-min shortfalls across a day, gets no travel-time warning (daily load may still catch a packed day); 30 min is untuned. Drafts made before it carry warnings that are now no label |
| 79 | Four fixes from the gold-mode diagnosis (eval runs `730a021b` → `8ef09a3b`, 2026-10-31): consecutive stops in one morning/afternoon/evening slot share its window in equal parts, in order (normalisation, annotation guide, `draft-v4`/`baseline-v4`); the holiday check gets every ISO 3166-2 code of the city's address (`regions`), so a regional holiday where the trip is becomes a warning; opening hours are evaluated with the stop's coordinates, so sunrise/sunset rules are placed; gold-mode stops are located like parsed ones (`located: false` never looked up, areas marked) | Keep full windows for every stop; one region code; leave the eval runner as it was | Shared windows made consecutive stops overlap by hours and put "late morning" at 09:00; regional holidays came back "may not apply"; without coordinates sunset is a fixed 19:00; the runner geocoded activity words (one landed 45 km away) and timed legs into neighbourhoods, which the app doesn't do | Equal shares are a guess (a 3-hour museum and a 20-minute walk get the same time); marking areas drops gold travel-time labels between far-apart areas (settled by decision 80) |
| 80 | A leg to or from a neighbourhood is timed when it's at least 2 km on foot (`legs.AREA_BESIDE_KM`); under that the traveller is beside it and the leg is left out, as before for every area leg (2026-10-31) | Never time area legs (decision 76); time all of them; subtract an edge allowance | Decision 79 marked areas in gold mode and two gold travel-time labels between far-apart areas disappeared; on the dev set every labelled area leg was 2.2 km or more on foot and every unlabelled one 1.8 km or less (run `095a4d6d`: travel time back to 5 of 8 found, no new false alarms) | Set on 7 dev legs with a narrow margin: tuned on dev, to be confirmed by the test set; timing to the centre overstates the journey a little |
| 81 | Daily load: venue count and day length over the pace limits are **info** ("looks busy"), not warnings; walking over the limit stays a warning, 1.5× a blocker. Whether a day is too much is judged by the LLM review (Tier 1 plan, step 3), with these numbers as evidence (2026-10-31) | Keep counts as warnings; raise the limits | On the dev set the counts flagged 15 of 46 days for 2 real ones, and those 2 had the same numbers as days that were fine; OSM tags can't tell a visit from a walk-through; the guide judges load as a traveller, not by formula. Raising the limits would be tuning on 2 labels and drop both | Until the LLM review exists, the system reports no overloaded days except on walking (dev run `146a353c`: daily load 0 of 2 found, no false alarms). Supersedes 19 for the counts |

## 13. Known gaps and next steps

- The holiday calendar used for `PH` rules differs from Nager.Date (section 5).
- Nominatim's 1 request/second limit is per process; fine for one API container, but several
  processes calling it would need a shared limiter (e.g. in Postgres).
- Transit times are estimates; the travel-mode constants are untuned guesses. The pace
  limits only produce info hints (decision 81); whether a day is too much waits for the
  LLM review.
- The frontend must show OSM attribution with a "fix the map" link (FOSSGIS policy).
- The dataset exists only on one Mac until it's backed up (gitignored by design).
- Parsed-mode evals need the API key; neither mode has run on real annotations yet.
- Extraction and fixes have only run against fake models; both need the API key for a live run.
- Time typos ("9:30 AM – 11:30 PM" for a morning visit) are reported as an overlap, plus
  opening-hours and daily-load violations for the same slip, where the gold has one label.
  Idea, not planned: code that spots a probable AM/PM slip and reports "check this time"
  instead (beyond the PRD's five checks; decision 77).
- Travel time doesn't time journeys into an area from elsewhere, which the gold labels
  (decision 76).
- Hotel red flags: safety and location matching is weak (F1 ~0.5 on held-out reviews), and
  `min_share` / `min_reviews` are untuned against the hotel labels.
- API runs, live feeds, rate-limit counters and demo runs live in one process's memory; more
  than one API container would need them in Postgres (section 6c).
- Parse cost isn't tied to the check run that follows it (section 6c).
- The injection suite hasn't run against the real model yet (needs the key).
- Frontend: sign-in (Auth.js) and the signed-in pages aren't built yet; no Dockerfile for it
  until the hosting shape is settled (section 11).
- Next: annotate the dataset (Vincent), the first baseline eval, and the frontend's signed-in
  half. Order and status are in `PLAN.md`.

## Glossary

- **MCP (Model Context Protocol):** a standard way to give an AI model tools: each tool
  declares JSON Schemas for input and output, and any MCP client can call it.
- **OSM / Nominatim / Overpass:** OpenStreetMap is the open map database. Nominatim is its
  search service (names → places, and place IDs → tags, used here for `opening_hours`).
  Overpass is its general query API, no longer used because the public servers are overloaded.
- **OSRM:** Open Source Routing Machine, a fast router over OSM roads and paths. FOSSGIS runs
  public servers for walking, cycling and driving; its "table" service returns travel times
  between many points in one request.
- **GTFS:** the standard timetable format for public transport; needed for real transit routing.
- **Nager.Date:** a free public-holidays API, by country and year.
- **pgvector / HNSW:** a Postgres extension for storing embeddings and finding similar ones;
  HNSW is the index type that makes that search fast.
- **Alembic:** the migration tool for SQLAlchemy; each schema change is a versioned script.
- **respx:** a library that intercepts HTTP calls in tests and returns canned responses.
- **SSE (server-sent events):** a one-way stream from server to browser, used for the live
  progress feed (`GET /checks/{id}/events`).
- **JWT:** a signed JSON token. Here the frontend's server signs a short-lived one per API call
  so FastAPI knows who is calling (section 6c).
- **Prompt injection:** text that tries to give an LLM instructions from inside the data it is
  reading (a review, a map tag, a pasted reply). Section 6d.
