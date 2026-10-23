import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.config import get_settings
from trip_feasibility.db import make_engine
from trip_feasibility.errors import install_error_handlers

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = make_engine(get_settings().database_url)
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Trip Feasibility Checker", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

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
