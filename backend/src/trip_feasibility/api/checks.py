"""Check runs (PRD: POST /checks, GET /checks/{id}/events, GET /checks/{id}) and demo samples.

A signed-in caller checks a saved itinerary version with their own preferences and check
settings; the run is saved. Anyone can check a demo sample with the default settings; the
run lives in memory only (PRD FR-6: no sign-in, nothing saved). Demo run ids are random
UUIDs, so only whoever started one can find it.

Browsers' EventSource can't send an Authorization header: the frontend's server proxies
/events and adds the token (as for every other call).
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from trip_feasibility import models
from trip_feasibility.api.common import (
    Session,
    Svc,
    client_ip,
    load_check_config,
    load_preferences,
    not_found,
    owned_run,
    owned_version,
    travel_preferences,
)
from trip_feasibility.api.schemas import CheckStart, CheckStarted, RunOut, SampleOut
from trip_feasibility.auth import OptionalUser
from trip_feasibility.checks.config import CheckConfig
from trip_feasibility.demo import samples
from trip_feasibility.errors import AppError
from trip_feasibility.itinerary import Stop
from trip_feasibility.models import User
from trip_feasibility.parse.normalise import normalise
from trip_feasibility.runs import LiveRun, RunFailed, RunRequest

router = APIRouter(tags=["checks"])


@router.get("/demo/samples")
async def demo_samples() -> list[SampleOut]:
    return [SampleOut.model_validate(s.model_dump()) for s in samples().values()]


@router.post("/checks", status_code=202)
async def start_check(
    body: CheckStart, request: Request, user: OptionalUser, session: Session, svc: Svc
) -> CheckStarted:
    if body.sample_id is not None:
        sample = samples().get(body.sample_id)
        if sample is None:
            raise not_found("demo sample")
        svc.demo_limit.hit(client_ip(request))
        req = RunRequest(
            stops=normalise(sample.stops, sample.start_date),  # located when it runs
            city=sample.city,
            preferences=sample.preferences,
            config=CheckConfig(),
        )
    else:
        if user is None:
            raise AppError(401, "unauthorized", "Sign in to check a saved itinerary")
        assert body.version_id is not None
        version, itinerary = await owned_version(session, user, body.version_id)
        svc.check_limit.hit(str(user.id))
        preferences = travel_preferences(await load_preferences(session, user.id))
        if version.transport_modes:  # an accepted fix allowed another way to travel
            preferences = preferences.model_copy(
                update={"transport_modes": version.transport_modes}
            )
        req = RunRequest(
            stops=[Stop.model_validate(s) for s in version.parsed_stops],
            city=itinerary.city,
            preferences=preferences,
            config=await load_check_config(session, user.id),
            owner_id=user.id,
            version_id=version.id,
        )
    try:
        run = await svc.runs.start(req)
    except RunFailed as e:
        raise AppError(503, e.code, e.message) from e
    base = f"{request.scope.get('root_path', '')}/api/v1/checks/{run.id}"
    return CheckStarted(run_id=run.id, demo=run.demo, events_url=f"{base}/events", result_url=base)


def _visible(run: LiveRun, user: User | None) -> bool:
    return run.owner_id is None or (user is not None and user.id == run.owner_id)


def _frame(event: dict[str, Any], index: int | None = None) -> str:
    head = f"id: {index}\n" if index is not None else ""
    return f"{head}event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


async def _follow(run: LiveRun, after: int) -> AsyncIterator[str]:
    async for item in run.follow(after):
        if item is None:
            yield ": keepalive\n\n"
        else:
            yield _frame(item[1], item[0])


async def _run_from_db(session: Any, run: models.CheckRun) -> RunOut:
    report = run.report or {}
    violations = (
        await session.scalars(
            select(models.Violation)
            .where(models.Violation.run_id == run.id)
            .order_by(models.Violation.position)
        )
    ).all()
    fixes = (
        await session.scalars(
            select(models.Fix).where(models.Fix.run_id == run.id, models.Fix.valid)
        )
    ).all()
    return RunOut.model_validate(
        {
            "id": run.id,
            "status": run.status,
            "demo": False,
            "version_id": run.version_id,
            "error": run.error,
            "city": report.get("city"),
            "stops": report.get("stops", []),
            "results": report.get("results", {}),
            "violations": [
                {
                    "id": v.id,
                    "check_name": v.check_name,
                    "severity": v.severity,
                    "stop_ids": v.stop_ids,
                    "message": v.message,
                    "evidence": v.evidence,
                    "source": v.source,
                }
                for v in violations
            ],
            "fixes": [
                {
                    "id": f.id,
                    "violation_ids": f.violation_ids,
                    "description": f.description,
                    "stops": f.patched_stops,
                    "status": f.status,
                    **{
                        k: f.details[k]
                        for k in ("operations", "transport_modes", "new_warnings", "unverified")
                    },
                }
                for f in fixes
            ],
            "unfixed": report.get("unfixed", []),
            "fix_error": report.get("fix_error"),
            "fixes_enabled": report.get("fixes_enabled", False),
            "usage": report.get("usage"),
            "latency_ms": run.latency_ms,
            "trace_id": run.trace_id,
        }
    )


def _live_out(run: LiveRun) -> RunOut:
    if run.result is not None:
        return RunOut.model_validate(run.result)
    error = next((e for e in reversed(run.events) if e["type"] == "error"), None)
    return RunOut(
        id=run.id,
        status="error" if error else "running",
        demo=run.demo,
        version_id=run.version_id,
        error=f"{error['code']}: {error['message']}" if error else None,
    )


@router.get("/checks/{run_id}/events")
async def run_events(
    run_id: uuid.UUID, request: Request, user: OptionalUser, session: Session, svc: Svc
) -> StreamingResponse:
    """Server-sent events: everything so far, then each new event until done or error.
    Reconnecting with Last-Event-ID resumes after that event."""
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    live = svc.runs.get_live(run_id)
    if live is not None and _visible(live, user):
        try:
            after = int(request.headers.get("last-event-id", "-1"))
        except ValueError:
            after = -1
        return StreamingResponse(
            _follow(live, after), media_type="text/event-stream", headers=headers
        )
    saved = await owned_run(session, user, run_id) if user else None
    if saved is None:
        raise not_found("check run")
    out = await _run_from_db(session, saved)
    if saved.status == "done":
        event = {"type": "done", "run": out.model_dump(mode="json")}
    else:
        code, _, message = (saved.error or "interrupted: the run didn't finish").partition(": ")
        event = {"type": "error", "code": code, "message": message}

    async def once() -> AsyncIterator[str]:
        yield _frame(event)

    return StreamingResponse(once(), media_type="text/event-stream", headers=headers)


@router.get("/checks/{run_id}")
async def get_run(run_id: uuid.UUID, user: OptionalUser, session: Session, svc: Svc) -> RunOut:
    live = svc.runs.get_live(run_id)
    if live is not None and _visible(live, user):
        return _live_out(live)
    saved = await owned_run(session, user, run_id) if user else None
    if saved is None:
        raise not_found("check run")
    return await _run_from_db(session, saved)
