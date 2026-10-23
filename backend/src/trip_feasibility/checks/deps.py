"""What every check needs: an HTTP client, the Postgres-backed response cache and settings."""

import asyncio
import time
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


class RateLimiter:
    """Keeps calls to one service at least min_interval_s apart (per process), and stops
    calling it for cooldown_s after it signals overload, instead of retrying into a ban."""

    def __init__(self, name: str, min_interval_s: float, cooldown_s: float) -> None:
        self.name = name
        self._min_interval_s = min_interval_s
        self._cooldown_s = cooldown_s
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        self._paused_until = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._paused_until:
                left = self._paused_until - now
                raise Unavailable(f"{self.name} asked us to back off; pausing {left:.0f}s more")
            if now < self._next_at:
                await asyncio.sleep(self._next_at - now)
            self._next_at = time.monotonic() + self._min_interval_s

    def back_off(self) -> None:
        self._paused_until = time.monotonic() + self._cooldown_s


@dataclass
class CheckDeps:
    http: httpx.AsyncClient
    cache: ResponseCache
    settings: Settings
    nominatim_limiter: RateLimiter
    routing_limiter: RateLimiter

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


def make_check_deps(
    http: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> CheckDeps:
    return CheckDeps(
        http=http,
        cache=ResponseCache(sessionmaker),
        settings=settings,
        nominatim_limiter=RateLimiter(
            "Nominatim", settings.nominatim_min_interval_s, settings.rate_limit_cooldown_s
        ),
        routing_limiter=RateLimiter(
            "OSRM routing", settings.routing_min_interval_s, settings.rate_limit_cooldown_s
        ),
    )
