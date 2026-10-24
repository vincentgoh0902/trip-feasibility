# How trip-feasibility works, and why

This explains what's been built, how the pieces fit, and the reasoning behind each choice,
including the tradeoffs and what was given up. Requirements are in [`PRD.md`](PRD.md);
progress and open decisions are in [`../PLAN.md`](../PLAN.md). Keep this file current when
the design changes.

**Status (Oct 24, 2026):** backend skeleton, database schema, CI, and all five check tools
(public holidays, opening hours, travel time, daily load, hotel red flags) exist, are tested,
and work against the live data sources. The evaluation dataset's format, validator and annotation
guide exist; the itineraries themselves are being collected. Parse steps 1–4 exist (step 1,
Claude extraction, is untested against the live API until a key is added). Hotel reviews
are embedded and loaded. The agent, the API routes, auth and the frontend don't exist yet.

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
    ├── src/trip_feasibility/
    │   ├── main.py           FastAPI app: /health, error handlers
    │   ├── config.py         all settings, read from environment variables
    │   ├── db.py             database engine + session helper
    │   ├── errors.py         the single error response shape
    │   ├── models.py         database tables (SQLAlchemy), incl. hotels + hotel_reviews
    │   ├── itinerary.py      the Stop schema every check takes as input
    │   ├── parse/            parse pipeline: extract.py, schema.py, normalise.py, geocode.py
    │   ├── mcp_server.py     exposes the checks as MCP tools
    │   ├── dataset/          evaluation dataset: file format, dev/test split, validation
    │   └── checks/
    │       ├── models.py     CheckResult / Violation / UnknownStop
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
| Stops overlap (the next starts before the previous ends) | blocker |
| Gap shorter than the travel time (walk/bike: exact; estimates: more than 25% short) | blocker |
| Estimated time slightly over the gap (within 25%) | warning: "may not be enough time" |
| Enough time, but less slack than the buffer (strictness: 0, 10 or 20 min; default 10) | warning |
| Two stops at the same place | never flagged; the buffer only applies when there's travel |
| A missing time, an unlocated stop, or no route | unknown |

Why the 25% margin: in the first live run, "21 min needed, 20 available" came out as a
blocker, but 21 was an estimate. A blocker should mean "this cannot work", so estimated
times only block when they're clearly over.

**Daily load** looks at each day: number of venue stops, length of the day (first start to
last end), and walking distance (walking legs, plus 0.4 km to and from stations for each
transit leg). Limits by pace (PRD FR-4), starting defaults to tune on the dev set:

| Pace | Venues per day | Day length |
| --- | --- | --- |
| relaxed | 3 | 8 h |
| moderate (default) | 5 | 10 h |
| packed | 7 | 13 h |

Over any limit → one **warning** per day, listing every limit exceeded. Walking more than
1.5× the traveller's limit → **blocker**. If routing is down, the day is still checked for
venues and hours, and a note says walking wasn't checked; that's why `CheckResult` has
`notes`.

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

PRD FR-1 turns pasted text into located stops in five steps. Only step 1 uses an LLM.

| Step | Where | Status |
|---|---|---|
| 1. Extraction | `extract.extract_stops`: Claude Haiku 5.5, structured output | Built; live run needs a key |
| 2. Validation | `schema.validate_extraction`: the model, or the error text for one retry | Built |
| 3. Normalisation | `normalise.normalise` | Built |
| 4. Geocoding | `geocode.locate_city`, `geocode.geocode_stops` | Built, works live |
| 5. User review | Frontend table | Later |

### Extraction schema (`schema.py`)

`ExtractedStop` has exactly the PRD's fields: day, start/end time, time of day, place name and
type. Unknown fields are rejected, so the model can't slip in coordinates. The dataset's
gold stops extend this class, so parse accuracy compares the same fields on both sides and
the two can't drift apart. Times like `9:00` are padded to `09:00`.

### Normalisation (`normalise.py`)

Day *n* becomes `trip_start + n − 1`; "morning", "afternoon" and "evening" become the fixed
windows in `itinerary.TIME_OF_DAY_WINDOWS` (09–12, 13–17, 18–21), the same ones annotators
use. Stops get ids `s1`, `s2`, ... in text order.

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
no datasets in git). One YAML file per itinerary, plus `hotels.yaml`:

