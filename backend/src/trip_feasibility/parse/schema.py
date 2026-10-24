"""What parse step 1 (LLM extraction) must return, and how it's validated (step 2).

The same fields are the gold stops in the dataset (dataset.models.AnnotatedStop extends
ExtractedStop), so parse accuracy compares like with like. Optional fields stay null when
the text doesn't state them; place_name is copied exactly as written.
"""

import re
from datetime import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trip_feasibility.itinerary import StopType, TimeOfDay

_SHORT_TIME = re.compile(r"^\d:\d\d$")


class ExtractedStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    start_time: time | None = None
    end_time: time | None = None
    time_of_day: TimeOfDay | None = None
    place_name: str = Field(min_length=1, max_length=300)
    type: StopType

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _pad_hour(cls, v):
        if isinstance(v, str) and _SHORT_TIME.match(v):
            return f"0{v}"
        return v

    @model_validator(mode="after")
    def _one_kind_of_time(self):
        if self.time_of_day and (self.start_time or self.end_time):
            raise ValueError("use start/end times or time_of_day, not both")
        return self


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stops: list[ExtractedStop] = Field(max_length=200)


def validate_extraction(raw: str | dict) -> Extraction | str:
    """Step 2: the parsed extraction, or the validation error as text to send back to the
    model for its one retry."""
    try:
        if isinstance(raw, str):
            return Extraction.model_validate_json(raw)
        return Extraction.model_validate(raw)
    except ValidationError as e:
        return str(e)
