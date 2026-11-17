# How it works, and why

A traveller pastes an itinerary a chatbot wrote. The app turns it into stops, checks them
against real data, reports what will break with the evidence for each problem, and proposes
fixes that are re-checked before they're shown. This page is the short version: the parts,
the decisions that shaped them, and what's known not to work. The numbers are in
[`EVALS.md`](EVALS.md); the requirements in [`PRD.md`](PRD.md).

## The flow

```
pasted text
   │
   ▼
parse ─────────── clean (code) → extract stops (Claude, structured output)
   │              → normalise times (code) → geocode in OpenStreetMap (code)
   ▼
the traveller confirms the stops
   │
   ▼
agent (LangGraph) ── checks, in parallel, as MCP tools ──┐
   │                  opening hours      (OSM)            │
   │                  public holidays    (Nager.Date)     │
   │                  travel time        (OSRM routing)   ├─► violations, each with
   │                  daily load         (the same legs)  │   evidence and a source
   │                  hotel red flags    (296K reviews)   │
   │                  closures           (web search)     │
   │                                                      │
   └─ second opinion: a web search on opening hours, ─────┘
      blind to the checks; agreement = "confirmed"
   │
   ▼
fixes ─────────── Claude proposes operations → code applies them to a copy
                  → every check re-runs → only fixes that clear their problem
                    without adding a blocker are shown
```

Everything streams to the browser over server-sent events as it finishes, so the results
page fills in live.

## The parts

**Parse** (`backend/src/trip_feasibility/parse/`). Chatbot replies arrive with headings,
emoji, image links and booking checklists, so code strips the debris first. Claude Haiku
5.5 then extracts the stops into a fixed JSON schema (place name verbatim, day, times,
type), validated by Pydantic with one retry. Code normalises the times and geocodes each
place inside the city's bounding box; a name it can't match stays unresolved rather than
guessed. The model never produces coordinates.

**Checks** (`checks/`). Each is a plain async function that takes stops and returns a
structured `CheckResult`: violations (severity, stops, message, evidence, source), plus
`unknown` for stops it couldn't judge and `unavailable` if its data source is down. Missing
data is never a pass or a fail, so precision and coverage stay honest, and one failed
source never sinks a run. They're exposed as MCP tools, called in-process. Every external
lookup is cached in Postgres, and the public services (Nominatim, OSRM) are called at most
once a second under their usage policies.

- *Opening hours:* OSM's `opening_hours` tag for the date and time of the visit.
- *Public holidays:* Nager.Date, including regional ones. A holiday is a warning, never a
  blocker by itself; a real closure comes through opening hours.
- *Travel time and daily load:* walking, cycling and driving times from OSRM; transit and
  taxi times are estimated from driving times. A gap more than 10 minutes too short is a
  warning, more than 30 a blocker.
- *Hotel red flags:* 296K negative hotel reviews (Kaggle, CC0) embedded locally
  (bge-small, pgvector). A review counts toward a theme (noise, cleanliness, safety,
  location) only if it matches keywords *and* is close to the theme in embedding space;
  a red flag needs an unusually high share of such reviews for that hotel.
- *Closures:* a web search per place, the top pages fetched by our own guarded fetcher,
  and a tool-less model that extracts only dated closure periods. A finding counts as
  "official" only when the site is the place's website according to both OSM and Wikidata.

**Second opinion** (`agent/second_opinion.py`). Beside the checks, one Claude call with web
search looks up each venue's status on its date, without seeing what the checks found.
Code compares the two: both flag it → "confirmed"; only the search → "worth checking";
the checks flag it but the search found it open → "disputed". It can raise a warning at
most, never a blocker on one web page.

**Fixes** (`agent/fixes.py`). Claude proposes fixes as operations (`move`, `remove`,
`replace`, `add_transport_mode`), never a rewritten itinerary. Code applies each to a copy,
geocodes any new place, and re-runs every check. A fix is shown only if its target problem
is gone and no new blocker appeared; problems still unfixed get one retry, with the reasons
the first attempts failed.

**API and frontend**. FastAPI with runs as background tasks and an event log replayed to
every SSE subscriber. Next.js 16 (App Router) with shadcn/ui; the browser only ever calls
its own origin, and a proxy route forwards to the API. API types are generated from the
backend's OpenAPI schema, and CI fails if they drift.

**The public demo.** Anonymous visitors can check the sample itineraries, by button or by
pasting the text, at no cost to anyone: every model reply the samples need is prepared once
(`demo/warm.py`) and stored under a hash of the exact request. An anonymous request gets a
client that can only read those replies; anything else pasted is refused before any model
call.

## Key decisions

The full design log has 100 decisions; code comments cite them by number. These are the
ones that shaped the project, with what they cost.

