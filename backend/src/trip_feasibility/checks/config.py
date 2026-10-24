"""Which checks run and how strict they are (PRD FR-5). Saved with every check run."""

from typing import Literal

from pydantic import BaseModel, Field

from trip_feasibility.checks.hotel_red_flags import DEFAULT_MIN_REVIEWS, MinReviews
from trip_feasibility.checks.travel_time import DEFAULT_BUFFER_MIN

CheckName = Literal[
    "opening_hours", "public_holidays", "travel_time", "daily_load", "hotel_red_flags"
]
CHECK_NAMES: tuple[CheckName, ...] = (
    "opening_hours",
    "public_holidays",
    "travel_time",
    "daily_load",
    "hotel_red_flags",
)


class CheckConfig(BaseModel):
    enabled: list[CheckName] = Field(default=list(CHECK_NAMES))
    travel_buffer_min: Literal[0, 10, 20] = DEFAULT_BUFFER_MIN
    hotel_min_reviews: MinReviews = DEFAULT_MIN_REVIEWS
