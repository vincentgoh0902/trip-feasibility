"""The parse pipeline (PRD FR-1): text → structured, located stops.

0. cleaning: chat-interface debris (image links, citations, widget text) removed (`clean.py`)
1. extraction: one LLM call returns `Extraction` (`extract.py`)
2. validation: Pydantic, with one retry on failure (`schema.py`)
3. normalisation: day numbers → dates, morning/afternoon/evening → time windows (`normalise.py`)
4. geocoding: place names → OSM places via Nominatim, never guessed (`geocode.py`)
5. user review happens in the frontend
"""
