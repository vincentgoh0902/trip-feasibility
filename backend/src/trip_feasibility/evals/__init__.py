"""The eval harness: runs the checks over the annotated dataset and scores them.

Two modes, reported separately (decided 2026-10-24):
- gold: the checks run on the hand-written gold stops (located from their OSM refs), so
  scores measure the checks alone. No API key needed.
- parsed: the full pipeline (Claude extraction, geocoding, checks) on the raw text; parsed
  stops are aligned to gold stops (align.py) before scoring, and parse accuracy is scored too.

Every run writes an eval_runs row. The test split (and so Vienna) runs only with --final.
"""
