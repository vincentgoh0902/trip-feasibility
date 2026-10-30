# Plan and progress

Requirements: `docs/PRD.md`. Dates there are a loose reference only.

## Tier 1

- [x] Repo, Docker Compose, Postgres 17 + pgvector, Alembic schema (all tables except `hotel_reviews`)
- [x] FastAPI skeleton: `/health` with DB check, single error shape
- [x] CI: ruff, pytest against Postgres, `alembic check`, gitleaks
- [x] Tests use a separate `_test` database (guarded), so they never wipe dev data
- [x] Test-set cities and hotels picked (`docs/TEST_CITIES.md`)
- [x] Annotation format (`dataset/`), validator (`scripts/validate_dataset.py`), review
      sampler, guide (`docs/ANNOTATION.md`), template and worked example (`docs/annotation/`)
- [x] **Vincent: collect** 40 itineraries from ChatGPT, Claude and Gemini (10 per city), then
      assign splits at random (ANNOTATION.md steps 1–2). Done 2026-10-24: texts saved, YAML
      skeletons written (all default preferences), splits drawn with seed 39991414. Dev:
      paris 02 05 07 08 09; london 04 05 08 09 10; barcelona 02 06 07 08 10.
- [x] **Vincent: label the 16 hotels** in `data/dataset/hotels.yaml` (step 3). Done 2026-10-25.
- [ ] **Vincent: annotate** the itineraries (step 4); must be done before the first baseline
      eval **← now**
- [ ] **Vincent: back up** `data/dataset/` (gitignored; private repo or other copy)
- [x] Check contract (`CheckResult`), Postgres response cache, MCP server (SDK 2.x)
- [x] Public holidays tool (Nager.Date)
- [x] Opening hours tool (OSM tags via Nominatim `/lookup`, `opening-hours-py`)
- [x] Switched from Overpass to Nominatim; 1 req/s throttle and 60 s back-off on 429/403/503
- [x] Travel time tool (FOSSGIS OSRM table service; transit estimated)
- [x] Daily load tool (pace limits, walking distance)
- [x] Eval mode: read the cache regardless of expiry, so eval reruns make no external calls
- [x] `hotels` + `hotel_reviews` tables; 296K negative reviews from the 4 dataset cities
      embedded with bge-small-en-v1.5 (`scripts/load_reviews.py`, Mac only)
- [x] Hotel red-flags tool: keyword + embedding matching per theme, per-theme share
      thresholds from base rates over non-dataset hotels (`checks/hotel_red_flags.py`)
