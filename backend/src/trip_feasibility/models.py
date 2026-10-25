"""Database schema (docs/PRD.md, "Data model").

Hotels and their reviews come from the Kaggle "515K Hotel Reviews Data in Europe" set, loaded
by scripts/load_reviews.py: only the four dataset cities, and only reviews with a negative
part (the only part that can evidence a red flag), embedded with REVIEW_EMBEDDING_MODEL.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PACES = ("relaxed", "moderate", "packed")
# bge-small-en-v1.5: 384 dimensions, stored as halfvec (half precision) to halve the size.
REVIEW_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
REVIEW_EMBEDDING_DIM = 384
VERSION_SOURCES = ("user", "fix")
RUN_STATUSES = ("queued", "running", "done", "error")
SEVERITIES = ("info", "warning", "blocker")
FIX_STATUSES = ("proposed", "accepted", "rejected")


def one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_valid")


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _owner(target: str, *, primary_key: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        ForeignKey(target, ondelete="CASCADE"), primary_key=primary_key, index=not primary_key
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    auth_provider: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = _created_at()


class Preferences(Base):
    __tablename__ = "preferences"
    __table_args__ = (one_of("pace", PACES),)

    user_id: Mapped[uuid.UUID] = _owner("users.id", primary_key=True)
    pace: Mapped[str] = mapped_column(String(20), default="moderate")
    max_walk_km: Mapped[float] = mapped_column(default=10.0)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    transport_modes: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)


class CheckConfig(Base):
    __tablename__ = "check_configs"

    user_id: Mapped[uuid.UUID] = _owner("users.id", primary_key=True)
    check_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    strictness: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Itinerary(Base):
    __tablename__ = "itineraries"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = _owner("users.id")
    title: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = _created_at()


class ItineraryVersion(Base):
    __tablename__ = "itinerary_versions"
    __table_args__ = (
        UniqueConstraint("itinerary_id", "version_no"),
        one_of("source", VERSION_SOURCES),
    )

    id: Mapped[uuid.UUID] = _id()
    itinerary_id: Mapped[uuid.UUID] = _owner("itineraries.id")
    version_no: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    parsed_stops: Mapped[list[Any]]
    source: Mapped[str] = mapped_column(String(10))
    # Set when an accepted fix allowed another travel mode; overrides the traveller's
    # preferences for this version only.
    transport_modes: Mapped[list[str] | None] = mapped_column(ARRAY(String(30)))
    created_at: Mapped[datetime] = _created_at()


class CheckRun(Base):
    __tablename__ = "check_runs"
    __table_args__ = (one_of("status", RUN_STATUSES),)

    id: Mapped[uuid.UUID] = _id()
    version_id: Mapped[uuid.UUID] = _owner("itinerary_versions.id")
    status: Mapped[str] = mapped_column(String(10), default="queued")
    config_snapshot: Mapped[dict[str, Any]]
    model_id: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None]
    trace_id: Mapped[str | None] = mapped_column(String(100))
    # Per-check status, unknown stops and notes, and the fix step's usage; violations and
    # fixes have their own tables.
    report: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Violation(Base):
    __tablename__ = "violations"
    __table_args__ = (one_of("severity", SEVERITIES),)

    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = _owner("check_runs.id")
    check_name: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10))
    stop_ids: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]]
    source: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)  # in the run's report: most severe first


class Fix(Base):
    """A proposed fix (agent/fixes.py). One fix can target several violations; invalid ones
    are kept too (fix validity is a metric), but only valid ones are shown or accepted."""

    __tablename__ = "fixes"
    __table_args__ = (one_of("status", FIX_STATUSES),)

    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = _owner("check_runs.id")
    violation_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    description: Mapped[str] = mapped_column(Text)
    patched_stops: Mapped[list[Any]]
    valid: Mapped[bool]
    # operations, transport_modes, new_warnings, unverified, problems, round
    details: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(10), default="proposed")


class ApiCache(Base):
    __tablename__ = "api_cache"

    key: Mapped[str] = mapped_column(String(500), primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    response: Mapped[dict[str, Any]]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _id()
    dataset_version: Mapped[str] = mapped_column(String(50))
    model_id: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    metrics: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _created_at()


class Hotel(Base):
    __tablename__ = "hotels"
    __table_args__ = (UniqueConstraint("name", "address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # as in the CSV (accents are mangled)
    address: Mapped[str] = mapped_column(String(300))
    city: Mapped[str] = mapped_column(String(50), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    average_score: Mapped[Decimal] = mapped_column(Numeric(3, 1))
    total_reviews: Mapped[int] = mapped_column(Integer)  # on Booking.com when scraped


class HotelReview(Base):
    """One review's negative part. Searched per hotel (a few thousand rows at most), so an
    exact scan is fast and there is no vector index to keep in memory."""

    __tablename__ = "hotel_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    review_date: Mapped[date] = mapped_column(Date)
    reviewer_score: Mapped[Decimal] = mapped_column(Numeric(3, 1))
    negative_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(HALFVEC(REVIEW_EMBEDDING_DIM))
