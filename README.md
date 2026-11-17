# Trip Feasibility Checker

Paste an itinerary a chatbot wrote. An AI agent checks it against real opening hours,
public holidays, travel times and hotel reviews, shows the evidence and source for every
problem, and proposes fixes that are re-checked before you see them.

**Live demo:** [trip-feasibility.tail9b08fe.ts.net](https://trip-feasibility.tail9b08fe.ts.net/)
(sample itineraries: check one, or copy it and paste it to see the whole flow)

![Copying a chatbot-style London itinerary, pasting it, confirming the six stops it read, then the live check: two closed places confirmed by a web search with their sources, and fixes that passed the re-check](docs/images/demo.gif)

## Why I built this

For a trip with friends, I used an AI-generated itinerary that took in the spots we wanted
to see and the food places we wanted to go. Only after we'd booked accommodation in the
different cities did we realise that, on the days we'd be in town, a restaurant we really
wanted to go to would be closed. Nothing in the itinerary had told us.

## How it works

1. **Parse.** Claude turns the pasted text into stops; code geocodes each place in
   OpenStreetMap and never guesses coordinates. You confirm the stops.
2. **Check.** Six checks run in parallel as MCP tools in a LangGraph agent: opening hours,
   public holidays, travel time, daily load, hotel red flags (296K embedded reviews) and
   temporary closures (web search).
3. **Second opinion.** A separate web search looks up the same opening hours without seeing
   the checks. Where both find a problem it's marked **confirmed**.
4. **Fix.** Claude proposes fixes as operations (move, remove, replace a stop); code applies
   each one and re-runs every check. Only fixes that clear their problem are shown.

The rule behind it: **every fact has a cited source.** A model may search the web, but it
never states hours, holidays or travel times from memory, and code validates every claim.

More: [how it works and why](docs/ARCHITECTURE.md).

## Results

Measured on 40 hand-verified itineraries from ChatGPT, Claude and Gemini. 15 were used
while building; the other 25, including every Vienna itinerary, were held out and run once.

| On the held-out set (47 problems) | Found | Right |
|---|---|---|
| Plain LLM with web search (same model, same rules) | **70%** | **80%** |
| This system (checks + second opinion) | 51% | 44% |

**The plain LLM won.** The system had edged ahead on the development set, but that lead
didn't hold on new data. Its losses come from travel time and daily load, which rest on
constants I never tuned. What did hold up:

- **Agreement is a reliable signal.** When the checks and the independent web search both
  flagged a closed venue, they were right 7 of 8 times, the same 88% as during development.
- **No overfitting to the cities.** Vienna, never seen while building, scored as well as
  the other held-out itineraries.
- **Fixes:** 84% passed every re-check.
- **Prompt injection:** 36 of 36 planted attacks resisted.

The lesson: a well-prompted LLM with web search is a strong baseline, and a 15-itinerary
dev set is too small to tune against. What the structured pipeline adds isn't more
problems found but evidence: a cited source for every flag, a calibrated confidence from
agreement, and fixes that are verified, not trusted.

Full numbers: [EVALS.md](docs/EVALS.md).

## Built with

- **Backend:** Python 3.12, FastAPI, LangGraph, MCP, Anthropic SDK (Claude Haiku 5.5),
  Postgres 17 + pgvector, SQLAlchemy + Alembic. Live progress over server-sent events,
  rate limits, tracing and cost per run.
- **Frontend:** Next.js 16, TypeScript, Tailwind 4, shadcn/ui, with API types generated
  from the backend's OpenAPI schema.
- **Quality:** 439 backend tests (no test calls a real API), vitest, Playwright on the full
  Docker stack, a behaviour snapshot for refactors, and CI with linting, migration checks
  and secret scanning.
- **Public demo:** visitors can't trigger model calls. Every model reply the samples need
  is prepared once and stored, so the demo costs nothing per visit.

## Run it locally

Needs Docker and an Anthropic API key.

```sh
cp .env.example .env             # add ANTHROPIC_API_KEY
docker compose up --build        # Postgres, the API on :8020, the site on :3020
docker compose exec api python -m trip_feasibility.demo.warm   # prepare the samples (~$0.50)
open http://localhost:3020
```

The hotel check needs the review data (`backend/scripts/load_reviews.py`, from the Kaggle
dataset below), which isn't in the repo. Backend development: `cd backend && uv sync &&
uv run pytest`; frontend: `cd frontend && npm ci && npm run dev`. `e2e/run.sh` runs every
check before a commit. Settings and their defaults are in
[`config.py`](backend/src/trip_feasibility/config.py).

## Data sources

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via
[Nominatim](https://nominatim.org) and the [FOSSGIS routing servers](https://routing.openstreetmap.de).
Public holidays from [Nager.Date](https://date.nager.at). Official websites from
[Wikidata](https://www.wikidata.org). Hotel reviews from the
[515K Hotel Reviews dataset](https://www.kaggle.com/datasets/jiashenliu/515k-hotel-reviews-data-in-europe) (CC0).
Closures and the second opinion cite the web pages Anthropic's web search found.
