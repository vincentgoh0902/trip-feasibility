"""Embed the hotel red-flag theme phrasings into checks/hotel_themes.json (Mac only).

    uv run --group embed python scripts/embed_hotel_themes.py

Rerun whenever THEMES' phrasings or REVIEW_EMBEDDING_MODEL change; the check refuses a stale
file. The server never embeds: it reads the vectors from that file.
"""

import json
from pathlib import Path

from sentence_transformers import SentenceTransformer

from trip_feasibility.checks import hotel_red_flags
from trip_feasibility.checks.hotel_red_flags import QUERY_PREFIX, THEMES
from trip_feasibility.models import REVIEW_EMBEDDING_DIM, REVIEW_EMBEDDING_MODEL

OUT = Path(hotel_red_flags.__file__).with_name("hotel_themes.json")


def main() -> None:
    model = SentenceTransformer(REVIEW_EMBEDDING_MODEL, device="cpu")
    themes = {}
    for name, theme in THEMES.items():
        vectors = model.encode(
            [QUERY_PREFIX + p for p in theme.phrasings], normalize_embeddings=True
        )
        assert vectors.shape == (len(theme.phrasings), REVIEW_EMBEDDING_DIM)
        themes[name] = {
            "phrasings": list(theme.phrasings),
            "vectors": [[round(float(x), 6) for x in v] for v in vectors],
        }
    data = {"model": REVIEW_EMBEDDING_MODEL, "query_prefix": QUERY_PREFIX, "themes": themes}
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
