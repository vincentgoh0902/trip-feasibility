"""Score the hotel red-flag theme matcher on hand-labelled reviews (Mac only).

    uv run --group embed python scripts/calibrate_hotel_themes.py            # current settings
    uv run --group embed python scripts/calibrate_hotel_themes.py --sweep    # best thresholds

Reads data/calibration/hotel_theme_sample.jsonl (tuning set) and hotel_theme_holdout.jsonl
(scored, never tuned on): one review per line, {"id", "hotel", "text", "themes": [...]}.
Both come from hotels outside the dataset (and never Vienna). Uses the same keywords,
thresholds and query vectors as the check, with Python's re in place of Postgres' ~*.
"""

import argparse
import json
import re
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

from trip_feasibility.checks.hotel_red_flags import THEMES, theme_vectors
from trip_feasibility.models import REVIEW_EMBEDDING_MODEL

DIR = Path(__file__).resolve().parents[2] / "data" / "calibration"
FILES = {"tuning": DIR / "hotel_theme_sample.jsonl", "holdout": DIR / "hotel_theme_holdout.jsonl"}


def f1(pred: np.ndarray, gold: np.ndarray) -> float:
    tp = int((pred & gold).sum())
    p, r = tp / max(int(pred.sum()), 1), tp / max(int(gold.sum()), 1)
    return 2 * p * r / (p + r) if p + r else 0.0


def score(pred: np.ndarray, gold: np.ndarray) -> str:
    tp = int((pred & gold).sum())
    p, r = tp / max(int(pred.sum()), 1), tp / max(int(gold.sum()), 1)
    return (
        f"P {p:.2f}  R {r:.2f}  F1 {f1(pred, gold):.2f}  "
        f"(n={int(gold.sum())}, predicted {int(pred.sum())})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()

    model = SentenceTransformer(REVIEW_EMBEDDING_MODEL, device="mps")
    queries = {
        name: np.array([json.loads(v) for v in vs], dtype=np.float32)
        for name, vs in theme_vectors().items()
    }
    for split, path in FILES.items():
        rows = [json.loads(line) for line in path.open()]
        texts = [r["text"] for r in rows]
        embeddings = model.encode(texts, normalize_embeddings=True)
        print(f"{split}: {len(rows)} reviews ({path.name})")
        for name, theme in THEMES.items():
            gold = np.array([name in r["themes"] for r in rows])
            similarity = (embeddings @ queries[name].T).max(axis=1)
            keyword = np.array([bool(re.search(theme.keywords, t, re.IGNORECASE)) for t in texts])
            pred = keyword & (similarity >= theme.min_similarity)
            print(f"  {name:12} {score(pred, gold)}")
            if args.sweep and split == "tuning":
                taus = np.arange(0.50, 0.80, 0.01)
                best = max(taus, key=lambda tau: f1(keyword & (similarity >= tau), gold))
                best_pred = keyword & (similarity >= best)
                print(f"  {'':12} best min_similarity {best:.2f}: {score(best_pred, gold)}")


if __name__ == "__main__":
    main()
