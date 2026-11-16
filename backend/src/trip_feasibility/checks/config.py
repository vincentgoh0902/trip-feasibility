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
    "second_opinion",
    "review",
]
# closures last of the tools: it's the slow one (web search), so its results come in last
# anyway.
CHECK_NAMES: tuple[CheckName, ...] = (
    "opening_hours",
    "public_holidays",
    "travel_time",
    "daily_load",
    "hotel_red_flags",
    "closures",
    "second_opinion",
    "review",
)
# Not MCP tools: the second opinion is a web-search pass set against the checks' results
# (decision 97), the review judges their results (decision 96).
NOT_TOOLS: frozenset[CheckName] = frozenset({"second_opinion", "review"})
TOOL_CHECKS: tuple[CheckName, ...] = tuple(n for n in CHECK_NAMES if n not in NOT_TOOLS)
# Shown, but never sent to the fixer: a fact about the date, not a fault a change to the plan
# solves (decision 75). A real holiday closure is an opening_hours violation, which is fixed.
# The review is a judgement, not sent at first (decision 96). The second opinion isn't re-run
# on re-checks, so a fix could never be shown to clear it (decision 97).
ADVISORY_CHECKS: frozenset[CheckName] = frozenset({"public_holidays", "second_opinion", "review"})


# Off unless asked for: on dev the review flags more fine days than overloaded ones
# (decision 96), so it isn't shown to travellers until it beats that; the second opinion
# makes paid web searches and waits for its dev eval (decision 97).
DEFAULT_ENABLED: tuple[CheckName, ...] = tuple(
    n for n in CHECK_NAMES if n not in ("second_opinion", "review")
)


class CheckConfig(BaseModel):
    enabled: list[CheckName] = Field(default=list(DEFAULT_ENABLED))
    travel_buffer_min: Literal[0, 10, 20] = DEFAULT_BUFFER_MIN
    hotel_min_reviews: MinReviews = DEFAULT_MIN_REVIEWS
