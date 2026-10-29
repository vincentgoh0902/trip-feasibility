"""Structured itinerary stops: the output of the parse pipeline and the input to every check."""

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field

StopType = Literal["venue", "transfer", "hotel", "meal"]
TransportMode = Literal["walk", "transit", "bike", "car"]
Pace = Literal["relaxed", "moderate", "packed"]
TimeOfDay = Literal["morning", "afternoon", "evening"]

# Parse step 3 turns "morning" etc. into these windows; annotators judge such stops by them too.
TIME_OF_DAY_WINDOWS: dict[TimeOfDay, tuple[time, time]] = {
    "morning": (time(9), time(12)),
    "afternoon": (time(13), time(17)),
    "evening": (time(18), time(21)),
}


class OsmRef(BaseModel):
    type: Literal["node", "way", "relation"]
    id: int = Field(gt=0)

    @property
    def url(self) -> str:
        return f"https://www.openstreetmap.org/{self.type}/{self.id}"


class Stop(BaseModel):
    id: str = Field(min_length=1, max_length=50)
    day: int = Field(ge=1)
    date: date
    start_time: time | None = None
    end_time: time | None = None
    place_name: str = Field(min_length=1, max_length=300)
    type: StopType
    osm: OsmRef | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    # Located at a neighbourhood or district, not one point: travel to and from it isn't timed
    area: bool = False


class TravelPreferences(BaseModel):
    """How the traveller likes to travel (PRD FR-4). Defaults apply when they never set any."""

    pace: Pace = "moderate"
    max_walk_km: float = Field(default=10.0, gt=0, le=100)
    transport_modes: list[TransportMode] = Field(default=["walk", "transit"], min_length=1)
