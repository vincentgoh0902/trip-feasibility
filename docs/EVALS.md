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

Next to fix: daily load (13 of the 17 false alarms left), then the geocoder (unresolved
English names of local places, in-city matches beating the real day-trip place).

### 2026-10-31 · gold mode · `095a4d6d` · neighbourhood legs from 2 km

What changed: a leg to or from a neighbourhood is timed when it's at least 2 km on foot
(ARCHITECTURE decision 80), set on this dev set. The baseline's one uncertain name pair was
decided (same place), so nothing is provisional.

| Check | Precision | Recall | Severity | vs `8ef09a3b` |
|---|---|---|---|---|
| Opening hours | 10 of 12 | 10 of 17 | 90% | unchanged |
| Public holidays | 4 of 4 | 4 of 4 | 100% | unchanged |
| Travel time | 5 of 7 | 5 of 8 | 80% | both lost hits back, no new false alarms |
| Daily load | 2 of 15 | 2 of 2 | 100% | unchanged |
| **All four** | **21 of 38 (55%)** | **21 of 31 (68%)** | | |
| Hotel red flags | 1 of 1 | 1 of 2 | 100% | unchanged |

Unknown: 134 stops for opening hours, 137 for travel time.

### 2026-10-31 · LLM-only baseline · `baseline-v4`

Rerun for the slot-splitting rule (decision 79); scored with `scripts/score_baseline.py`.
Two files each had one entry dropped as invalid after the retry. The model isn't
deterministic, so part of the change from `baseline-v3` is run-to-run noise, not the rule.

| Check | Precision | Recall | Severity |
|---|---|---|---|
| Opening hours | 11 of 12 | 11 of 17 | 100% |
| Public holidays | 4 of 4 | 4 of 4 | 100% |
| Travel time | 2 of 2 | 2 of 8 | 50% |
| Daily load | 1 of 4 | 1 of 2 | 100% |
| **All four** | **18 of 22 (82%)** | **18 of 31 (58%)** | 94% |

Side by side on the four checks (not like-for-like: ours on gold stops, the baseline from
the raw text; parsed mode is the fair comparison):

| | Precision | Recall |
|---|---|---|
| Ours, gold mode `095a4d6d` | 21 of 38 (55%) | 21 of 31 (68%) |
| Baseline `baseline-v4` | 18 of 22 (82%) | 18 of 31 (58%) |

We find more of the real problems; the baseline raises far fewer false alarms, and 13 of our
17 come from daily load.

### 2026-10-31 · gold mode · `146a353c` · daily load counts become hints

What changed: daily load's venue counts and day length are info, not warnings (ARCHITECTURE
decision 81); only walking over the limit is reported. Dataset: one travel-time label
removed from a dev file on re-checking it against the 10-minute rule (decision 78), so
travel time has 7 labels, not 8, and the dataset version changed (`34429875d542`).

| Check | Precision | Recall | Severity | vs `095a4d6d` |
|---|---|---|---|---|
| Opening hours | 10 of 12 | 10 of 17 | 90% | unchanged |
| Public holidays | 4 of 4 | 4 of 4 | 100% | unchanged |
| Travel time | 5 of 7 | 5 of 7 | 80% | one label fewer |
| Daily load | none reported | 0 of 2 | | 13 false alarms and 2 lucky hits gone |
| **All four** | **19 of 23 (83%)** | **19 of 30 (63%)** | | |
| Hotel red flags | 1 of 1 | 1 of 2 | 100% | unchanged |

The baseline (`baseline-v4`, same outputs) on this dataset version:

| Check | Precision | Recall |
|---|---|---|
| Opening hours | 11 of 12 | 11 of 17 |
| Public holidays | 4 of 4 | 4 of 4 |
| Travel time | 2 of 2 | 2 of 7 |
| Daily load | 1 of 4 | 1 of 2 |
| **All four** | **18 of 22 (82%)** | **18 of 30 (60%)** |

Side by side (still not like-for-like: ours on gold stops, the baseline from the raw text):

| | Precision | Recall |
|---|---|---|
| Ours, gold mode `146a353c` | 19 of 23 (83%) | 19 of 30 (63%) |
| Baseline `baseline-v4` | 18 of 22 (82%) | 18 of 30 (60%) |

Remaining false alarms (4): two opening-hours blockers from OSM data (a date-range gap; hours
for tourist visits only), and two travel-time legs. Remaining misses (11): long closures and
"X or Y" options (the closures check and the LLM review), unresolved names, OSM hours missing
or stale, the 2 overloaded days (LLM review), and journeys the checks can't see yet.

### 2026-10-31 · gold mode · `ed8f90eb` · geocoder checks names

What changed (ARCHITECTURE decision 82): searches ask for every OSM name; an exact name
outside the city beats an inexact one inside; the nearby (day-trip) fallback needs a
matching name; a trailing kind word ("Amusement Park", "Market") is searched without it and
accepted only for a place of that kind; three more OSM types count as neighbourhoods. Same
config and dataset as `146a353c`. Every search was re-queried (new cache keys).

| Check | Precision | Recall | Severity | vs `146a353c` |
|---|---|---|---|---|
| Opening hours | 10 of 12 | 10 of 17 | 90% | unchanged |
| Public holidays | 4 of 4 | 4 of 4 | 100% | unchanged |
| Travel time | 5 of 6 | 5 of 7 | 80% | one false alarm gone, by chance (below) |
| Daily load | none reported | 0 of 2 | | unchanged |
| **All four** | **19 of 22 (86%)** | **19 of 30 (63%)** | | |
| Hotel red flags | 1 of 1 | 1 of 2 | 100% | unchanged |

What the geocoder fixed on the dev set: "Versailles" is now the town (was a Paris exhibition
centre), "Tibidabo Amusement Park" and "La Boqueria Market" resolve (were unresolved),
"Montjuïc gardens" is unresolved (was a garden in another town), and the West End,
Saint-Germain and Montmartre are areas. None of these moved a score: the newly found places
have no OSM hours, or no labelled problem. The one false alarm that went away (a street to a
house on that street) went because the street now resolves to a different segment of
itself: luck, not the fix. Still wrong: "Petit Trianon" resolves to a Paris restaurant
called "Le Petit Trianon" (the real one isn't among Nominatim's results), and the Montserrat
abbey, the Sant Pau site and the Expo Hotel stay unresolved.
