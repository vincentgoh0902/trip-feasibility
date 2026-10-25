"""Accepting or rejecting a proposed fix (PRD FR-3). Accepting creates a new itinerary
version from the checked version with that fix applied; nothing changes until then.

Each fix was re-checked against the version its run checked, so an accepted fix always
starts from that version: accepting two fixes from one run gives two separate versions.
To combine them, accept one and run the check again on the new version.
"""

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from trip_feasibility import models
from trip_feasibility.api.common import Session, next_version_no, not_found, version_out
from trip_feasibility.api.schemas import NewVersion
from trip_feasibility.auth import CurrentUser
from trip_feasibility.errors import AppError

router = APIRouter(prefix="/fixes", tags=["fixes"])


async def _owned_fix(
    session, user: models.User, fix_id: uuid.UUID
) -> tuple[models.Fix, models.ItineraryVersion]:
    row = (
        await session.execute(
            select(models.Fix, models.ItineraryVersion)
            .join(models.CheckRun, models.CheckRun.id == models.Fix.run_id)
            .join(models.ItineraryVersion)
            .join(models.Itinerary)
            .where(models.Fix.id == fix_id, models.Itinerary.user_id == user.id, models.Fix.valid)
        )
    ).first()
    if row is None:
        raise not_found("fix")
    return row[0], row[1]


@router.post("/{fix_id}/accept", status_code=201)
async def accept(fix_id: uuid.UUID, user: CurrentUser, session: Session) -> NewVersion:
    fix, checked = await _owned_fix(session, user, fix_id)
    if fix.status != "proposed":
        raise AppError(409, "conflict", f"This fix was already {fix.status}")
    allows_mode = any(op["op"] == "add_transport_mode" for op in fix.details["operations"])
    version = models.ItineraryVersion(
        id=uuid.uuid4(),
        itinerary_id=checked.itinerary_id,
        version_no=await next_version_no(session, checked.itinerary_id),
        raw_text=checked.raw_text,
        parsed_stops=fix.patched_stops,
        source="fix",
        transport_modes=fix.details["transport_modes"] if allows_mode else checked.transport_modes,
    )
    fix.status = "accepted"
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return NewVersion(itinerary_id=checked.itinerary_id, version=version_out(version))


@router.post("/{fix_id}/reject", status_code=204)
async def reject(fix_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    fix, _ = await _owned_fix(session, user, fix_id)
    if fix.status != "proposed":
        raise AppError(409, "conflict", f"This fix was already {fix.status}")
    fix.status = "rejected"
    await session.commit()
