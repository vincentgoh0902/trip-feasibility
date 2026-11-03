"""Where the repo keeps its local files: for scripts and evals on the Mac, never the app.

The deployed API takes everything it needs from settings; nothing it runs reads these. `data/`
is gitignored (dataset, eval details, caches), `docs/annotation/` is committed.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # src/trip_feasibility/paths.py → repo root

DATA = REPO / "data"
DATASET = DATA / "dataset"  # hotels.yaml, itineraries/<id>.yaml, texts/<id>.md
DATASET_DRAFTS = DATA / "dataset-drafts"  # the drafter's raw outputs
EVALS = DATA / "evals"
EVAL_RUNS = EVALS / "runs"  # per-run details
BASELINE = EVALS / "baseline"  # cached LLM-only baseline outputs
MATCH_DECISIONS = EVALS / "match_decisions.yaml"
SNAPSHOT = EVALS / "snapshot.json"
EMBEDDINGS = DATA / "embeddings"
CALIBRATION = DATA / "calibration"
REVIEWS_CSV = DATA / "raw" / "hotel-reviews" / "Hotel_Reviews.csv"

EXAMPLE = REPO / "docs" / "annotation" / "example.yaml"  # made up; text in example.md
HOTELS_TEMPLATE = REPO / "docs" / "annotation" / "hotels.yaml"  # the 16 hotels, unlabelled
OPENAPI = REPO / "frontend" / "openapi.json"
