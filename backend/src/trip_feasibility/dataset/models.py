"""The annotation file format: one YAML file per itinerary, plus hotels.yaml.

An itinerary file holds metadata (written when it's collected) and, once
annotated, the gold stops, real travel times for tight legs, and hand-labelled violations.
The guide is docs/ANNOTATION.md; docs/annotation/example.yaml is a filled-in example.

Gold stops are what parse step 1 (LLM extraction) should return, plus annotation-only
fields (`osm`, `hotel`). Travel-time and hotel violations are not hand-labelled: they are
derived from `legs` and hotels.yaml (see validate.gold_violations). The text isn't in the
YAML: it's the saved reply in texts/<id>.md, which validate.load_dataset reads into `text`.
"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trip_feasibility.dataset.split import City, DatasetEntry
from trip_feasibility.itinerary import OsmRef, TransportMode, TravelPreferences
from trip_feasibility.parse.clean import MAX_CLEAN_CHARS, MAX_RAW_CHARS, clean_text
from trip_feasibility.parse.schema import ExtractedStop

Source = Literal["chatgpt", "claude", "gemini", "synthetic"]
HotelTheme = Literal["noise", "cleanliness", "safety", "location"]
# checks labelled by hand; travel_time and hotel_red_flags labels are derived
LabelledCheck = Literal["opening_hours", "public_holidays", "daily_load"]
LabelSeverity = Literal["warning", "blocker"]

_OSM_REF = re.compile(r"(node|way|relation)/(\d+)/?$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Hotel(_Strict):
    key: str = Field(pattern=r"^[a-z0-9-]+$")
    city: City
    name: str = Field(min_length=1)
    csv_name: str = Field(min_length=1)  # Hotel_Name in the Kaggle CSV
    # None until annotated; [] means no red flags
    red_flags: list[HotelTheme] | None = None
    note: str | None = None


class AnnotatedStop(ExtractedStop, _Strict):
    """A gold stop: what parse step 1 should return, plus annotation-only fields."""

    id: str = Field(min_length=1, max_length=50)
    # the real place, and for hotel stops the key in hotels.yaml
    osm: OsmRef | None = None
    hotel: str | None = None

    @field_validator("osm", mode="before")
    @classmethod
    def _parse_osm(cls, v):
        """Accept "way/123" or an openstreetmap.org URL."""
        if isinstance(v, str):
            if not (m := _OSM_REF.search(v.strip())):
                raise ValueError('expected "node/123", "way/123" or "relation/123"')
            return {"type": m[1], "id": int(m[2])}
        return v


class TravelLeg(_Strict):
    """The real door-to-door travel time between two consecutive stops."""

    from_stop: str = Field(alias="from")
    to_stop: str = Field(alias="to")
    mode: TransportMode
    minutes: int = Field(ge=0, le=600)
    source: str = Field(min_length=1)


class LabelledViolation(_Strict):
    check: LabelledCheck
    severity: LabelSeverity
    # opening_hours names a stop; public_holidays and daily_load name a day
    stop: str | None = None
    day: int | None = Field(default=None, ge=1)
    note: str = Field(min_length=1)
    source: str | None = None

    @model_validator(mode="after")
    def _target_matches_check(self):
        wants_stop = self.check == "opening_hours"
        if wants_stop and (self.stop is None or self.day is not None):
            raise ValueError("opening_hours labels name a stop, not a day")
        if not wants_stop and (self.day is None or self.stop is not None):
            raise ValueError(f"{self.check} labels name a day, not a stop")
        return self


class DatasetItinerary(DatasetEntry, _Strict):
    source: Source
    model: str | None = None  # the generating model, as named in its UI
    prompt: str | None = None
    trip_start: date
    preferences: TravelPreferences = TravelPreferences()
    # from texts/<id>.md, as copied; extraction (and so every gold place_name) sees clean_text
    text: str = Field(min_length=1, max_length=MAX_RAW_CHARS)
    # absent until annotated
    stops: list[AnnotatedStop] | None = None
    legs: list[TravelLeg] | None = None
    violations: list[LabelledViolation] | None = None
    notes: str | None = None

    @property
    def clean_text(self) -> str:
        return clean_text(self.text)

    @property
    def annotated(self) -> bool:
        return self.stops is not None

    @model_validator(mode="after")
    def _annotation_complete(self):
        parts = {"stops": self.stops, "legs": self.legs, "violations": self.violations}
        missing = [k for k, v in parts.items() if v is None]
        if 0 < len(missing) < 3:
            raise ValueError(
                f"annotated files need stops, legs and violations (use [] for none); "
                f"missing: {', '.join(missing)}"
            )
        if len(self.clean_text) > MAX_CLEAN_CHARS:
            raise ValueError(
                f"text is {len(self.clean_text):,} characters once cleaned (limit "
                f"{MAX_CLEAN_CHARS:,}): regenerate with a shorter word limit, don't cut it"
            )
        if (self.source == "synthetic") != (self.parent_id is not None):
            raise ValueError("synthetic itineraries need a parent_id, and only they have one")
        return self
