"""Demo itineraries: the only thing an anonymous caller can check (no pasted text, so the
demo never pays for an extraction call on someone else's text)."""

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
