"""Shared by the routes: app-wide services, ownership checks, and conversions between rows
and API bodies. Every query for user data filters by the caller's id; another user's
itinerary is a 404, not a 403, so ids can't be probed."""

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trip_feasibility import models
from trip_feasibility.api.schemas import RunSummary, StopIn, VersionOut
from trip_feasibility.checks.config import CHECK_NAMES, CheckConfig
from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.config import Settings
from trip_feasibility.db import get_session
from trip_feasibility.errors import AppError
from trip_feasibility.itinerary import Stop, TravelPreferences
from trip_feasibility.parse.normalise import normalise
from trip_feasibility.ratelimit import SlidingWindowLimiter
from trip_feasibility.runs import RunService
from trip_feasibility.tracing import Tracer


@dataclass
class Services:
    settings: Settings
    deps: CheckDeps
    runs: RunService
    tracer: Tracer
    llm: Any | None  # anthropic.AsyncAnthropic, None without ANTHROPIC_API_KEY
    parse_limit: SlidingWindowLimiter
    check_limit: SlidingWindowLimiter
    demo_limit: SlidingWindowLimiter


def services(request: Request) -> Services:
    return request.app.state.services


Svc = Annotated[Services, Depends(services)]
Session = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers sets this from X-Forwarded-For when the proxy is trusted
    # (FORWARDED_ALLOW_IPS); otherwise it is the proxy's own address.
    return request.client.host if request.client else "unknown"


def not_found(what: str) -> AppError:
    return AppError(404, "not_found", f"No such {what}")


# --- stops ---


def stops_from_input(stops: list[StopIn], trip_start: Any) -> list[Stop]:
    """Confirmed stops → Stop, as parse step 3 does it (ids s1, s2, ...; day → date; time of
    day → its window), keeping the coordinates the traveller didn't change."""
    normalised = normalise(stops, trip_start)
    return [
        s.model_copy(update={"osm": given.osm, "lat": given.lat, "lon": given.lon})
        for s, given in zip(normalised, stops, strict=True)
    ]


# --- ownership ---


async def owned_itinerary(
    session: AsyncSession, user: models.User, itinerary_id: uuid.UUID
) -> models.Itinerary:
    it = await session.scalar(
        select(models.Itinerary).where(
            models.Itinerary.id == itinerary_id, models.Itinerary.user_id == user.id
        )
    )
    if it is None:
        raise not_found("itinerary")
    return it


async def owned_version(
    session: AsyncSession, user: models.User, version_id: uuid.UUID
) -> tuple[models.ItineraryVersion, models.Itinerary]:
    row = (
        await session.execute(
            select(models.ItineraryVersion, models.Itinerary)
            .join(models.Itinerary)
            .where(
                models.ItineraryVersion.id == version_id,
                models.Itinerary.user_id == user.id,
            )
        )
    ).first()
    if row is None:
        raise not_found("itinerary version")
    return row[0], row[1]


async def owned_run(
    session: AsyncSession, user: models.User, run_id: uuid.UUID
) -> models.CheckRun | None:
    return await session.scalar(
        select(models.CheckRun)
        .join(models.ItineraryVersion)
        .join(models.Itinerary)
        .where(models.CheckRun.id == run_id, models.Itinerary.user_id == user.id)
    )


async def next_version_no(session: AsyncSession, itinerary_id: uuid.UUID) -> int:
    latest = await session.scalar(
        select(func.max(models.ItineraryVersion.version_no)).where(
            models.ItineraryVersion.itinerary_id == itinerary_id
        )
    )
    return (latest or 0) + 1


# --- conversions ---


async def run_summaries(
    session: AsyncSession, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[RunSummary]]:
    """Runs per version, newest first, with their blocker and warning counts."""
    if not version_ids:
        return {}
    counts = (
        select(
            models.Violation.run_id,
            func.count().filter(models.Violation.severity == "blocker").label("blockers"),
            func.count().filter(models.Violation.severity == "warning").label("warnings"),
        )
        .group_by(models.Violation.run_id)
        .subquery()
    )
    rows = await session.execute(
        select(models.CheckRun, counts.c.blockers, counts.c.warnings)
        .outerjoin(counts, counts.c.run_id == models.CheckRun.id)
        .where(models.CheckRun.version_id.in_(version_ids))
        .order_by(models.CheckRun.created_at.desc())
    )
    out: dict[uuid.UUID, list[RunSummary]] = {}
    for run, blockers, warnings in rows:
        out.setdefault(run.version_id, []).append(
            RunSummary(
                id=run.id,
                status=run.status,
                created_at=run.created_at,
                blockers=blockers or 0,
                warnings=warnings or 0,
            )
        )
    return out


def version_out(v: models.ItineraryVersion, runs: list[RunSummary] | None = None) -> VersionOut:
    return VersionOut(
        id=v.id,
        version_no=v.version_no,
        source=v.source,  # type: ignore[arg-type]
        created_at=v.created_at,
        raw_text=v.raw_text,
        stops=[Stop.model_validate(s) for s in v.parsed_stops],
        transport_modes=v.transport_modes,  # type: ignore[arg-type]
        runs=runs or [],
    )


# --- the caller's settings ---


async def load_preferences(session: AsyncSession, user_id: uuid.UUID) -> models.Preferences | None:
    return await session.get(models.Preferences, user_id)


def travel_preferences(row: models.Preferences | None) -> TravelPreferences:
    if row is None:
        return TravelPreferences()
    return TravelPreferences(
        pace=row.pace,  # type: ignore[arg-type]
        max_walk_km=row.max_walk_km,
        transport_modes=row.transport_modes or TravelPreferences().transport_modes,  # type: ignore[arg-type]
    )


# check_configs rows: one per check; strictness JSON holds that check's setting
STRICTNESS_KEYS = {"travel_time": "travel_buffer_min", "hotel_red_flags": "hotel_min_reviews"}


async def load_check_config(session: AsyncSession, user_id: uuid.UUID) -> CheckConfig:
    rows = (
        await session.scalars(
            select(models.CheckConfig).where(models.CheckConfig.user_id == user_id)
        )
    ).all()
    if not rows:
        return CheckConfig()
    by_name = {r.check_name: r for r in rows}
    values: dict[str, Any] = {
        "enabled": [n for n in CHECK_NAMES if n not in by_name or by_name[n].enabled]
    }
    for check, key in STRICTNESS_KEYS.items():
        if check in by_name and key in by_name[check].strictness:
            values[key] = by_name[check].strictness[key]
    return CheckConfig.model_validate(values)