- [x] Parse steps 2–4: extraction schema + validation, normalisation, geocoding (Nominatim
      search inside the city's bounding box, 60 km fallback, unresolved otherwise)
- [x] Parse step 1: Claude Haiku 5.5 extraction, structured output, one validation retry
      (`parse/extract.py`)
- [ ] **Vincent: add `ANTHROPIC_API_KEY`** to the repo-root `.env`, after setting a monthly
      spend cap in the Anthropic Console; then `uv run python scripts/smoke_parse.py`
- [x] LangGraph check graph (enabled checks in parallel via in-process MCP), parse pipeline
      end to end, CLI (`scripts/check_itinerary.py`)
- [x] Eval runner (`scripts/run_eval.py`): gold-stop and parsed modes, alignment, parse
      accuracy, eval_runs rows; checked live on the example annotation
- [ ] **Violations-first evaluation** (decided 2026-10-27, ARCHITECTURE decision 74):
  - [x] 1. Stop rules frozen in ANNOTATION.md: dev only, for parser debugging
  - [x] 2. Gold format: violations by check + day + place/time as written, severity,
        evidence, source, confidence; `unknowns`, `other_issues`, `drafted_by`,
        `verified_by_human`, `hotel`. Validator, layout, scoring (place-name matching) and
        eval runner updated; travel_time labelled directly, hotels from hotels.yaml
  - [x] 3. Converted paris-02, paris-05, barcelona-02 (all `violations: []`, so no labels
        to carry over; `verified_by_human: false`); `hotel` set on the 4 dev files whose
        prompt names one. barcelona-06 skipped: invalid YAML (a stray leg line)
  - [x] 4. Drafter (`scripts/draft_gold.py`): GPT (gpt-6-sol, high) via the Codex CLI with
        web search, behind a model-agnostic interface (`evals/search_llm.py`); dev only,
        refuses Anthropic; one retry for invalid entries. Drafted the 11 dev files with no
        gold yet (2026-10-27); Vincent checks each and sets `verified_by_human: true`
  - [x] 5. LLM-only baseline (`scripts/run_baseline.py`): Claude Haiku 5.5 + web search via
        headless Claude Code (no API key), stripped to a plain model; same inputs and
        check rules as the drafter (`baseline-v1`); outputs cached. Checked live on
        paris-08. Run the rest once preferences are final (they're in the cache key).
        2026-10-29: `draft-v2` / `baseline-v2` carry the travel-time rules of decisions
        76-77; london-08 and london-09 re-drafted with them; baseline re-run on dev.
        2026-10-30: `draft-v3` / `baseline-v3` for decision 78 (travel-time bands); the
        baseline cache misses on the new version, so rerun it before step 7
  - [x] 6. Matcher: names scoring 0.4–0.9 go to Vincent once (`scripts/match_decisions.py
        --ask`), saved in `data/evals/match_decisions.yaml` and reused by every run and
        system; undecided pairs make a score provisional (2026-10-29). The 6 verified dev
        files' baseline outputs raised no uncertain pairs
  - [ ] 7. Baseline vs our system (parsed mode) on the first 5 dev files Vincent verifies:
        precision and recall per check
- [ ] First baseline eval on the dev set (needs the annotated dev set; parsed mode needs the key)
- [x] Langfuse tracing (optional, no traveller content by default), cost per run and per
      parse, `check_runs` rows with violations and fixes (`tracing.py`, `pricing.py`, `runs.py`)
- [ ] **Vincent: Langfuse Cloud** project keys in `.env` (optional; tracing stays off without)
- [x] Fixes with re-checks (`agent/fixes.py`): Claude proposes operations, code applies and
      re-runs every check, one retry; fix validity in `run_eval.py --fixes`. Untested live
      (needs the key)
- [x] FastAPI routes (PRD table), SSE live feed with replay, sign-in via a per-call token
      signed by the Next.js server, demo mode (samples, in memory), rate limits (`api/`,
      `auth.py`, `runs.py`, `ratelimit.py`); checked live on the demo sample
- [ ] Judge calibration (20+ hand-rated fixes)
- [x] Frontend slice 1: Next.js 16 + Tailwind + shadcn/ui in `frontend/`, branding-kit tokens,
      API proxy route, types generated from `openapi.json`, landing, `/demo`, live results page
      (`/check/[runId]`: progress, day timeline, evidence, fixes, OSM attribution); frontend CI
      job. Checked in the browser on the demo sample, including 375 px
- [x] Restyle to match the hero illustration: palette sampled from `hero.png`, side-face cards
      and pressable buttons, two-column hero with the cut-out `hero.webp` floating, Nunito
      headings, check icons, teal wave dividers, applied to every page (2026-10-26; decision 67)
- [ ] Frontend slice 2: Auth.js sign-in (GitHub, Google, email link) + per-call API token in
      the proxy; `/check/new` (paste, parsed-stops review table), fix accept/reject
- [ ] **Vincent: OAuth apps** (GitHub and Google client IDs/secrets for localhost) and
      `API_JWT_SECRET` in `.env`, before slice 2 can be tried
- [ ] Frontend slice 3: `/trips`, `/trips/[id]`, `/settings/*` (preferences, check settings,
      account deletion)
- [x] Injection suite: 4 vectors × 6 attacks, with and without defences, judged in code
      (`evals/injection.py`); closed a gap it exposed (free-text OSM tags in fix evidence)
- [ ] Run the injection suite live (needs the key) and put the numbers in the README
- [ ] Injected variants of dataset itineraries (`parent_id`), after annotation
- [ ] Final test-set run on the pinned model; results in README

## Tier 1 plan from here (decided 2026-10-29)

Annotation showed that much of what breaks a real itinerary is fuzzy (vague stops,
unrealistic days, "optional" or "X or Y" slots) or only exists as text on the web (the
London Eye's annual maintenance shutdown). So the core rule changes, and two LLM parts are
added. Order: matcher and the 5-itinerary side-by-side run first (a baseline number before
the design changes), then 1 → 2 → 4 → 3 → 5.

1. **New core rule** (replaces "the LLM proposes, code decides"): every fact has a cited
   source; structured sources (OSM, Nager.Date, routing, hotel reviews) are preferred and
   always run; an LLM may judge fuzzy questions and search the web for facts structured
   data doesn't have, but every claim carries a source URL and a confidence, and it never
   states hours, holidays or travel times from memory. Web page text reaches only the
   tool-less extractor, never the agent, review or fix prompt. In CLAUDE.md, ARCHITECTURE
   (decision with the London Eye example) and the PRD.
2. **Closures check** (temporary closures, special or seasonal hours, events), on located
   venues and named meals, trip dates only, at most 12 places per run (the rest "not
   checked"), on by default:
   - step A: Haiku 5.5 with Anthropic web search, `max_uses: 1` per place, returns candidate
     URLs only;
   - our own guarded fetch (https only, no private addresses, size and time limits, our
     User-Agent, HTML only), not Anthropic's web fetch;
   - step B: Haiku 5.5, no tools, structured output: status (closed | altered_hours | open
     | unknown), affected dates, source URL; code validates (dates parse, URL from step A);
   - confidence "official" when the domain matches the OSM `website` tag, else "web";
     web-only findings are "worth checking", never blockers;
   - findings (not page text) cached per venue and month, shared across users; eval mode
     replays them; frozen for reported runs; fix re-checks use cached findings only (a
     replacement venue is "not verified");
   - results stream last over SSE; the UI shows "still checking closures…" meanwhile.
4. **Injection suite** gains web pages: hidden instructions, a fake closure notice,
   "ignore previous instructions", a fake booking link, a vandalised OSM `website` tag.
3. **LLM review node** after the checks: judges only (unrealistic days, feeding
   daily_load; what optional and "X or Y" slots mean). Untimed stops are estimated in code
   (time-of-day windows, durations, stated journeys). Every finding cites the check results
   it relies on and is "estimated"; it may call the read-only MCP tools (a few calls at
   most); findings citing nothing are rejected in code; not sent to the fixer at first.
5. **Evaluation:** `cause: regular | special` on opening_hours labels (no separate closures
   type), in the format, validator, drafter and baseline prompts; sweep of the dev drafts'
   `other_issues` (listed for Vincent to confirm before anything moves); results per check
   side by side with the LLM-only baseline, opening_hours also split by cause and by which
   component found it; optional/X-or-Y findings as counts plus Vincent's ratings, unscored.
   Vienna stays held out; tests mock every LLM and search call.

**Cost (decided):** Claude Haiku 5.5 for everything, no free tiers. One-off eval cost about
$5–10; reruns and the demo come from the cache. The public demo serves cached sample runs
only (no live LLM or search calls); live checks on pasted itineraries are for signed-in
users only, within the hourly limits. Live checks cost about $0.15–0.30 per itinerary with
closures (web search $10 per 1,000 searches, ~11–12 places per itinerary on the dev set),
under a monthly spend cap set in the Anthropic Console.

### Definition of done for Tier 1

- All five checks plus closures, each with source and confidence.
- The LLM review step.
- The eval on the test split plus synthetic variants (a few per check type), side by side
  with the baseline; fix validity (share of fixes passing every re-check) reported.
- The injection suite covering web pages.
- A deployed demo (fallback if hosting slips: a recorded demo).
- A README section "What changed and why", including the rule change.

Tier 2 is decided after this is done.

### Future work

- LLM-judged fix quality (PRD metric; noted in the README as not measured in Tier 1) and
  judge calibration.
- The provider-agnostic LLM layer (PRD ADR-6).
- Wikidata cross-check for "official" closure sources (OSM tags can be vandalised).
- Typo detection ("check this time") for AM/PM slips.

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

- **Models (decided 2026-10-24):** Anthropic Claude for the agent and parse step 1 (Haiku 5.5
  for extraction); judge from another provider, chosen later; review embeddings from a local
  open model (bge-small-en-v1.5, 384 dims); reviews loaded for all hotels in the 4 dataset
  cities.

- **Hotel label rule (decided 2026-10-24):** out of 40 sampled reviews, a theme is a red flag
  at about 4+ for noise and cleanliness, 3+ for location, 2+ for safety (ANNOTATION.md step 3).
  Rarer themes count at fewer reviews.
- **Eval scoring (decided 2026-10-24):** the eval runner scores checks two ways, reported
  separately: on the gold stops (checks alone, no API key) and on the agent's own parsed stops
  (end to end), with parsed stops aligned to gold stops by day, order and name.

- **API (decided 2026-10-25):** the Next.js server signs a short-lived HS256 token per API
  call (`API_JWT_SECRET`); FastAPI verifies it and creates users by email. Check runs are
  background tasks with in-memory event logs replayed over SSE; saved runs go to the database
  when they finish. ARCHITECTURE section 6c, decisions 54–61.
- **Demo mode (decided 2026-10-25):** ready-made sample itineraries only (no pasted text, so no
  extraction cost), every check at default settings, fixes on; runs kept in memory for an
  hour, never in the database (settles the `version_id` question). 10 demo runs per IP per
  hour.
- **Injection suite (decided 2026-10-25):** judged in code from structured output, on the demo
  sample (no dataset, no Vienna); free-text OSM tags and opening-hours comments are kept out of
  the fix prompt (`fixes-v2`). ARCHITECTURE section 6d, decision 62.
- Pasted text is cleaned in code before extraction (parse step 0, `parse/clean.py`): image
  links, citations, entities, escapes and widget text go. Limits 50,000 characters raw and
  15,000 cleaned (was one 5,000 limit), so whole ChatGPT replies fit; the dataset keeps raw
  replies. Extraction prompt `extract-v2`: a checklist repeating scheduled places adds no
  stops; `extract-v3`: nor do places recommended outside the schedule ("where to eat"). ARCHITECTURE decision 52. (2026-10-24)
  `extract-v4`: nor do open choices ("X or Y") or activities with no named place ("a boat
  trip"), matching the annotation rule. ARCHITECTURE decision 68. (2026-10-26)
  `extract-v5`: stated durations ("allow 30–40 minutes") go in `duration_min`/`duration_max`,
  never `end_time`; a heading's extra words ("photo stop") aren't part of `place_name`.
  ARCHITECTURE decision 69. Next: travel time and daily load use durations (an estimated
  end, labelled as estimated). (2026-10-26)
  Legs are also recorded where start + duration leaves under 60 minutes before the next
  start (the validator asks for them), so that check has the real times; labels still come
  from stated ends only. (2026-10-26)
  `extract-v6`: a timed slot (a meal or activity with its own time or duration) is a stop even
  with no named place, with the area as place_name; travel sections are routes, not stops.
  ARCHITECTURE decision 70. To verify with the duration work: opening hours stays quiet on
  area stops. (2026-10-26)
  `extract-v7`: stated journeys go on the stop travelled to (`travel_start`, `travel_min/max`);
  timed open choices are stops named by the activity. The validator asks for a leg wherever a
  journey is stated; real > claim + 10 min is a derived warning. ARCHITECTURE decision 71.
  Next (with durations): the travel-time check reports these. (2026-10-26)
  `extract-v8`: ten more stop rules from the dev files (downtime, arrivals, journey lines,
  several places in one slot, favoured options, optional items...). ARCHITECTURE decision 72.
  Dev stops drafted for the 12 files not yet started (paris-02/05/07 are Vincent's); legs,
  violations and preferences still to do. (2026-10-26)
  `extract-v9`: `located: false` for activity-named stops (never geocoded, no legs); marked in
  the dev files (45 stops); the dev files now need 33 legs. paris-07's template placeholders
  replaced with drafted stops. ARCHITECTURE decision 73. (2026-10-26)
  The validator checks a file part by part while it's being annotated (stops, then legs, then
  violations), listing what's still to do; evals still use only complete files. (2026-10-26)
- **Public-holiday warnings are advisory (decided 2026-10-29):** still reported and scored as
  warnings, but not sent to the fixer (`ADVISORY_CHECKS` in `checks/config.py`, fix prompt
  `fixes-v3`); real holiday closures are `opening_hours` violations and get fixes.
  ARCHITECTURE decision 75.
- **Travel-time severity and areas (decided 2026-10-29):** short by up to 10 min is a
  warning, blocker only when overlapping or more than 10 min short; area stops (`Stop.area`,
  set at geocoding) aren't timed. Same rule in ANNOTATION.md. ARCHITECTURE decision 76.
  Drafts made before this may have travel-time blockers that are now warnings: check while
  verifying.
- **Travel-time bands (decided 2026-10-30):** short by up to 10 min (or fits) → no label;
  more than 10 min short → warning; overlapping or more than 30 min short → blocker. Default
  buffer 0 (was 10). Back-to-back slots were a warning at nearly every changeover. Checker,
  ANNOTATION.md and drafter/baseline prompts (`draft-v3`, `baseline-v3`) changed together.
  ARCHITECTURE decision 78. Existing drafts: delete travel-time warnings 10 min short or
  less, and recheck blockers 11–30 min short (now warnings), while verifying.
- **Overlaps from times alone (decided 2026-10-29):** the travel-time check reports an overlap
  on any consecutive timed stops, even unlocated or area ones (was routed legs only, so
  overlaps next to "Lunch" were missed). Gold labels a typo-caused overlap once.
  ARCHITECTURE decision 77.
- Dataset text lives only in `data/dataset/texts/<id>.md` (was pasted into the YAML and
  checked against `data/evals/raw/`); the YAML holds metadata and labels. The 26 saved
  replies moved there. ARCHITECTURE decision 53. (2026-10-24)

## Open

- Tune the travel-mode constants (`checks/legs.py`) and pace limits (`checks/daily_load.py`)
  on the dev set once it exists. Caveat (2026-10-29): the packed limits and the 1.5× walking
  blocker factor were quoted to Vincent before preferences were set (only london-05 is packed,
  12 km), so dev preferences aren't fully blind to the limits.
- Time typos: an AM/PM slip is reported three ways (overlap, opening hours, daily load)
  where the gold has one label. Possible later: code that flags "check this time" instead.
  Not in the PRD; Vincent decides. (ARCHITECTURE decision 77, section 13.)
- Preferences set for all 40 (2026-10-29). No prompt names a car or bike, so no dataset trip
  has them: the car/bike paths are covered by unit tests only. Re-drafted the 4 dev files whose
  preferences changed (barcelona-08, london-05, london-09, paris-09).
- Frontend hosting: no Dockerfile yet; it depends on the hosting shape below.
- Email magic links need an email provider (not chosen); slice 2 can start with OAuth only.

- If traffic outgrows Nominatim's public policy: keyed service (NextGIS, Overpass.ch free
  tier) or self-hosted Nominatim/Overpass for the test-set cities (server session).
- `PH` rules use opening-hours-py's holiday calendar, Nager.Date is used by the holiday check;
  they could disagree. Revisit if evals show it.

- Hosting shape (see CLAUDE.md "Hosting"): Auth.js needs a running Next.js server, so a
  single image means Next.js on the public port proxying `/api` to FastAPI on localhost.
- Judge model (must not be Anthropic); decide when fixes exist.
- How the server gets the review data (dump/restore of `hotels` + `hotel_reviews`).
- Tune `min_share` / `min_reviews` and the theme thresholds against the dev hotels' labels.
- More demo samples (one Paris day today); each must be made up, never from the dataset.
- Parse cost isn't linked to the run that follows it; decide whether the PRD's "under $0.01
  per check" means parse + run, and if so carry the parse usage into the saved version.
- Behind the Next.js proxy, set `FORWARDED_ALLOW_IPS` so per-IP demo limits see real IPs.
- Chatbot replies with interactive widgets (e.g. day tabs) paste incomplete. Planned for the
  paste box: a copyable "rewrite that as plain text" follow-up hint, and a warning when the
  parsed days don't cover the trip. Screenshot upload is a possible later addition.
