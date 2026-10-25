"""Request and response bodies (PRD "API design"): the OpenAPI schema the frontend's types are
generated from."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from trip_feasibility.checks.config import CheckConfig
from trip_feasibility.itinerary import OsmRef, Pace, Stop, TransportMode
from trip_feasibility.parse.clean import MAX_RAW_CHARS
from trip_feasibility.parse.schema import ExtractedStop

MAX_STOPS = 200

# --- stops ---


class StopIn(ExtractedStop):
    """A stop as the traveller confirmed it (parse step 5). Keep osm/lat/lon from the parse
    result for stops the traveller didn't change; leave them out for edited ones and they are
    looked up when the check runs."""

    osm: OsmRef | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _both_coordinates(self):
        if (self.lat is None) != (self.lon is None):
            raise ValueError("give both lat and lon, or neither")
        return self


class ParsedStop(Stop):
    resolved: bool
    outside_city: bool = False
    reason: str | None = None  # why it's unresolved


# --- parsing ---


class ParseRequest(BaseModel):
    # The parse pipeline gives the clear "too long" error; this cap only bounds the body.
    text: str = Field(min_length=1, max_length=2 * MAX_RAW_CHARS)
    city: str = Field(min_length=1, max_length=100)
    start_date: date


class UsageOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    models: list[str]


class ParseResponse(BaseModel):
    city: str
    country_code: str | None
    text: str  # what extraction read, after chat-interface debris was removed
    stops: list[ParsedStop]
    unresolved: list[str]  # stop ids
    model: str
    prompt_version: str
    usage: UsageOut


# --- itineraries ---


class ItineraryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    start_date: date
    raw_text: str = Field(default="", max_length=MAX_RAW_CHARS)
    stops: list[StopIn] = Field(min_length=1, max_length=MAX_STOPS)


class ItineraryPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class VersionCreate(BaseModel):
    stops: list[StopIn] = Field(min_length=1, max_length=MAX_STOPS)


class RunSummary(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    blockers: int
    warnings: int


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    source: Literal["user", "fix"]
    created_at: datetime
    raw_text: str
    stops: list[Stop]
    transport_modes: list[TransportMode] | None
    runs: list[RunSummary] = []


class ItinerarySummary(BaseModel):
    id: uuid.UUID
    title: str
    city: str
    start_date: date
    created_at: datetime
    versions: int
    latest_version_id: uuid.UUID
    latest_run: RunSummary | None


class ItineraryDetail(BaseModel):
    id: uuid.UUID
    title: str
    city: str
    start_date: date
    created_at: datetime
    versions: list[VersionOut]  # newest first


# --- check runs ---


class CheckStart(BaseModel):
    """Exactly one of: a saved version (signed in) or a demo sample (no sign-in)."""

    version_id: uuid.UUID | None = None
    sample_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.version_id is None) == (self.sample_id is None):
            raise ValueError("give either version_id or sample_id")
        return self


class CheckStarted(BaseModel):
    run_id: uuid.UUID
    demo: bool
    events_url: str
    result_url: str


class ViolationOut(BaseModel):
    id: uuid.UUID
    check_name: str
    severity: Literal["info", "warning", "blocker"]
    stop_ids: list[str]
    message: str
    evidence: dict[str, Any]
    source: str


class FixOut(BaseModel):
    id: uuid.UUID
    violation_ids: list[uuid.UUID]
    description: str
    operations: list[dict[str, Any]]
    stops: list[Stop]  # the itinerary with the fix applied
    transport_modes: list[TransportMode]
    new_warnings: list[str]
    unverified: list[str]
    status: Literal["proposed", "accepted", "rejected"]


class CheckResultOut(BaseModel):
    status: Literal["ok", "unavailable"]
    violations: int
    unknown: list[dict[str, str]]
    checked_stop_ids: list[str]
    notes: list[str]
    error: str | None


class RunOut(BaseModel):
    id: uuid.UUID
    status: Literal["queued", "running", "done", "error"]
    demo: bool
    version_id: uuid.UUID | None
    error: str | None = None
    city: str | None = None
    stops: list[Stop] = []
    results: dict[str, CheckResultOut] = {}
    violations: list[ViolationOut] = []
    fixes: list[FixOut] = []  # valid fixes only
    unfixed: list[uuid.UUID] = []  # warnings/blockers without a valid fix
    fix_error: str | None = None
    fixes_enabled: bool = False
    usage: UsageOut | None = None
    latency_ms: int | None = None
    trace_id: str | None = None


class SampleOut(BaseModel):
    id: str
    title: str
    city: str
    start_date: date
    text: str


# --- account ---


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    auth_provider: str


class PreferencesIO(BaseModel):
    pace: Pace = "moderate"
    max_walk_km: float = Field(default=10.0, gt=0, le=100)
    transport_modes: list[TransportMode] = Field(default=["walk", "transit"], min_length=1)
    daily_budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


CheckConfigIO = CheckConfig


class NewVersion(BaseModel):
    itinerary_id: uuid.UUID
    version: VersionOut
