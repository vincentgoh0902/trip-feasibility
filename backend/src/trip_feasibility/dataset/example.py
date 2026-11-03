"""The made-up worked example (docs/annotation/example.yaml + example.md): safe to run and
print anywhere, unlike the dataset. The CLI, the smoke test and the snapshot use it."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from trip_feasibility import paths
from trip_feasibility.dataset.validate import load_yaml
from trip_feasibility.itinerary import TravelPreferences
from trip_feasibility.parse.schema import ExtractedStop


@dataclass
class Example:
    city: str  # as looked up: "Paris"
    trip_start: date
    preferences: TravelPreferences
    stops: list[ExtractedStop]  # the hand-written gold stops
    text: str  # the itinerary as written (example.md)


def load_example(path: Path = paths.EXAMPLE) -> Example:
    raw = load_yaml(path)
    fields = ExtractedStop.model_fields
    return Example(
        city=raw["city"].title(),
        trip_start=raw["trip_start"],
        preferences=TravelPreferences(**raw["preferences"]),
        stops=[ExtractedStop(**{k: v for k, v in s.items() if k in fields}) for s in raw["stops"]],
        text=path.with_suffix(".md").read_text(encoding="utf-8"),
    )
