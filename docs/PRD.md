# Trip Feasibility Checker: PRD

Oct 23, 2026 · @Vincent

## Overview

The Trip Feasibility Checker is a web app where a traveller pastes an itinerary and an AI agent reports what will break (closed venues, public holidays, travel times that don't fit, hotel red flags) and proposes fixes. It ships in three tiers between Oct 26, 2026 and early January 2027, and every result it claims is backed by a logged evaluation.

**Problem.** Itineraries written by people or by chatbots often look plausible but fail on contact with reality: a museum closed on Mondays, a 40-minute transfer squeezed into 15, a hotel with repeated noise complaints. Travellers find out on the day.

**Solution.** An agent parses the itinerary into structured stops, checks each one with real data tools (OpenStreetMap, routing, public holidays, hotel reviews), and returns a list of violations with evidence and a suggested fix. Users tune the checks to their own travel style and keep a history of their trips.

### Goals

1. Catch the four violation types above on real itineraries, with precision and recall measured on a hand-annotated test set.
2. Let users set travel preferences and choose which checks run and how strict they are.
3. Let users sign in, save itineraries and see past check results.
4. Resist prompt injection hidden in third-party data such as hotel reviews.
5. Keep cost per check low enough to run the full eval suite on every pull request.

### Non-goals

- Booking, payments or live prices.
- Generating itineraries from scratch (the app checks and repairs; it doesn't plan).
- Mobile apps (the web app is responsive, nothing more).
- Real-time data such as live traffic or weather.
- Multi-user collaboration on one itinerary.

## Users and user stories

The primary user is an independent traveller planning a 2–10 day city trip, often with a chatbot-drafted itinerary. The secondary audience is recruiters and interviewers, who need a working demo in under two minutes.

| ID | As a… | I want to… | So that… | Tier |
| --- | --- | --- | --- | --- |
| US-1 | traveller | paste an itinerary in plain text | I don't have to reformat it | 1 |
| US-2 | traveller | see each problem with its evidence and source | I can trust or dismiss it | 1 |
| US-3 | traveller | get a concrete fix for each problem | I can repair the plan quickly | 1 |
| US-4 | traveller | set my pace, walking limit and budget | the checks match how I travel | 1 |
| US-5 | traveller | turn checks on or off and set how strict they are | I only see problems I care about | 1 |
| US-6 | traveller | watch the check's progress live | a 30-second wait doesn't feel broken | 1 |
| US-7 | traveller | sign in and save itineraries | I can come back to them | 1 |
| US-8 | traveller | re-run a saved itinerary and compare results | I can see if my edits fixed things | 3 |
| US-9 | recruiter | try a sample itinerary without signing up | I can judge the project fast | 1 |

Acceptance criteria for each story live with its functional requirement in the next section.

## Functional requirements

Requirements use MUST for Tier 1 scope and SHOULD for later tiers. Each one is testable.

### FR-1 Itinerary input and parsing

- MUST accept plain text up to 5,000 characters, plus a city and trip start date.
- MUST parse it into structured stops: day, start time, end time, place name, and type (venue, transfer, hotel, meal).
- MUST resolve each place to OpenStreetMap coordinates, and flag any it can't resolve instead of guessing.
- MUST show the parsed stops to the user, who can correct them before the check runs.

Parsing runs as a five-step pipeline. Only step 1 uses an LLM; in Tier 2, the fine-tuned model replaces the API model in that step alone.

1. **LLM extraction.** One API call returns stops matching a fixed schema with the fields listed above, enforced with the provider's structured output or tool calling.
2. **Validation.** Pydantic validates the JSON. On failure, retry once with the validation error; on a second failure, report a parse error instead of guessing.
3. **Normalisation.** Code converts day numbers to dates from the trip start date, and maps morning, afternoon and evening to fixed default time windows.
4. **Geocoding.** Code looks up each place name in OpenStreetMap for coordinates and an OSM ID. The LLM never produces coordinates.
5. **User review.** The frontend shows the stops in an editable table; the check starts only after the user confirms.

Schema rules:

- Optional fields (times, time of day) stay null when the text doesn't state them, so the model isn't pushed to guess.
- `place_name` is copied exactly as written; correcting names is the geocoder's job, where success or failure is visible.

Parse accuracy (see Evaluation) measures step 1 only, so the API model and the Tier 2 model are compared on the same task.

### FR-2 Checks

Each check is an MCP tool that returns violations as structured JSON, never free text.

| Check | Data source | Flags when |
| --- | --- | --- |
| Opening hours | OSM Overpass | A stop falls outside the venue's opening hours, or the venue no longer exists |
| Public holidays | Nager.Date | A stop falls on a holiday that closes or crowds the venue |
| Travel time | OSRM or OpenRouteService | The gap between two stops is shorter than routed travel time plus a buffer |
| Hotel red flags | Kaggle reviews in pgvector | Retrieved reviews show repeated issues (noise, cleanliness, safety, location) |
| Daily load | Parsed stops + preferences | A day exceeds the user's pace or walking limit |

Every violation MUST include: check name, severity (info, warning, blocker), affected stops, evidence (the data that triggered it) and source.

### FR-3 Fixes

- MUST propose at least one fix per warning or blocker: reorder stops, move a stop to another day, swap a transfer mode, or replace a venue.
- MUST re-run the affected checks on each proposed fix before showing it, so a fix never creates a new blocker.
- MUST let the user accept or reject each fix; accepted fixes produce a new itinerary version.

### FR-4 Travel preferences

- MUST store per user: pace (relaxed, moderate, packed), maximum walking per day in km, daily budget with currency, and preferred transport modes.
- MUST pass preferences into the daily-load and travel-time checks and into fix generation.
- MUST apply sensible defaults for users who never set them.

### FR-5 Check configuration

- MUST let users turn each check on or off.
- MUST let users set strictness per check. Example: travel-time buffer of 0, 10 or 20 minutes; hotel flags shown only when an issue appears in 3, 5 or 10+ reviews.
- MUST save the configuration with each check run, so results stay reproducible.

### FR-6 Accounts, saved itineraries and history

- MUST support sign-in with GitHub or Google OAuth and email magic links.
- MUST let users save, rename and delete itineraries.
- MUST keep every check run for a saved itinerary, with its versions, config and results.
- SHOULD (Tier 3) show a diff between two runs of the same itinerary.
- MUST offer a demo mode with sample itineraries that needs no sign-in and saves nothing.
- MUST let users delete their account and all their data.

## Non-functional requirements

These are the targets the system is held to. Each is measured from Langfuse traces or test runs, not estimated.

| Area | Requirement | Target |
| --- | --- | --- |
| Latency | Full check of a 3-day itinerary, p95 | Under 60 s, first progress event under 2 s |
| Cost | LLM spend per itinerary check | Under $0.01, logged per run |
| Cost | Monthly LLM spend | Hard cap set at the provider before the first call |
| Reliability | External API down or rate-limited | Check reports "unavailable" for that tool; the run still finishes |
| Reliability | Repeated lookups | Cached in Postgres with a time-to-live; eval reruns make no new external calls |
| Security | Prompt injection in reviews or venue data | Tool output treated as data; injection suite pass rate reported in the README |
| Security | Secrets | Never in the repo; loaded from environment variables; checked by a secret scanner in CI |
| Security | Auth | Every API route except demo and health requires a valid session token |
| Privacy | User data | Itineraries visible only to their owner; full account deletion supported |
| Abuse | Rate limiting | Per-user and per-IP limits on check runs (e.g. 20 per hour) |
| Reproducibility | Eval runs | Model ID, prompt version, config and dataset version stored with every result |
| Accessibility | Frontend | Keyboard navigable, WCAG AA colour contrast, works at 375 px width |

## Architecture and tech stack

The system is two services plus a database: a Next.js frontend and a FastAPI backend that runs the agent and its MCP tools, sharing one Postgres database.

> _[Embedded content not included: system architecture diagram, 8 components]_

The browser never talks to an LLM or a data API directly. Every check flows through the backend, so auth, caching, cost limits and tracing happen in one place.

**Stack.** Backend: Python 3.12, uv, FastAPI, Pydantic, LangGraph, MCP Python SDK, SQLAlchemy 2.0, Alembic, pytest, ruff. Frontend: Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui, Auth.js, openapi-typescript. Infra: Docker Compose locally, GitHub Actions for CI, Langfuse for tracing, MLflow for Tier 2 training runs.

### Architecture Decision Records

| ADR | Decision | Alternatives considered | Why |
| --- | --- | --- | --- |
| ADR-1 | Next.js + TypeScript frontend | Streamlit, HTMX | A presentable, customisable UI and full-stack practice; logic stays in the API, so the UI is replaceable |
| ADR-2 | Postgres 17 + pgvector for all data | SQLite; Postgres + Qdrant | Hotel filter and vector search in one SQL query; 515K vectors is well within pgvector's range; one service to run |
| ADR-3 | LangGraph for the agent | A hand-written loop; other agent frameworks | Explicit state graph, checkpoints and step-level streaming for the progress feed |
| ADR-4 | Checks as an MCP tool server | Plain Python functions | A standard tool protocol; each tool is testable on its own and usable from other MCP clients |
| ADR-5 | Auth.js in Next.js; FastAPI verifies the session JWT | Clerk, Supabase Auth; auth built in FastAPI | Free, no vendor lock-in, and you learn how sessions work; cost: more security code to test |
| ADR-6 | LLM access through one provider-agnostic layer | Calling one vendor SDK directly | Switching from a free tier to a paid model is a config change |
| ADR-7 | FastAPI background tasks for check runs | Celery; a Redis queue | Simplest option that works at this scale; revisit only if runs pile up |

## Data model

All data lives in one Postgres 17 database with the pgvector extension, managed with SQLAlchemy 2.0 and Alembic migrations. Users own itineraries; each itinerary has versions; each version can have many check runs.

| Table | Key columns | Notes |
| --- | --- | --- |
| `users` | id, email, name, auth_provider, created_at | Created on first sign-in |
| `preferences` | user_id, pace, max_walk_km, daily_budget, currency, transport_modes[] | One row per user |
| `check_configs` | user_id, check_name, enabled, strictness (JSONB) | One row per user per check |
| `itineraries` | id, user_id, title, city, start_date, created_at | Deleted with the user |
| `itinerary_versions` | id, itinerary_id, version_no, raw_text, parsed_stops (JSONB), source (user, fix) | A new row per edit or accepted fix |
| `check_runs` | id, version_id, status, config_snapshot (JSONB), model_id, prompt_version, cost_usd, latency_ms, trace_id | trace_id links to Langfuse |
| `violations` | id, run_id, check_name, severity, stop_ids[], evidence (JSONB), source | Many per run |
| `fixes` | id, violation_id, description, patched_stops (JSONB), status (proposed, accepted, rejected) | Re-checked before display |
| `hotel_reviews` | id, hotel_id, hotel_name, review_text, sentiment, embedding vector(1024) | HNSW index on embedding, B-tree on hotel_id |
| `api_cache` | key, source, response (JSONB), expires_at | Overpass, routing and holiday lookups |
| `eval_runs` | id, dataset_version, model_id, prompt_version, metrics (JSONB), created_at | Written by the eval harness and CI |

The embedding dimension (1024) depends on the embedding model chosen; it's fixed once the model is picked. Raw itineraries and tool outputs go in JSONB so the schema doesn't change every time a tool's output does.

## API design

FastAPI owns all logic and exposes a versioned REST API under `/api/v1`. Its OpenAPI schema is the contract with the frontend: `openapi-typescript` generates the frontend's types from it on every build.

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| GET | `/health` | Liveness and database check | None |
| POST | `/itineraries/parse` | Parse raw text into stops (no save) | Optional |
| POST | `/itineraries` | Save a new itinerary | Required |
| GET | `/itineraries` | List the user's itineraries | Required |
| GET | `/itineraries/{id}` | One itinerary with its versions | Required |
| PATCH / DELETE | `/itineraries/{id}` | Rename or delete | Required |
| POST | `/itineraries/{id}/versions` | Save edited stops as a new version | Required |
| POST | `/checks` | Start a check run; returns `run_id` | Optional (demo) |
| GET | `/checks/{run_id}/events` | Server-sent events: progress, violations, fixes | Optional (demo) |
| GET | `/checks/{run_id}` | Final results of a run | Optional (demo) |
| POST | `/fixes/{id}/accept` | Accept a fix; creates a new version | Required |
| GET / PUT | `/me/preferences` | Read or update travel preferences | Required |
| GET / PUT | `/me/check-config` | Read or update check settings | Required |
| DELETE | `/me` | Delete account and all data | Required |

Conventions:

- Errors follow one shape: `{"error": {"code": "...", "message": "..."}}`, with proper HTTP status codes.
- Request and response bodies are Pydantic models, so validation and docs come from one definition.
- SSE events carry a `type` field: `step_started`, `tool_result`, `violation`, `fix`, `done` or `error`.
- Long work runs in a background task, so a dropped browser connection doesn't kill the run.

## Frontend pages and UX

The frontend is Next.js (App Router) with TypeScript, Tailwind CSS and shadcn/ui components, so you own and can restyle every component. It renders UI and calls FastAPI; it holds no agent logic.

| Route | Page | Key elements | Tier |
| --- | --- | --- | --- |
| `/` | Landing | One-line pitch, "Try a sample" button, sign-in | 1 |
| `/demo` | Demo check | Sample itineraries, full check flow, nothing saved | 1 |
| `/check/new` | New check | Paste box, city, start date, parsed-stops review table | 1 |
| `/check/[runId]` | Results | Live progress feed, day-by-day timeline with violations marked, fix cards with accept/reject | 1 |
| `/trips` | Saved itineraries | List with last result summary, rename, delete | 1 |
| `/trips/[id]` | Itinerary history | Versions and runs; diff between two runs | 1 (diff in 3) |
| `/settings/preferences` | Travel preferences | Pace, walking limit, budget, transport modes | 1 |
| `/settings/checks` | Check settings | Toggle and strictness control per check | 1 |
| `/settings/account` | Account | Sign-out, delete account | 1 |

UX principles:

- **Evidence first.** Every violation card shows why it fired and links to its source.
- **Progress, not spinners.** The results page fills in as SSE events arrive.
- **Fixes are proposals.** Nothing changes the itinerary until the user accepts.
- **A map is optional.** A day timeline carries the core value; a Leaflet map with OSM tiles is a Tier 3 polish item.

Tier 1 aims for functional and clean; Tier 3 adds visual polish, dark mode and the map.

## Evaluation and success metrics

The project succeeds when every metric below is measured on a fixed, versioned dataset with one pinned model, and the numbers are reported honestly in the README, whatever they turn out to be. No target is set before the first baseline run; targets are set from it.

**Dataset.** About 30 itineraries generated with ChatGPT, Claude and Gemini, each hand-annotated with its true violations. It is split into a dev set for iterating and a held-out test set touched only for reported results.

| Metric | What it measures | How |
| --- | --- | --- |
| Violation precision | Share of flagged problems that are real | Compare to annotations, per check |
| Violation recall | Share of real problems caught | Compare to annotations, per check |
| Fix validity | Share of fixes that pass all checks when re-run | Automatic re-check |
| Fix quality | Whether a fix is sensible for the traveller | LLM-as-judge on a 1–5 rubric, calibrated against your own ratings on 20+ fixes |
| Parse accuracy | Stops correctly extracted | Field-level match against annotated stops |
| Injection resistance | Share of malicious reviews that don't change behaviour | Injection suite, with and without defences |
| Cost and latency | Spend and p95 time per check | Langfuse traces |

Rules:

- The judge model comes from a different provider than the agent model.
- Every eval run writes a row to `eval_runs` with its model ID, prompt version and dataset version.
- In Tier 3, CI runs a 10-itinerary regression subset on each pull request and fails it if precision or recall drops more than a set margin.

## Milestones

The project runs in three tiers from Oct 26, 2026 to early January 2027, and a tier only closes when its results are logged.

> _[Embedded content not included: roadmap, 3 tiers, 3 gates]_

Suggested Tier 1 build order, backend first so the frontend always has a working API to call:

1. **Week of Oct 26:** repo, Docker Compose, Postgres with pgvector, CI running lint and tests; collect and annotate itineraries.
2. **Week of Nov 2:** MCP tools with caching and unit tests; load hotel reviews and embeddings.
3. **Week of Nov 9:** LangGraph agent for parse and check, run from a CLI; first baseline eval on the dev set.
4. **Week of Nov 16:** fixes with re-checks; FastAPI routes, SSE and auth; judge calibration.
5. **Week of Nov 23 to Nov 30:** Next.js pages, preferences, check settings and saved trips; injection suite; final test-set run on the pinned model.

Adjust these weeks to match the dates in the original build plan.

## Risks and open questions

The biggest risk is scope: accounts, settings and a Next.js frontend add real work to a Tier 1 that was already full.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Tier 1 overruns Nov 30 | Delays Tier 2 and the resume entry | Build backend and evals first; frontend pages only once the API works; trip diff and polish move to Tier 3 |
| OSM opening hours missing or stale | Opening-hours recall looks worse than the agent really is | Report "unknown hours" separately from violations; measure coverage |
| Free LLM tiers rate-limit eval loops | Slow iteration | Cache LLM responses in dev; switch to a pinned paid model for reported runs |
| Public OSRM demo server is slow or down | Travel-time check fails | Fall back to OpenRouteService; cache routes |
| Small dataset (about 30 itineraries) | Wide confidence intervals | Report per-check counts alongside percentages |
| Auth adds security surface | Data leaks between users | Ownership check on every query, plus tests that try to read another user's data |

Open questions:

- [ ] Which embedding model, and therefore which vector dimension?
- [ ] Which agent model and which judge model for the reported runs?
- [ ] Which cities does the test set cover? Fewer cities means better cache hits; more means a broader claim.
- [ ] Is demo mode limited to sample itineraries only, or does it allow pasted text with a rate limit?
- [ ] Is the repo public from day one?
