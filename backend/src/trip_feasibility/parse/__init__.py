"""The parse pipeline (PRD FR-1): text → structured, located stops.

1. extraction (LLM, not built yet) returns `Extraction`
2. validation: Pydantic, with one retry on failure (`schema.py`)
3. normalisation: day numbers → dates, morning/afternoon/evening → time windows (`normalise.py`)
4. geocoding: place names → OSM places via Nominatim, never guessed (`geocode.py`)
5. user review happens in the frontend
"""
