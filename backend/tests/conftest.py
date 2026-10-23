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

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

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
