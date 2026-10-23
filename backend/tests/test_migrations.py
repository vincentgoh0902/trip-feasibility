import asyncio

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from trip_feasibility.config import get_settings


def _alembic() -> Config:
    return Config("alembic.ini")


async def _current_revision() -> str | None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
    await engine.dispose()
    return row[0] if row else None


async def test_migrations_round_trip():
    # Drops every table in the test database, then rebuilds it.
    cfg = _alembic()
    # Alembic's async env.py runs its own event loop, so call it off this one.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _current_revision() == ScriptDirectory.from_config(cfg).get_current_head()
