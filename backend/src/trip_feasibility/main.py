import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.api import router as api_router
from trip_feasibility.api.common import Services
from trip_feasibility.checks.deps import make_check_deps, make_http_client
from trip_feasibility.config import get_settings
from trip_feasibility.db import make_engine
from trip_feasibility.errors import install_error_handlers
from trip_feasibility.parse.extract import make_client
from trip_feasibility.ratelimit import SlidingWindowLimiter
from trip_feasibility.runs import RunService
from trip_feasibility.tracing import make_tracer

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    # One CheckDeps for the whole app: its rate limiters keep every request together within
    # Nominatim's and OSRM's 1 request/second.
    http = make_http_client(settings)
    deps = make_check_deps(http, sessionmaker, settings)
    tracer = make_tracer(settings)
    llm = make_client(settings) if settings.anthropic_api_key is not None else None
    runs = RunService(settings, deps, sessionmaker, tracer, llm)
    app.state.services = Services(
        settings=settings,
        deps=deps,
        runs=runs,
        tracer=tracer,
        llm=llm,
        parse_limit=SlidingWindowLimiter("parses", settings.rate_limit_parses_per_hour),
        check_limit=SlidingWindowLimiter("check runs", settings.rate_limit_checks_per_hour),
        demo_limit=SlidingWindowLimiter("demo runs", settings.rate_limit_demo_per_hour),
    )
    # the database may be down: /health reports it, and the app still starts
    with contextlib.suppress(Exception):
        await runs.mark_interrupted()
    yield
    await runs.shutdown()
    await http.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Trip Feasibility Checker", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)

    @app.get("/health", tags=["meta"])
    async def health(request: Request) -> JSONResponse:
        try:
            async with asyncio.timeout(2):
                async with request.app.state.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok"})

    return app


app = create_app()
