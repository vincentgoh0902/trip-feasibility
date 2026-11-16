"""Demo itineraries. Anonymous callers check these, as ready-made stops or pasted as text;
the model's part of either comes from replies prepared in advance (`prepared`, decision
100), so no anonymous request ever pays for a model call."""

from datetime import date
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from trip_feasibility.itinerary import TravelPreferences
from trip_feasibility.parse.schema import ExtractedStop

SAMPLES_FILE = Path(__file__).with_name("samples.yaml")


class Sample(BaseModel):
    id: str
    title: str
    city: str
    start_date: date
    preferences: TravelPreferences
    text: str
    stops: list[ExtractedStop]


@cache
def samples() -> dict[str, Sample]:
    raw = yaml.safe_load(SAMPLES_FILE.read_text())
    return {s["id"]: Sample.model_validate(s) for s in raw}


def _words(text: str) -> str:
    return " ".join(text.split())


def sample_for_text(text: str) -> Sample | None:
    """The sample this pasted text is, ignoring whitespace (a copy may gain or lose line
    breaks), so its prepared extraction is found."""
    pasted = _words(text)
    return next((s for s in samples().values() if _words(s.text) == pasted), None)
