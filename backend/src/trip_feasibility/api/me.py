"""The signed-in traveller: preferences (FR-4), check settings (FR-5), account deletion."""

from fastapi import APIRouter, Response
from sqlalchemy import delete as sql_delete
from sqlalchemy.dialects.postgresql import insert

from trip_feasibility import models
from trip_feasibility.api.common import (
    STRICTNESS_KEYS,
    Session,
    load_check_config,
    load_preferences,
)
from trip_feasibility.api.schemas import CheckConfigIO, MeOut, PreferencesIO
from trip_feasibility.auth import CurrentUser
from trip_feasibility.checks.config import CHECK_NAMES

router = APIRouter(prefix="/me", tags=["account"])


@router.get("")
async def me(user: CurrentUser) -> MeOut:
    return MeOut(id=user.id, email=user.email, name=user.name, auth_provider=user.auth_provider)


@router.get("/preferences")
async def get_preferences(user: CurrentUser, session: Session) -> PreferencesIO:
    row = await load_preferences(session, user.id)
    if row is None:
        return PreferencesIO()
    return PreferencesIO(
        pace=row.pace,  # type: ignore[arg-type]
        max_walk_km=row.max_walk_km,
        transport_modes=row.transport_modes or PreferencesIO().transport_modes,  # type: ignore[arg-type]
        daily_budget=row.daily_budget,
        currency=row.currency,
    )


@router.put("/preferences")
async def put_preferences(
    body: PreferencesIO, user: CurrentUser, session: Session
) -> PreferencesIO:
    values = body.model_dump()
    stmt = insert(models.Preferences).values(user_id=user.id, **values)
    stmt = stmt.on_conflict_do_update(index_elements=[models.Preferences.user_id], set_=values)
    await session.execute(stmt)
    await session.commit()
    return body


@router.get("/check-config")
async def get_check_config(user: CurrentUser, session: Session) -> CheckConfigIO:
    return await load_check_config(session, user.id)


@router.put("/check-config")
async def put_check_config(
    body: CheckConfigIO, user: CurrentUser, session: Session
) -> CheckConfigIO:
    settings = body.model_dump()
    rows = [
        {
            "user_id": user.id,
            "check_name": name,
            "enabled": name in body.enabled,
            "strictness": {STRICTNESS_KEYS[name]: settings[STRICTNESS_KEYS[name]]}
            if name in STRICTNESS_KEYS
            else {},
        }
        for name in CHECK_NAMES
    ]
    stmt = insert(models.CheckConfig).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[models.CheckConfig.user_id, models.CheckConfig.check_name],
        set_={"enabled": stmt.excluded.enabled, "strictness": stmt.excluded.strictness},
    )
    await session.execute(stmt)
    await session.commit()
    return body


@router.delete("", status_code=204)
async def delete_account(user: CurrentUser, session: Session) -> Response:
    """Deletes the user and, by ON DELETE CASCADE, every itinerary, version, run, violation,
    fix, preference and setting they own."""
    await session.execute(sql_delete(models.User).where(models.User.id == user.id))
    await session.commit()
    return Response(status_code=204)
