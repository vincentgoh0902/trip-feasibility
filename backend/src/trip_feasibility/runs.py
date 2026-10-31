"""Check runs started from the API: run in the background, stream progress, save the result.

A run is an asyncio task owned by `RunService`, not by the request, so a dropped browser
connection doesn't stop it (PRD: long work runs in a background task). While it runs, its
events are kept in memory (`LiveRun`): the SSE route replays them from the start (or from
Last-Event-ID) and then follows, so a late or reconnecting subscriber misses nothing.

Saved runs (signed-in, from an itinerary version) are written to check_runs, violations and
fixes when they finish, then dropped from memory; the database is their record. Demo runs
are never written anywhere and expire from memory after DEMO_RUN_TTL_S.

Events (`type` field, PRD "API design"):
    step_started  {"step": "geocode" | "checks" | "fixes", ...}
    tool_result   one check finished: status, counts, unknown stops, notes
    violation     one violation, with the id fixes refer to
    fix           one valid fix (re-checked; invalid ones are saved but not shown)
    done          the full result, as GET /checks/{id} returns it
    error         {"code", "message"}: the run failed as a whole
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from mcp import Client
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trip_feasibility import models
from trip_feasibility.agent.fixes import FIX_PROMPT_VERSION, FixReport
from trip_feasibility.agent.graph import CheckInput, CheckReport, Fixer, stream_agent
from trip_feasibility.checks.config import CHECK_NAMES, CheckConfig
from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.checks.models import CheckResult
from trip_feasibility.config import Settings
from trip_feasibility.itinerary import Stop, TravelPreferences
from trip_feasibility.mcp_server import create_server
from trip_feasibility.parse.geocode import City, geocode_place, locate_city
from trip_feasibility.tracing import TracedClient, TracedMcp, Tracer, Usage

log = logging.getLogger(__name__)

KEEPALIVE_S = 15.0
MAX_DEMO_RUNS = 500


class RunFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class RunRequest:
    stops: list[Stop]
    city: str
    preferences: TravelPreferences
    config: CheckConfig
    owner_id: uuid.UUID | None = None  # None: a demo run
    version_id: uuid.UUID | None = None
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)

    @property
    def demo(self) -> bool:
        return self.version_id is None


@dataclass
class LiveRun:
    id: uuid.UUID
    owner_id: uuid.UUID | None
    demo: bool
    version_id: uuid.UUID | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    result: dict[str, Any] | None = None  # the done event's run, or None on error
    expires_at: float | None = None
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def publish(self, event: dict[str, Any], *, final: bool = False) -> None:
        async with self._changed:
            self.events.append(event)
            self.finished = self.finished or final
            self._changed.notify_all()

    async def follow(self, after: int = -1) -> AsyncIterator[tuple[int, dict[str, Any]] | None]:
        """(index, event) from index after+1 on, then each new one as it comes; None every
        KEEPALIVE_S while nothing happens. Stops after the final event."""
        i = after + 1
        while True:
            async with self._changed:
                if i >= len(self.events) and not self.finished:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._changed.wait(), KEEPALIVE_S)
                pending = self.events[i:]
                finished = self.finished
            if not pending and not finished:
                yield None
            for event in pending:
                yield i, event
                i += 1
            if finished and i >= len(self.events):
                return


# What a run streams, so tests can stand in for the agent: (mcp, deps, input, fixer) -> events
AgentStream = Callable[..., AsyncIterator[Any]]


def _violation_json(v_id: uuid.UUID, v: Any) -> dict[str, Any]:
    return {"id": str(v_id), **v.model_dump(mode="json")}


def _result_summary(r: CheckResult) -> dict[str, Any]:
    return {
        "status": r.status,
        "violations": len(r.violations),
        "unknown": [u.model_dump() for u in r.unknown],
        "checked_stop_ids": r.checked_stop_ids,
        "notes": r.notes,
        "error": r.error,
    }


class RunService:
    def __init__(
        self,
        settings: Settings,
        deps: CheckDeps,
        sessionmaker: async_sessionmaker[AsyncSession],
        tracer: Tracer,
        llm: Any | None,  # anthropic.AsyncAnthropic, or None without a key (no fixes)
        stream: AgentStream = stream_agent,
    ) -> None:
        self.settings, self.deps, self.sessionmaker = settings, deps, sessionmaker
        self.tracer, self.llm, self.stream = tracer, llm, stream
        self.live: dict[uuid.UUID, LiveRun] = {}
        self._tasks: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(settings.max_concurrent_runs)

    @property
    def fixes_on(self) -> bool:
        return self.llm is not None

    # --- starting ---

    async def start(self, req: RunRequest) -> LiveRun:
        self._expire()
        if req.demo and sum(r.demo for r in self.live.values()) >= MAX_DEMO_RUNS:
            raise RunFailed("busy", "Too many demo runs at the moment; try again shortly")
        if not req.demo:
            async with self.sessionmaker() as session:
                session.add(
                    models.CheckRun(
                        id=req.run_id,
                        version_id=req.version_id,
                        status="queued",
                        config_snapshot={
                            "city": req.city,
                            "config": req.config.model_dump(mode="json"),
                            "preferences": req.preferences.model_dump(mode="json"),
                        },
                        model_id=self.settings.fix_model if self.fixes_on else "none",
                        prompt_version=FIX_PROMPT_VERSION if self.fixes_on else "none",
                    )
                )
                await session.commit()
        run = LiveRun(
            id=req.run_id, owner_id=req.owner_id, demo=req.demo, version_id=req.version_id
        )
        self.live[run.id] = run
        task = asyncio.create_task(self._execute(req, run), name=f"check-run-{run.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def get_live(self, run_id: uuid.UUID) -> LiveRun | None:
        self._expire()
        return self.live.get(run_id)

    def _expire(self) -> None:
        now = time.monotonic()
        for rid in [k for k, r in self.live.items() if r.expires_at and r.expires_at < now]:
            del self.live[rid]

    # --- running ---

    async def _execute(self, req: RunRequest, run: LiveRun) -> None:
        started = time.monotonic()
        usage = Usage()
        trace_id: str | None = None
        try:
            async with self._slots:
                await self._set_status(req, "running")
                content = self.tracer.capture_content
                with self.tracer.observe(
                    "check_run",
                    as_type="agent",
                    input={"stops": [s.model_dump(mode="json") for s in req.stops]}
                    if content
                    else None,
                    metadata={
                        "run_id": str(req.run_id),
                        "demo": req.demo,
                        "city": req.city,
                        "stops": len(req.stops),
                        "checks": req.config.enabled,
                    },
                ) as root:
                    trace_id = root.trace_id
                    result = await self._run(req, run, usage)
                    result["latency_ms"] = round((time.monotonic() - started) * 1000)
                    result["trace_id"] = trace_id
                    root.update(
                        output={
                            "violations": len(result["violations"]),
                            "fixes": len(result["fixes"]),
                            "cost_usd": result["usage"]["cost_usd"],
                        }
                    )
            if not req.demo:
                await self._save(req, result, usage)
            run.result = public(result)
            await run.publish({"type": "done", "run": run.result}, final=True)
        except asyncio.CancelledError:
            await self._fail(req, run, "interrupted", "The server stopped during this run")
            raise
        except RunFailed as e:
            await self._fail(req, run, e.code, e.message)
        except Exception as e:  # the run's own failure, reported, never a crashed task
            log.exception("check run %s failed", req.run_id)
            await self._fail(req, run, "internal", f"The run failed: {type(e).__name__}")
        finally:
            if run.demo:
                run.expires_at = time.monotonic() + self.settings.demo_run_ttl_s
            else:
                self.live.pop(run.id, None)

    async def _run(self, req: RunRequest, run: LiveRun, usage: Usage) -> dict[str, Any]:
        try:
            city = await locate_city(self.deps, req.city)
        except Unavailable as e:
            raise RunFailed("unavailable", f"Couldn't look up the city: {e}") from e
        if city is None:
            raise RunFailed("city_not_found", f"Couldn't find the city {req.city!r}")
        stops = await self._locate(req.stops, city, run)
        inp = CheckInput(
            stops=stops,
            city=city.name,
            country_code=city.country_code.upper() if city.country_code else None,
            regions=city.regions,
            preferences=req.preferences,
            config=req.config,
        )
        enabled = [n for n in CHECK_NAMES if n in req.config.enabled]
        # The located stops go out with the first checks event, so the live feed can lay out
        # the itinerary before the first result arrives.
        await run.publish(
            {
                "type": "step_started",
                "step": "checks",
                "checks": enabled,
                "city": city.name,
                "stops": [s.model_dump(mode="json") for s in stops],
            }
        )

        fixer = None
        if self.llm is not None:
            client = TracedClient(self.llm, self.tracer, usage, "fixes")
            fixer = Fixer(
                client=client,
                model=self.settings.fix_model,
                effort=self.settings.fix_effort,
                city=city,
            )
        results: dict[str, CheckResult] = {}
        violation_ids: dict[int, uuid.UUID] = {}  # id(Violation) -> its id
        fix_report: FixReport | None = None
        async with Client(create_server(self.deps)) as mcp:
            traced = TracedMcp(mcp, self.tracer)
            async for kind, value in self.stream(traced, self.deps, inp, fixer):
                if kind == "check":
                    results[value.check_name] = value
                    await run.publish(
                        {"type": "tool_result", "check": value.check_name} | _result_summary(value)
                    )
                    for v in value.violations:
                        violation_ids[id(v)] = uuid.uuid4()
                        await run.publish(
                            {
                                "type": "violation",
                                "violation": _violation_json(violation_ids[id(v)], v),
                            }
                        )
                    if len(results) == len(enabled) and fixer is not None:
                        report = CheckReport.from_results(inp, results)
                        if any(v.severity != "info" for v in report.violations):
                            await run.publish({"type": "step_started", "step": "fixes"})
                elif kind == "fixes":
                    fix_report = value

        report = CheckReport.from_results(inp, results)
        by_index = [violation_ids[id(v)] for v in report.violations]
        fixes = self._fixes(fix_report, by_index)
        for f in fixes:
            if f["valid"]:
                await run.publish({"type": "fix", "fix": f})
        return {
            "id": str(req.run_id),
            "status": "done",
            "demo": req.demo,
            "version_id": str(req.version_id) if req.version_id else None,
            "city": city.name,
            "stops": [s.model_dump(mode="json") for s in stops],
            "preferences": req.preferences.model_dump(mode="json"),
            "config": req.config.model_dump(mode="json"),
            "results": {n: _result_summary(r) for n, r in report.results.items()},
            "violations": [
                _violation_json(vid, v) for vid, v in zip(by_index, report.violations, strict=True)
            ],
            "fixes": [f for f in fixes if f["valid"]],
            "unfixed": [str(by_index[fix_report.violation_ids[v]]) for v in fix_report.unfixed]
            if fix_report
            else [],
            "fix_error": fix_report.error if fix_report else None,
            "fixes_enabled": fixer is not None,
            "usage": usage.as_dict(),
            "_all_fixes": fixes,  # saved, not returned (see _public)
        }

    async def _locate(self, stops: list[Stop], city: City, run: LiveRun) -> list[Stop]:
        """Stops saved without coordinates (edited by the user) are geocoded now."""
        missing = [s for s in stops if s.lat is None or s.lon is None]
        if not missing:
            return stops
        await run.publish(
            {"type": "step_started", "step": "geocode", "stop_ids": [s.id for s in missing]}
        )
        found: dict[str, Stop] = {}
        for s in missing:
            try:
                g = await geocode_place(self.deps, s.place_name, city)
            except Unavailable:
                continue  # stays unlocated: the checks report it as unknown
            if g.place is not None:
                found[s.id] = s.model_copy(
                    update={"osm": g.place.osm, "lat": g.place.lat, "lon": g.place.lon}
                )
        return [found.get(s.id, s) for s in stops]

    @staticmethod
    def _fixes(report: FixReport | None, by_index: list[uuid.UUID]) -> list[dict[str, Any]]:
        if report is None:
            return []
        out = []
        for f in report.fixes:
            targets = [
                str(by_index[report.violation_ids[v]])
                for v in f.violation_ids
                if v in report.violation_ids
            ]
            out.append(
                {
                    "id": str(uuid.uuid4()),
                    "violation_ids": targets,
                    "description": f.description,
                    "valid": f.valid,
                    "operations": [
                        o.model_dump(mode="json", exclude_none=True) for o in f.operations
                    ],
                    "stops": [s.model_dump(mode="json") for s in f.stops],
                    "transport_modes": f.transport_modes,
                    "new_warnings": f.new_warnings,
                    "unverified": f.unverified,
                    "problems": f.problems,
                    "round": f.round,
                    "status": "proposed",
                }
            )
        return out

    # --- saving ---

    async def _set_status(self, req: RunRequest, status: str) -> None:
        if req.demo:
            return
        async with self.sessionmaker() as session:
            await session.execute(
                update(models.CheckRun)
                .where(models.CheckRun.id == req.run_id)
                .values(status=status)
            )
            await session.commit()

    async def _save(self, req: RunRequest, result: dict[str, Any], usage: Usage) -> None:
        all_fixes = result["_all_fixes"]
        async with self.sessionmaker() as session:
            for position, v in enumerate(result["violations"]):
                session.add(
                    models.Violation(
                        id=uuid.UUID(v["id"]),
                        run_id=req.run_id,
                        check_name=v["check_name"],
                        severity=v["severity"],
                        stop_ids=v["stop_ids"],
                        message=v["message"],
                        evidence=v["evidence"],
                        source=v["source"],
                        position=position,
                    )
                )
            for f in all_fixes:
                session.add(
                    models.Fix(
                        id=uuid.UUID(f["id"]),
                        run_id=req.run_id,
                        violation_ids=[uuid.UUID(i) for i in f["violation_ids"]],
                        description=f["description"],
                        patched_stops=f["stops"],
                        valid=f["valid"],
                        details={
                            k: f[k]
                            for k in (
                                "operations",
                                "transport_modes",
                                "new_warnings",
                                "unverified",
                                "problems",
                                "round",
                            )
                        },
                    )
                )
            await session.flush()  # violations before the run update below
            await session.execute(
                update(models.CheckRun)
                .where(models.CheckRun.id == req.run_id)
                .values(
                    status="done",
                    report={
                        k: result[k]
                        for k in (
                            "city",
                            "stops",
                            "results",
                            "unfixed",
                            "fix_error",
                            "fixes_enabled",
                            "usage",
                        )
                    },
                    cost_usd=usage.cost_usd.quantize(Decimal("0.000001"))
                    if usage.cost_usd is not None
                    else None,
                    latency_ms=result["latency_ms"],
                    trace_id=result["trace_id"],
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def _fail(self, req: RunRequest, run: LiveRun, code: str, message: str) -> None:
        await run.publish({"type": "error", "code": code, "message": message}, final=True)
        if req.demo:
            return
        try:
            async with self.sessionmaker() as session:
                await session.execute(
                    update(models.CheckRun)
                    .where(models.CheckRun.id == req.run_id)
                    .values(
                        status="error", error=f"{code}: {message}", finished_at=datetime.now(UTC)
                    )
                )
                await session.commit()
        except Exception:
            log.exception("couldn't record the failure of run %s", req.run_id)

    # --- lifecycle ---

    async def mark_interrupted(self) -> None:
        """At startup: runs left queued/running by a previous process will never finish."""
        async with self.sessionmaker() as session:
            await session.execute(
                update(models.CheckRun)
                .where(models.CheckRun.status.in_(["queued", "running"]))
                .values(
                    status="error",
                    error="interrupted: the server restarted during this run",
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self.tracer.flush()


def public(result: dict[str, Any]) -> dict[str, Any]:
    """A run result as the API shows it (invalid fixes stay in the database only)."""
    return {k: v for k, v in result.items() if not k.startswith("_")}
