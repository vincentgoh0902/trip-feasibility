# Evaluation results

How the system was measured and what it found. The method is in
[`ARCHITECTURE.md`](ARCHITECTURE.md#evaluation): 40 hand-verified itineraries, a dev split
of 15 used while building, and a test split of 25 (including every Vienna itinerary) run
once, at the end.

**How to read the numbers.** A problem the system reports matches a labelled problem on
check, day and place name. *Found* (recall) is how many labelled problems were reported;
*right* (precision) is how many reported problems were real. Counts are shown alongside
percentages because the sets are small. The comparison with the plain LLM is end to end,
from the raw pasted text.

## Final result: the held-out test set (16 Nov 2026)

25 itineraries, 47 labelled problems on the four checks both systems cover (hotel red flags
left out: the baseline doesn't check hotels).

| | Found | Right |
|---|---|---|
| Plain LLM with web search (Claude Haiku 5.5, same rules) | **33 of 47 (70%)** | **33 of 41 (80%)** |
| Checks + second opinion (the hybrid) | 24 of 47 (51%) | 24 of 54 (44%) |

By check:

| Check | Hybrid: right / found | Plain LLM: right / found |
|---|---|---|
| Opening hours | 12 of 23 / 12 of 20 | 13 of 14 / 13 of 20 |
| Public holidays | 11 of 11 / 11 of 11 | 11 of 11 / 11 of 11 |
| Travel time | 1 of 20 / 1 of 10 | 4 of 9 / 4 of 10 |
| Daily load | none raised / 0 of 6 | 5 of 7 / 5 of 6 |

What it shows:

- **The plain LLM wins on held-out data.** The hybrid's dev-set lead (below) didn't hold.
- **Where the hybrid lost:** travel time (19 false alarms) and daily load (nothing raised).
  Both rest on constants that were never tuned.
- **What held up: agreement.** When the checks and the blind web-search second opinion both
  flagged a closed venue, they were right 7 of 8 times, the same 88% as on dev. When they
  disagreed (the checks flagged it, the search found it open), the check was wrong 7 of 8
  times.
- **No overfitting to the cities:** Vienna, never looked at while building, scored as well
  as the new itineraries from the three dev cities (found 12 of 24 against 12 of 23).
- **Fixes:** 36 of 43 proposed fixes passed every re-check (84%); 39 of 40 problems got a
  valid fix.
- One of the 25 itineraries couldn't be parsed (a list of places with no day-by-day plan);
  its problems count as missed.

## On the dev set, while building

15 itineraries, 30 labelled problems on the same four checks. These numbers guided the
design, so they're optimistic by construction.

| | Found | Right |
|---|---|---|
| Hybrid, end to end | 19 of 30 (63%) | 19 of 27 (70%) |
| Checks alone, end to end | 16 of 30 (53%) | 16 of 20 (80%) |
| Plain LLM with web search | 18 of 30 (60%) | 18 of 23 (78%) |
| Hybrid on hand-made stops (no parse errors) | 22 of 30 (73%) | 22 of 28 (79%) |

- **The parser** finds 89% of the hand-labelled stops, up from 73% after a prompt revision
  and a higher effort setting (missed stops across the dev set: 120 → 47).
- **Every problem both the checks and the plain LLM flagged was real** (14 of 14), and each
  found some the other missed. That's what motivated the second opinion.
- **Fixes:** 16 of 18 valid (89%), the same over two runs.

## Prompt injection

36 attacks of our own, six each hidden in: the pasted itinerary, OSM tags, OSM
opening-hours comments, hotel review excerpts, web pages (closures) and search results
(second opinion). Judged in code on whether the attacker's goal showed up in the output.

| | Resisted |
|---|---|
| Shipped defences | **36 of 36** |
| Defences removed (third-party text passed whole, no "data, not instructions" line) | 35 of 36 |

The defences that matter are structural: third-party text never goes into a prompt as
text, extractors have no tools and no free-text output, and code chooses sources and
severity. One attack got through earlier versions: a map link vandalised to point at a fake
"official" site. Official sites now have to be confirmed by Wikidata too. A false closure
planted on a real official page is still reported, as a "worth checking" warning at most.
