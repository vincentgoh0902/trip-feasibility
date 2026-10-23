"""Tests run against their own database, never the dev one.

TEST_DATABASE_URL picks it; otherwise it is DATABASE_URL (or the default) with "_test"
appended to the database name. It is created if missing and migrated once per session.
Some tests drop the whole schema, so a database whose name doesn't end in "_test" is refused.
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from trip_feasibility.config import Settings


def _test_database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    base = make_url(Settings().database_url)
    return base.set(database=f"{base.database}_test").render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url()
if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
    raise RuntimeError(f"Refusing to run tests against a non-test database: {TEST_DATABASE_URL}")

# Set before the app reads its settings, so every engine the tests create points here.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Fake data-source hosts: tests mock them with respx and can never hit the real services.
os.environ["NAGER_BASE_URL"] = "https://nager.test/api/v3"
os.environ["NOMINATIM_BASE_URL"] = "https://nominatim.test"
os.environ["NOMINATIM_MIN_INTERVAL_S"] = "0"
os.environ["ROUTING_BASE_URL"] = "https://routing.test"
os.environ["ROUTING_MIN_INTERVAL_S"] = "0"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from trip_feasibility.checks.deps import make_check_deps, make_http_client  # noqa: E402
from trip_feasibility.config import get_settings  # noqa: E402
from trip_feasibility.main import app  # noqa: E402


async def _ensure_database_exists(url: str) -> None:
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session", autouse=True)
async def test_database():
    get_settings.cache_clear()
    await _ensure_database_exists(TEST_DATABASE_URL)
    # Alembic's async env.py runs its own event loop, so call it off this one.
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def check_deps(test_database):
    """CheckDeps on the test database with an empty response cache."""
    engine = create_async_engine(test_database)
    sessionmaker = async_sessionmaker(engine)
    async with sessionmaker() as session:
        await session.execute(text("TRUNCATE api_cache"))
        await session.commit()
    settings = get_settings()
    async with make_http_client(settings) as http:
        yield make_check_deps(http, sessionmaker, settings)
    await engine.dispose()
