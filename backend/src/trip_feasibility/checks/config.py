"""Which checks run and how strict they are (PRD FR-5). Saved with every check run."""

from typing import Literal

from pydantic import BaseModel, Field

from trip_feasibility.checks.hotel_red_flags import DEFAULT_MIN_REVIEWS, MinReviews
from trip_feasibility.checks.travel_time import DEFAULT_BUFFER_MIN

CheckName = Literal[
    "opening_hours",
    "public_holidays",
    "travel_time",
    "daily_load",
    "hotel_red_flags",
    "closures",
    "review",
]
# closures last of the tools: it's the slow one (web search), so its results come in last
# anyway. The review isn't a tool: it runs after all of them, on their results (decision 96).
CHECK_NAMES: tuple[CheckName, ...] = (
    "opening_hours",
    "public_holidays",
    "travel_time",
    "daily_load",
    "hotel_red_flags",
    "closures",
    "review",
)
TOOL_CHECKS: tuple[CheckName, ...] = tuple(n for n in CHECK_NAMES if n != "review")
# Shown, but never sent to the fixer: a fact about the date, not a fault a change to the plan
# solves (decision 75). A real holiday closure is an opening_hours violation, which is fixed.
# The review is a judgement, not sent at first (decision 96).
ADVISORY_CHECKS: frozenset[CheckName] = frozenset({"public_holidays", "review"})


# The review is off unless asked for: on dev it flags more fine days than overloaded ones
# (decision 96), so it isn't shown to travellers until it beats that.
DEFAULT_ENABLED: tuple[CheckName, ...] = tuple(n for n in CHECK_NAMES if n != "review")


class CheckConfig(BaseModel):
    enabled: list[CheckName] = Field(default=list(DEFAULT_ENABLED))
    travel_buffer_min: Literal[0, 10, 20] = DEFAULT_BUFFER_MIN
    hotel_min_reviews: MinReviews = DEFAULT_MIN_REVIEWS
