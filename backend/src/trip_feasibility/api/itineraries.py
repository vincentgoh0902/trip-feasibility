"""Parsing and saved itineraries (PRD FR-1, FR-6). Versions are append-only: an edit or an
accepted fix adds one, nothing is overwritten."""

import uuid

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from trip_feasibility import models
from trip_feasibility.api.common import (
    Session,
    Svc,
    next_version_no,
    owned_itinerary,
    run_summaries,
    stops_from_input,
    version_out,
)
from trip_feasibility.api.schemas import (
    ItineraryCreate,
    ItineraryDetail,
    ItineraryPatch,
    ItinerarySummary,
    ParsedStop,
    ParseRequest,
    ParseResponse,
    UsageOut,
    VersionCreate,
    VersionOut,
)
from trip_feasibility.auth import CurrentUser
from trip_feasibility.checks.deps import Unavailable
from trip_feasibility.errors import AppError
from trip_feasibility.parse.pipeline import ParseError, parse_itinerary
from trip_feasibility.tracing import TracedClient, Usage

router = APIRouter(prefix="/itineraries", tags=["itineraries"])


def _stops_json(stops: list, it: models.Itinerary) -> list[dict]:
    return [s.model_dump(mode="json") for s in stops_from_input(stops, it.start_date)]


# ParseError codes → HTTP status: the text's fault (422), or a service's (503)
_PARSE_STATUS = {
    "too_long": 422,
    "city_not_found": 422,
    "parse_failed": 422,
    "refused": 422,
    "truncated": 422,
}


@router.post("/parse")
async def parse(body: ParseRequest, user: CurrentUser, svc: Svc) -> ParseResponse:
    """Text → stops for the traveller to review (nothing is saved). Signed in only: the demo
    uses ready-made samples, so anonymous callers never spend on extraction."""
    if svc.llm is None:
        raise AppError(503, "unavailable", "Parsing isn't configured on this server")
    svc.parse_limit.hit(str(user.id))
    usage = Usage()
    with svc.tracer.observe(
        "parse",
        as_type="chain",
        metadata={"city": body.city, "chars": len(body.text)},
        input={"text": body.text} if svc.tracer.capture_content else None,
    ):
        client = TracedClient(svc.llm, svc.tracer, usage, "extraction")
        try:
            parsed = await parse_itinerary(
                svc.deps,
                client,
                svc.settings,
                body.text,
                city=body.city,
                trip_start=body.start_date,
            )
        except ParseError as e:
            raise AppError(_PARSE_STATUS.get(e.code, 503), e.code, e.message) from e
        except Unavailable as e:
            raise AppError(503, "unavailable", f"Couldn't look up places: {e}") from e
    stops = []
    for s in parsed.stops:
        g = parsed.geocodes[s.id]
        stops.append(
            ParsedStop(
                **s.model_dump(),
                resolved=g.place is not None,
                outside_city=g.outside_city,
                reason=g.reason,
            )
        )
    x = parsed.extraction
    return ParseResponse(
        city=parsed.city.name,
        country_code=parsed.city.country_code,
        text=parsed.text,
        stops=stops,
        unresolved=parsed.unresolved,
        model=x.model,
        prompt_version=x.prompt_version,
        usage=UsageOut(**usage.as_dict()),
    )


@router.post("", status_code=201)
async def create(body: ItineraryCreate, user: CurrentUser, session: Session) -> ItineraryDetail:
    it = models.Itinerary(
        id=uuid.uuid4(),
        user_id=user.id,
        title=body.title,
        city=body.city,
        start_date=body.start_date,
    )
    version = models.ItineraryVersion(
        id=uuid.uuid4(),
        itinerary_id=it.id,
        version_no=1,
        raw_text=body.raw_text,
        parsed_stops=[
            s.model_dump(mode="json") for s in stops_from_input(body.stops, it.start_date)
        ],
        source="user",
    )
    session.add(it)
    await session.flush()
    session.add(version)
    await session.commit()
    await session.refresh(it)
    await session.refresh(version)
    return ItineraryDetail(
        id=it.id,
        title=it.title,
        city=it.city,
        start_date=it.start_date,
        created_at=it.created_at,
        versions=[version_out(version)],
    )