- The top half is written when an itinerary is collected: the generated text exactly as it
  came, the prompt, model, trip start date, preferences and split.
- The annotation half is added later: gold `stops`, real travel times (`legs`) and
  hand-labelled `violations`.

YAML because it's hand-written: comments, multi-line text blocks, little punctuation. Its
one trap: YAML 1.1 reads `10:30` as the number 630 (base 60), so files are read with a
custom loader (`validate.load_yaml`) that keeps times as text.

The dataset version stored with every eval run is a hash of all the files
(`dataset_version`), so any edit to any label gives a new version without needing git.

### What's labelled, and by whom

| Check | Labelled how | Target |
|---|---|---|
| opening_hours | By hand, against official hours on the trip date | a stop |
| public_holidays | By hand, against official holiday lists | a day |
| daily_load | By hand, judged as a traveller with the stated preferences | a day |
| travel_time | Derived from the real leg times the annotator records | a leg |
| hotel_red_flags | Derived from hotels.yaml (one label per hotel per itinerary) | a hotel |

- **Targets match the checks' own output** (a stop, a pair of stops, a day), so on gold
  stops comparing predictions with labels is a lookup, not a fuzzy match.
- **Travel-time labels are derived** rather than hand-written: the annotator records the real
  door-to-door time (Google Maps, Citymapper) for every leg with under 60 minutes between
  stops, and the label follows the check's own rule (gap shorter than the journey → blocker;
  less than the 10-minute default buffer to spare → warning; overlap → blocker). Labels
  can't contradict the recorded times, and those times also calibrate the transit estimate.
- **Hotel labels are per hotel**, not per itinerary: 16 decisions instead of 40, from a
  fixed, seeded sample of reviews (`scripts/sample_reviews.py`).
- **Only warning and blocker count.** Info-level notes aren't evaluated.
- **Truth is the real world**, not this project's data sources. If labels came from OSM or
  Nager.Date, the evaluation would only show that the checker agrees with its own inputs;
  labelling against official sites means data gaps show up as missed problems, honestly.

### Gold stops

Gold stops are what parse step 1 (LLM extraction) should return: `place_name` copied
verbatim (the validator checks it's in the text), times only when stated, `time_of_day`
otherwise. That makes them the reference for parse accuracy as well as the stops the
violations point at. `time_of_day` maps to fixed windows (`itinerary.TIME_OF_DAY_WINDOWS`:
morning 09–12, afternoon 13–17, evening 18–21), used by both the annotator and parse step 3.
`osm` is optional for now; a geocoding helper can propose it later for the annotator to
confirm.

### The validator

`scripts/validate_dataset.py` (with `--partial` while work is in progress) checks the schema,
verbatim place names, stop order, that every tight leg has a real time, that labels point at
real stops and days, hotel keys and cities, file names, and the split rules (counts, Vienna
in test, variants in their parent's split). It prints counts, label totals and the version.

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
- **Memory:** the API idles around 60 MB.

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
   Langfuse needs several more services.

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

## 13. Known gaps and next steps

- Where demo runs are stored (section 4).
- The holiday calendar used for `PH` rules differs from Nager.Date (section 5).
- Nominatim's 1 request/second limit is per process; fine for one API container, but several
  processes calling it would need a shared limiter (e.g. in Postgres).
- Transit times are estimates; the travel-mode constants and pace limits are untuned
  guesses until the dev set exists.
- The frontend must show OSM attribution with a "fix the map" link (FOSSGIS policy).
- The dataset exists only on one Mac until it's backed up (gitignored by design).
- Gold stops have no OSM IDs yet; matching the agent's parsed stops to gold stops will
  need either those IDs or alignment by day, order and name (decided with the eval runner).
- Hotel red flags: safety and location matching is weak (F1 ~0.5 on held-out reviews), and
  `min_share` / `min_reviews` are untuned against the hotel labels.
- Next: collect and annotate the dataset (Vincent), then the agent. Order and status are in
  `PLAN.md`.

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
- **SSE (server-sent events):** a one-way stream from server to browser, planned for the live
  progress feed.
