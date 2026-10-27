"""The annotation file format: one YAML file per itinerary, plus hotels.yaml.

An itinerary file holds metadata (written when it's collected) and, once annotated, the
gold: violations as a traveller would read them (check, day, place and time as written,
with evidence), the things that couldn't be established (`unknowns`) and problems outside
the checks (`other_issues`). A draft records who drafted it and counts only once a human
has verified it. Hotel violations aren't labelled per file: they come from the itinerary's
`hotel` and hotels.yaml (see validate.gold_violations). The guide is docs/ANNOTATION.md;
docs/annotation/example.yaml is a filled-in example.

Gold stops and legs are dev only, for parser debugging (decision 74): stops are what parse
step 1 (LLM extraction) should return, plus annotation-only fields (`osm`, `hotel`). The
text isn't in the YAML: it's the saved reply in texts/<id>.md, which validate.load_dataset
reads into `text`.
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
# checks labelled in the file; hotel_red_flags labels come from hotels.yaml
LabelledCheck = Literal["opening_hours", "public_holidays", "travel_time", "daily_load"]
Check = LabelledCheck | Literal["hotel_red_flags"]
LabelSeverity = Literal["warning", "blocker"]
# verified: looked up (official site, Google Maps); estimated: a judgement from the text
Confidence = Literal["verified", "estimated"]
# the checks that name a place; the others name only a day
PLACE_CHECKS = ("opening_hours", "travel_time")

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
    """A true violation, in the itinerary's own words: no stop ids, so any system's output
    can be matched to it (check + day + place)."""

    check: LabelledCheck
    # opening_hours and travel_time name a place (travel_time: the one travelled to, with
    # the one left as from_place when the text names it); the others only a day
    day: int = Field(ge=1)
    place: str | None = Field(default=None, min_length=1, max_length=300)
    from_place: str | None = Field(default=None, min_length=1, max_length=300)
    time: str | None = Field(default=None, min_length=1, max_length=100)  # as written
    severity: LabelSeverity
    evidence: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confidence: Confidence

    @model_validator(mode="after")
    def _target_matches_check(self):
        if self.check in PLACE_CHECKS and self.place is None:
            raise ValueError(f"{self.check} labels name a place")
        if self.check not in PLACE_CHECKS and self.place is not None:
            raise ValueError(f"{self.check} labels name a day, not a place")
        if self.from_place is not None and self.check != "travel_time":
            raise ValueError("only travel_time labels have a from_place")
        return self


class Unknown(_Strict):
    """Something a check would judge that the annotator couldn't establish. Not scored
    either way: a prediction matching it is neither right nor wrong."""

    check: Check
    day: int | None = Field(default=None, ge=1)
    place: str | None = Field(default=None, min_length=1, max_length=300)
    reason: str = Field(min_length=1)


class DatasetItinerary(DatasetEntry, _Strict):
    source: Source
    model: str | None = None  # the generating model, as named in its UI
    prompt: str | None = None
    trip_start: date
    preferences: TravelPreferences = TravelPreferences()
    # the hotel the prompt names, as its key in hotels.yaml (its red flags are gold labels)
    hotel: str | None = None
    # from texts/<id>.md, as copied; extraction (and every quoted place) sees clean_text
    text: str = Field(min_length=1, max_length=MAX_RAW_CHARS)
    # the gold, absent until drafted: who drafted it ("human", or the model ID)
    drafted_by: str | None = Field(default=None, min_length=1)
    verified_by_human: bool = False
    violations: list[LabelledViolation] | None = None
    unknowns: list[Unknown] = []
    other_issues: list[str] = []
    notes: str | None = None
    # dev only, for parser debugging (frozen rules); absent until annotated
    stops: list[AnnotatedStop] | None = None
    legs: list[TravelLeg] | None = None

    @property
    def clean_text(self) -> str:
        return clean_text(self.text)

    @property
    def annotated(self) -> bool:
        """The gold is there and a human has verified it (evals use only these)."""
        return self.violations is not None and self.verified_by_human

    @property
    def still_to_do(self) -> list[str]:
        """What's missing before the gold counts: a draft, then a human check."""
        if self.violations is None:
            return []
        return [] if self.verified_by_human else ["verify the draft (verified_by_human: true)"]

    @model_validator(mode="after")
    def _annotation_order(self):
        if self.stops is None and self.legs is not None:
            raise ValueError("add stops before legs")
        if (self.violations is None) != (self.drafted_by is None):
            raise ValueError("violations and drafted_by go together")
        if self.verified_by_human and self.violations is None:
            raise ValueError("nothing to verify: no violations yet")
        if len(self.clean_text) > MAX_CLEAN_CHARS:
            raise ValueError(
                f"text is {len(self.clean_text):,} characters once cleaned (limit "
                f"{MAX_CLEAN_CHARS:,}): regenerate with a shorter word limit, don't cut it"
            )
        if (self.source == "synthetic") != (self.parent_id is not None):
            raise ValueError("synthetic itineraries need a parent_id, and only they have one")
        return self
