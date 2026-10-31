# Eval results log

Every eval run worth keeping, newest last, with what changed in between. Each run is
written to `eval_runs` (model ID, prompt version, config, dataset version) and its details
go to `data/evals/runs/` (gitignored). How the eval works is in ARCHITECTURE.md ("The eval
runner"); the labelling rules are in ANNOTATION.md.

## How to read the numbers

- **Dev split only** (5 each from Paris, London, Barcelona) until the final test run. Vienna
  and the rest of the test split are untouched.
- **What's compared:** our checks' violations against the human-verified gold violations.
  A prediction matches a label on check + day + place name (fuzzy, one to one; uncertain
  names are decided once by a human, `scripts/match_decisions.py`).
- **Precision:** of the problems reported, how many are real. **Recall:** of the real
  problems, how many were reported. Shown as counts: 15 dev itineraries make percentages
  jumpy. **Severity:** agreement on warning vs blocker among the matches.
- **Gold mode:** the checks run on the hand-made gold stops, so extraction is perfect. Gold
  files have no OSM refs, so place names still go through our geocoder: this measures checks
  plus geocoding. **Parsed mode:** end to end from the raw text (needs the API key). The fair
  comparison with the LLM-only baseline is parsed mode.
- Hotel red flags are shown separately: the baseline doesn't check hotels.

## Runs

### 2026-10-30 · gold mode · `730a021b` · first run on the verified dev set

Config: all checks, travel buffer 0, hotel min reviews 5. Dataset as verified by Vincent.

| Check | Precision | Recall | Severity |
|---|---|---|---|
| Opening hours | 9 of 13 | 9 of 17 | 89% |
| Public holidays | 3 of 3 | 3 of 4 | 100% |
| Travel time | 5 of 15 | 5 of 8 | 80% |
| Daily load | 2 of 15 | 2 of 2 | 100% |
| **All four** | **19 of 46 (41%)** | **19 of 31 (61%)** | |
| Hotel red flags | 1 of 1 | 1 of 2 | 100% |

Unknown (couldn't judge): 132 stops for opening hours, 138 for travel time.

### 2026-10-30 · LLM-only baseline · `baseline-v3`

Claude Haiku 5.5 with web search via headless Claude Code, reading the raw text (so not
like-for-like with gold mode). One place-name pair undecided at the time, which counts as a
miss and a false alarm against the baseline.

| Check | Precision | Recall |
|---|---|---|
| Opening hours | 9 of 10 | 9 of 17 |
| Public holidays | 4 of 4 | 4 of 4 |
| Travel time | 2 of 3 | 2 of 8 |
| Daily load | 1 of 1 | 1 of 2 |
| **All four** | **16 of 18 (89%)** | **16 of 31 (52%)** |

Superseded: the prompt is now `baseline-v4` (decision 79), so it needs a rerun.

### 2026-10-31 · gold mode · `8ef09a3b` · after the four diagnosis fixes

What changed (ARCHITECTURE decision 79; causes in PLAN.md "Gold-mode diagnosis"): stops in
one morning/afternoon/evening slot split it in order; the holiday check knows the city's
region; opening hours use the place's coordinates for sunrise/sunset; gold stops are located
like parsed ones (`located: false` never looked up, neighbourhoods marked as areas).
Same config and dataset as `730a021b`.

| Check | Precision | Recall | Severity | vs `730a021b` |
|---|---|---|---|---|
| Opening hours | 10 of 12 | 10 of 17 | 90% | 2 false alarms gone, 1 more found |
| Public holidays | 4 of 4 | 4 of 4 | 100% | regional holiday now found |
| Travel time | 3 of 5 | 3 of 8 | 100% | 8 false alarms gone, 2 hits lost (legs into areas, now untimed) |
| Daily load | 2 of 15 | 2 of 2 | 100% | unchanged |
| **All four** | **19 of 36 (53%)** | **19 of 31 (61%)** | | |
| Hotel red flags | 1 of 1 | 1 of 2 | 100% | unchanged |

Unknown: 134 stops for opening hours, 138 for travel time. Marked provisional by the
runner because the baseline's undecided name pair sits in a dev itinerary; gold-mode
predictions don't depend on it.

Next to fix: daily load (13 of 17 remaining false alarms), then the geocoder (unresolved
English names of local places, in-city matches beating the real day-trip place).
