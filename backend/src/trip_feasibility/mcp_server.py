"""MCP tool server: every feasibility check as a tool returning a structured CheckResult.

Run over stdio with `python -m trip_feasibility.mcp_server`; in-process clients (the agent,
tests) connect with `mcp.Client(server)`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy.ext.asyncio import async_sessionmaker

from trip_feasibility.checks.deps import CheckDeps, ResponseCache, make_http_client
from trip_feasibility.checks.holidays import check_public_holidays
from trip_feasibility.checks.models import CheckResult
from trip_feasibility.checks.opening_hours import check_opening_hours
from trip_feasibility.config import get_settings
from trip_feasibility.db import make_engine
from trip_feasibility.itinerary import Stop


@asynccontextmanager
async def lifespan(_: MCPServer) -> AsyncIterator[CheckDeps]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    async with make_http_client(settings) as http:
        try:
            yield CheckDeps(
                http=http, cache=ResponseCache(async_sessionmaker(engine)), settings=settings
            )
        finally:
            await engine.dispose()


server = MCPServer("trip-feasibility-checks", lifespan=lifespan)


def _deps(ctx: Context) -> CheckDeps:
    return ctx.request_context.lifespan_context


@server.tool()
async def public_holidays(
    stops: list[Stop], country_code: str, ctx: Context, region: str | None = None
) -> CheckResult:
    """Flag stops that fall on a public holiday in the trip's country.

    country_code: ISO 3166-1 alpha-2 (e.g. "FR"). region: ISO 3166-2 (e.g. "DE-BY"), used to
    decide whether regional holidays apply; leave empty if unknown.
    """
    return await check_public_holidays(_deps(ctx), stops, country_code, region)


@server.tool()
async def opening_hours(
    stops: list[Stop], ctx: Context, country_code: str | None = None
) -> CheckResult:
    """Flag venue and meal stops that fall outside the place's OpenStreetMap opening hours,
    or whose place no longer exists. Stops without usable hours are listed as unknown.

    country_code: ISO 3166-1 alpha-2, so holiday rules ("PH off") use the right calendar.
    """
    return await check_opening_hours(_deps(ctx), stops, country_code)


if __name__ == "__main__":
    server.run()
