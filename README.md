# Trip Feasibility Checker

Paste an itinerary that a chatbot wrote; an AI agent reports what will break (closed venues,
public holidays, travel times that don't fit, hotel red flags, overloaded days), shows the
evidence and its source for each problem, and proposes fixes that are re-checked before you
see them.

**Live demo:** not hosted yet (it runs locally with one command, below).

![A finished check: a bank holiday Monday in London, with two closed places, a holiday and two tight transfers, and three re-checked fixes](docs/images/results.png)

## Why I built this

For a trip with friends, I used an AI-generated itinerary that took in the spots we wanted
to see and the food places we wanted to go. Only after we'd booked accommodation in the
different cities did we realise that, on the days we'd be in town, a restaurant we really
wanted to go to would be closed.

Nothing in the itinerary had told us. This project is so that doesn't happen again: check a
plan against real opening hours, holidays and travel times before you book around it.

## What it does

1. **Parse.** Claude (Haiku 5.5, structured output) turns the pasted reply into stops; code
   normalises times and geocodes each place in OpenStreetMap, never guessing coordinates.
2. **Check.** Six checks run in parallel as MCP tools in a LangGraph graph: opening hours
   (OSM), public holidays (Nager.Date), travel time (OSRM routing), daily load, hotel red
   flags (296K embedded hotel reviews, pgvector) and temporary closures (web search, each
   finding citing its page).
3. **Second opinion.** Beside the checks, a web-search pass looks up the same opening hours
   independently, without seeing the checks' results. Where both find a problem it's
   **confirmed**; where only one does, it's shown as **worth checking**.
4. **Fix.** Claude proposes fixes as operations (move, remove, replace a stop); code applies
   each one to a copy of the itinerary and re-runs every check. Only fixes that remove their
   problem without adding a blocker are shown.

The rule behind all of it: **every fact has a cited source.** A model may judge fuzzy
questions and search the web, but it never states hours, holidays or travel times from
memory, and code validates every claim (a search-result URL, dates that parse) and sets the
severity. How it works and why: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Results so far

Measured against a hand-verified dataset of 40 itineraries written by ChatGPT, Claude and
Gemini for Paris, London, Barcelona and Vienna. Labels were drafted by a model from another
provider (GPT, with web search) and checked one by one against official sites and Google
Maps. 15 itineraries are for development; the other 25, including every Vienna one, are
held out and haven't been run yet. The numbers below are **development-set** results, the
set the design was tuned on; the held-out results will go here, whatever they are.

End to end from the raw text, on the 30 labelled problems:

| | Problems found | Flags that were right |
|---|---|---|
| Plain LLM with web search (Claude Haiku 5.5, given the same rules) | 18 of 30 (60%) | 78% |
| Our checks alone | 16 of 30 (53%) | 80% |
| **Checks + second opinion (the hybrid)** | **19 of 30 (63%)** | 70% |
| Checks + second opinion, on hand-parsed stops (the ceiling) | 22 of 30 (73%) | 79% |

- **Agreement is the useful signal:** problems that the checks and the second opinion both
  flagged were right 15 of 17 times; problems only the second opinion flagged, about half
  the time. That's why the results page labels them differently.
- **What I learned:** structured data alone tied a well-prompted LLM; each missed problems the
  other found, and almost never flagged the same thing wrongly. Combining them beat both on
  recall. The gaps left are what no source states yet (a 2027 maintenance closure not
  announced) and judgement calls (is this day too much?), where an LLM review was tried and
  [didn't beat simple counts](docs/EVALS.md), so it's off.
- **Fixes:** 16 of 18 proposed fixes passed every re-check, and 18 of 19 problems got one.
  6 of the 16 change a stop that a check couldn't verify (no hours in OpenStreetMap), so the
  page marks them "not verified".
- **Prompt injection:** 36 attacks of our own hidden in pasted text, OpenStreetMap tags,
  opening-hours comments, hotel reviews, web pages and search results: all 36 resisted with
  the shipped defences. The defences that matter are structural (no free text from third
  parties in a prompt, tool-less extractors, code choosing sources and severity); one
  attack (a vandalised map link to a fake official site) got through until official sites
  had to be confirmed by Wikidata.

Every run, with what changed in between, is in [`docs/EVALS.md`](docs/EVALS.md).

## How it was built

- **Backend:** Python 3.12, FastAPI, LangGraph, the MCP Python SDK (checks as tools),
  Pydantic, SQLAlchemy 2.0 (async) + Alembic, Postgres 17 + pgvector, the Anthropic SDK.
  Live progress over server-sent events; per-user and per-IP rate limits; Langfuse tracing
  and cost per run.
- **Frontend:** Next.js 16 (App Router), TypeScript, Tailwind 4, shadcn/ui; API types
  generated from the backend's OpenAPI schema; the browser only talks to its own origin.
- **Evaluation:** a versioned dataset with a held-out city, scoring that matches predicted
  problems to labels by check, day and place, an LLM-only baseline, a prompt-injection
  suite, and replayable model calls so changes can be compared on identical inputs.
- **Quality gates:** 428 backend tests (no test calls a real API), vitest, Playwright on the
  full Docker stack at desktop and phone width, a behaviour snapshot that must stay
  identical across refactors, CI with ruff, Alembic drift checks and gitleaks.

## What changed and why

- **Labels first, not stops (Oct 2026).** The first gold format labelled stops; every new
  parsing rule changed the dataset. Switching to labelling *problems* in the text's own
  words made the labels independent of how anyone splits an itinerary, and let a plain LLM
  be scored on the same labels.
- **From "code decides every fact" to "every fact has a source" (Nov 2026).** Annotating
  showed that much of what breaks a trip exists only as text on the web (the London Eye's
  winter maintenance shutdown isn't in OpenStreetMap). Models may now search, but every
  claim must cite a page the search returned, and code decides what's shown.
- **The hybrid (Nov 2026).** The checks tied the LLM baseline, but their errors were
  different. An independent second opinion turns agreement into confidence.

The full decision log, with what each choice cost, is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#12-decision-log). Requirements:
[`docs/PRD.md`](docs/PRD.md).

## Run locally

Needs Docker.

```sh
cp .env.example .env             # add ANTHROPIC_API_KEY for parsing, fixes and web search
docker compose up --build        # Postgres 17 + pgvector, the API on :8020, the site on :3020
curl localhost:8020/health       # {"status":"ok","database":"ok"}
open http://localhost:3020/demo  # check a sample itinerary
```

The API container applies database migrations on start. Without a key the demo still runs
every structured check; fixes and web lookups need it. The hotel check needs the review data
(`scripts/load_reviews.py`, from the Kaggle dataset), which isn't in the repo.

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

Before committing, `e2e/run.sh` (from the repo root) runs everything: lint, migrations and
tests, a behaviour snapshot of the evaluation (record it once with
`uv run python scripts/snapshot.py --save`), the frontend checks, then the full stack in
Docker with Playwright clicking through a live demo check at desktop and phone width.

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
| `WIKIDATA_BASE_URL` | `https://www.wikidata.org` | Official websites, to confirm OSM's (closures) |
| `RATE_LIMIT_COOLDOWN_S` | `60` | Pause after a service answers 429/403/503 |
| `HTTP_USER_AGENT` | `trip-feasibility/0.1 (+repo URL)` | Identifies the app to OSM services |
| `ANTHROPIC_API_KEY` | none | Claude API key for parse step 1 (secret; `.env` only) |
| `EXTRACTION_MODEL` | `claude-haiku-5-5` | Model for parse step 1 |
| `EXTRACTION_EFFORT` | `medium` | Effort level for parse step 1 |
| `FIX_MODEL` | `claude-haiku-5-5` | Claude model that proposes fixes |
| `FIX_EFFORT` | `medium` | Effort level for fix proposals |
| `REVIEW_MODEL` | `claude-haiku-5-5` | Claude model for the day review (off by default) |
| `REVIEW_EFFORT` | `medium` | Effort level for the day review |
| `SECOND_OPINION_MODEL` | `claude-haiku-5-5` | Claude model for the second opinion (off by default) |
| `SECOND_OPINION_MAX_SEARCHES` | `10` | Web searches the second opinion may make per check |
| `TEST_DATABASE_URL` | `DATABASE_URL` + `_test` | Database the tests use (created if missing; must end in `_test`) |

Secrets go in `.env` (gitignored), never in the repo. CI runs gitleaks on every push.

## Data sources

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via
[Nominatim](https://nominatim.org) and the [FOSSGIS routing servers](https://routing.openstreetmap.de).
Public holidays from [Nager.Date](https://date.nager.at). Official websites from
[Wikidata](https://www.wikidata.org) (CC0). Closures and the second opinion from web pages
found by Anthropic's web search, each cited. Hotel reviews from the
[515K Hotel Reviews dataset](https://www.kaggle.com/datasets/jiashenliu/515k-hotel-reviews-data-in-europe) (CC0).