@router.get("")
async def list_itineraries(user: CurrentUser, session: Session) -> list[ItinerarySummary]:
    latest = (
        select(
            models.ItineraryVersion.itinerary_id,
            func.count().label("versions"),
            func.max(models.ItineraryVersion.version_no).label("latest_no"),
        )
        .group_by(models.ItineraryVersion.itinerary_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(models.Itinerary, latest.c.versions, models.ItineraryVersion.id)
            .join(latest, latest.c.itinerary_id == models.Itinerary.id)
            .join(
                models.ItineraryVersion,
                (models.ItineraryVersion.itinerary_id == models.Itinerary.id)
                & (models.ItineraryVersion.version_no == latest.c.latest_no),
            )
            .where(models.Itinerary.user_id == user.id)
            .order_by(models.Itinerary.created_at.desc())
        )
    ).all()
    runs = await run_summaries(session, [version_id for _, _, version_id in rows])
    return [
        ItinerarySummary(
            id=it.id,
            title=it.title,
            city=it.city,
            start_date=it.start_date,
            created_at=it.created_at,
            versions=n,
            latest_version_id=version_id,
            latest_run=(runs.get(version_id) or [None])[0],
        )
        for it, n, version_id in rows
    ]


@router.get("/{itinerary_id}")
async def get_itinerary(
    itinerary_id: uuid.UUID, user: CurrentUser, session: Session
) -> ItineraryDetail:
    it = await owned_itinerary(session, user, itinerary_id)
    versions = (
        await session.scalars(
            select(models.ItineraryVersion)
            .where(models.ItineraryVersion.itinerary_id == it.id)
            .order_by(models.ItineraryVersion.version_no.desc())
        )
    ).all()
    runs = await run_summaries(session, [v.id for v in versions])
    return ItineraryDetail(
        id=it.id,
        title=it.title,
        city=it.city,
        start_date=it.start_date,
        created_at=it.created_at,
        versions=[version_out(v, runs.get(v.id)) for v in versions],
    )


@router.patch("/{itinerary_id}")
async def rename(
    itinerary_id: uuid.UUID, body: ItineraryPatch, user: CurrentUser, session: Session
) -> ItinerarySummary:
    it = await owned_itinerary(session, user, itinerary_id)
    it.title = body.title
    await session.commit()
    summaries = await list_itineraries(user, session)
    return next(s for s in summaries if s.id == it.id)


@router.delete("/{itinerary_id}", status_code=204)
async def delete(itinerary_id: uuid.UUID, user: CurrentUser, session: Session) -> Response:
    it = await owned_itinerary(session, user, itinerary_id)
    await session.delete(it)  # versions, runs, violations and fixes go with it (ON DELETE CASCADE)
    await session.commit()
    return Response(status_code=204)


@router.post("/{itinerary_id}/versions", status_code=201)
async def add_version(
    itinerary_id: uuid.UUID, body: VersionCreate, user: CurrentUser, session: Session
) -> VersionOut:
    it = await owned_itinerary(session, user, itinerary_id)
    previous = await session.scalar(
        select(models.ItineraryVersion)
        .where(models.ItineraryVersion.itinerary_id == it.id)
        .order_by(models.ItineraryVersion.version_no.desc())
        .limit(1)
    )
    version = models.ItineraryVersion(
        id=uuid.uuid4(),
        itinerary_id=it.id,
        version_no=await next_version_no(session, it.id),
        raw_text=previous.raw_text if previous else "",
        parsed_stops=[
            s.model_dump(mode="json") for s in stops_from_input(body.stops, it.start_date)
        ],
        source="user",
        transport_modes=previous.transport_modes if previous else None,
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return version_out(version)
