"""What every check needs: an HTTP client, the Postgres-backed response cache and settings."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trip_feasibility.config import Settings
from trip_feasibility.models import ApiCache


class Unavailable(Exception):
    """An external data source failed; the check reports "unavailable" and the run goes on."""


class ResponseCache:
    """api_cache table. Values are wrapped as {"body": ...} so lists and nulls can be cached."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        if not keys:
            return {}
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(ApiCache.key, ApiCache.response).where(
                    ApiCache.key.in_(keys), ApiCache.expires_at > datetime.now(UTC)
                )
            )
            return {key: response["body"] for key, response in rows}

    async def get(self, key: str) -> Any | None:
        return (await self.get_many([key])).get(key)

    async def put_many(self, source: str, items: dict[str, Any], ttl: timedelta) -> None:
        if not items:
            return
        expires_at = datetime.now(UTC) + ttl
        rows = [
            {"key": k, "source": source, "response": {"body": v}, "expires_at": expires_at}
            for k, v in items.items()
        ]
        stmt = insert(ApiCache).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ApiCache.key],
            set_={"response": stmt.excluded.response, "expires_at": stmt.excluded.expires_at},
        )
        async with self._sessionmaker() as session:
            await session.execute(stmt)
            await session.commit()

    async def put(self, source: str, key: str, value: Any, ttl: timedelta) -> None:
        await self.put_many(source, {key: value}, ttl)


@dataclass
class CheckDeps:
    http: httpx.AsyncClient
    cache: ResponseCache
    settings: Settings

    async def cached(
        self, source: str, key: str, ttl: timedelta, fetch: Callable[[], Awaitable[Any]]
    ) -> Any:
        if (hit := await self.cache.get(key)) is not None:
            return hit
        value = await fetch()
        await self.cache.put(source, key, value, ttl)
        return value


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": settings.http_user_agent},
        timeout=settings.http_timeout_s,
        follow_redirects=True,
    )