| # | Decision | Why | What it cost |
|---|---|---|---|
| 92 | **Every fact has a cited source.** Models may search the web and judge fuzzy questions, but never state hours, holidays or travel times from memory. Code validates every claim (the URL is one the search returned, dates parse) and sets severity. | Much of what breaks a trip exists only as text on the web (the London Eye's winter maintenance isn't in OSM). Keeping every claim checkable keeps results and evals meaningful. | More validation code; a claim without a source is dropped, even when it's right. |
| 42 | **Configuration decides which checks run, not a model.** | Deterministic, cheaper, and no way for a model to skip a check silently. | Less "agentic", by design. |
| 5, 6 | **Missing data is `unknown`; a failed source is `unavailable`.** | Honest precision and coverage; partial results instead of crashed runs. | The UI has to explain partial results. |
| 49, 50 | **Fixes are operations, re-checked by code.** | Small, verifiable changes; a fix that breaks something else is caught, not shown. | Some good fixes are rejected because a check can't verify the new place. |
| 97 | **A blind second opinion on opening hours.** | On dev, the checks and a plain LLM found mostly the same problems and each missed different ones. Agreement between two independent methods is a confidence signal. | One more model call with web search per run. |
| 51, 62 | **Third-party text never reaches a prompt** (review excerpts, free-text OSM tags, fetched pages go only to a tool-less extractor). | Defends against prompt injection structurally rather than by prompt wording. | The fixer sees less context. |
| 74 | **Label problems, not stops.** The gold data is the problems in each itinerary in its own words, matched by check, day and place. | Labels don't change when the parser does, and a plain LLM can be scored on the same labels. | Fuzzy name matching, with uncertain matches decided by a human once. |
| 20, 22 | **A held-out city.** Vienna was never looked at while building; the test split was run once, at the end. | Measures generalisation, not tuning. | Fewer itineraries to develop on (15). |
| 100 | **Anonymous requests never reach the model.** | A public demo with zero cost per visitor and nothing to abuse. | Visitors can only try the samples. |
| 11 | **Nominatim for OSM data, not Overpass.** | Public Overpass servers were down or at capacity for hours; Nominatim answers ID lookups reliably. | Fewer tags per place; 1 request a second. |

## How the design changed

- **From labelling stops to labelling problems (Oct 2026).** Every new parsing rule changed
  the first dataset format. Labelling problems in the text's own words fixed that, and let
  a plain LLM be scored on the same labels.
- **From "code decides every fact" to "every fact has a source" (Nov 2026).** Annotating
  showed that structured data misses much of what breaks a trip. Models may now search, under
  the validation above.
- **The hybrid (Nov 2026).** The checks tied the LLM baseline on dev, with different
  errors, so an independent second opinion turned agreement into confidence.
- **The held-out run (16 Nov 2026).** The plain LLM won (details in [`EVALS.md`](EVALS.md)).
  Nothing has been tuned since; anything that is will be marked as tuned after seeing the
  test results.

## Evaluation

- **Dataset:** 40 itineraries written by ChatGPT, Claude and Gemini for Paris, London,
  Barcelona and Vienna, each naming one of 16 hotels. Labels were drafted by a model from
  another provider (GPT, with web search) and verified one by one against official sites
  and Google Maps. The truth is reality, never our own data sources. The dataset isn't
  public (it includes the chatbots' replies); `docs/annotation/` has the format and a
  made-up worked example.
- **Splits:** dev = 5 each from Paris, London and Barcelona; test = the other 15 plus all
  10 from Vienna.
- **Two modes:** the checks on hand-made gold stops (no parse errors), and end to end from
  the raw text. A prediction matches a label on check, day and place name.
- **Baseline:** Claude Haiku 5.5 with web search, given the same rules and the raw text.
- **Reproducible:** every run records the model, prompt versions, config and a dataset
  hash; model replies can be replayed so changes are compared on identical inputs.
- **Prompt injection:** 36 attacks hidden in pasted text, OSM tags, opening-hours comments,
  hotel reviews, web pages and search results, judged in code.

## Testing

439 backend tests (pytest against a real Postgres, every external API mocked), vitest for
the frontend, Playwright on the full Docker stack at desktop and phone width, and a
behaviour snapshot (the dev evaluation from cache) that must stay identical across
refactors. CI runs ruff, the tests, Alembic drift checks, a check that the frontend's API
types match the backend, and gitleaks.

## Known limits

- **Travel time and daily load are untuned.** The transit estimates and pace limits are
  constants that were never fitted; on the test set travel time raised 19 false alarms and
  daily load found nothing.
- **OpenStreetMap coverage:** many places have no opening hours in OSM, or stale ones.
- **Closures:** about 40% of pages refuse our fetch, and partial closures aren't reported.
- **Not built:** sign-in and the signed-in pages (saved itineraries, live checks of your
  own trips).
