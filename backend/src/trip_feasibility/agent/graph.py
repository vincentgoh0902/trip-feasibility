"""The agent graph (LangGraph): one branch per enabled check, run in parallel through the
in-process MCP tool server, then fixes.

    START ─► run_check (opening_hours) ─┐
          ├► run_check (travel_time) ───┼─► fix ─► END
          └► ... one per enabled check ─┘

Each branch calls one MCP tool and stores its CheckResult. A tool error or a broken call
becomes an "unavailable" result for that check, so the run always finishes (PRD:
reliability). Which checks run is configuration, not a model's choice. The fix node
(agent/fixes.py) is where the LLM comes in: it proposes fixes, and code re-checks them. It's
skipped when the run has no fixer (no API key) or nothing to fix.
"""

import asyncio
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from mcp import Client

from trip_feasibility.agent.fixes import FixReport, propose_fixes
from trip_feasibility.agent.models import CheckInput, CheckReport
from trip_feasibility.checks.config import CHECK_NAMES, CheckName
from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.checks.models import CheckResult
from trip_feasibility.mcp_server import create_server
from trip_feasibility.parse.extract import MessagesClient
from trip_feasibility.parse.geocode import City

__all__ = ["CheckInput", "CheckReport", "run_checks", "run_agent"]


class CheckState(TypedDict, total=False):
    input: CheckInput
    results: Annotated[dict[str, CheckResult], operator.or_]
    fixes: FixReport | None


class CheckTask(TypedDict):
    input: CheckInput
    check: CheckName


@dataclass
class Fixer:
    """What the fix node needs; absent when fixes are off."""

    client: MessagesClient
    model: str
    effort: str
    city: City | None  # to geocode replacement places


@dataclass
class AgentContext:
    mcp: Client  # connected
    deps: CheckDeps | None = None
    fixer: Fixer | None = None


def tool_arguments(check: CheckName, inp: CheckInput) -> dict[str, Any] | str:
    """The MCP tool's arguments, or why the check can't run."""
    stops = [s.model_dump(mode="json") for s in inp.stops]
    preferences = inp.preferences.model_dump(mode="json")
    match check:
        case "public_holidays":
            if inp.country_code is None:
                return "the trip's country is unknown"
            return {"stops": stops, "country_code": inp.country_code}
        case "opening_hours":
            return {"stops": stops, "country_code": inp.country_code}
        case "travel_time":
            return {
                "stops": stops,
                "preferences": preferences,
                "buffer_min": inp.config.travel_buffer_min,
            }
        case "daily_load":
            return {"stops": stops, "preferences": preferences}
        case "hotel_red_flags":
            return {"stops": stops, "city": inp.city, "min_reviews": inp.config.hotel_min_reviews}


async def call_check(mcp: Client, name: CheckName, inp: CheckInput) -> CheckResult:
    args = tool_arguments(name, inp)
    if isinstance(args, str):
        return CheckResult.unavailable(name, args)
    try:
        called = await mcp.call_tool(name, args)
    except Exception as e:  # a broken call must not sink the run
        return CheckResult.unavailable(name, f"tool call failed: {e!r}")
    if called.is_error or called.structured_content is None:
        detail = " ".join(getattr(c, "text", "") for c in called.content).strip()
        return CheckResult.unavailable(name, detail or "tool error")
    return CheckResult.model_validate(called.structured_content)


async def check_all(mcp: Client, inp: CheckInput) -> CheckReport:
    """Every enabled check at once, outside the graph (fix re-checks)."""
    names = [n for n in CHECK_NAMES if n in inp.config.enabled]
    results = await asyncio.gather(*(call_check(mcp, n, inp) for n in names))
    return CheckReport.from_results(inp, dict(zip(names, results, strict=True)))


def fan_out(state: CheckState) -> list[Send]:
    enabled = state["input"].config.enabled
    return [
        Send("run_check", CheckTask(input=state["input"], check=name))
        for name in CHECK_NAMES
        if name in enabled
    ]


async def run_check(task: CheckTask, runtime: Runtime[AgentContext]) -> dict:
    name = task["check"]
    return {"results": {name: await call_check(runtime.context.mcp, name, task["input"])}}


async def fix(state: CheckState, runtime: Runtime[AgentContext]) -> dict:
    ctx = runtime.context
    report = CheckReport.from_results(state["input"], state["results"])
    if ctx.fixer is None or ctx.deps is None or not report.violations:
        return {"fixes": None}
    fixes = await propose_fixes(
        ctx.deps,
        ctx.fixer.client,
        state["input"],
        report,
        lambda inp: check_all(ctx.mcp, inp),
        city=ctx.fixer.city,
        model=ctx.fixer.model,
        effort=ctx.fixer.effort,
    )
    return {"fixes": fixes}


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(CheckState, context_schema=AgentContext)
    graph.add_node("run_check", run_check)
    graph.add_node("fix", fix)
    graph.add_conditional_edges(START, fan_out, ["run_check"])
    graph.add_edge("run_check", "fix")
    graph.add_edge("fix", END)
    return graph.compile()


GRAPH = build_graph()


async def run_agent(
    deps: CheckDeps, inp: CheckInput, fixer: Fixer | None = None
) -> tuple[CheckReport, FixReport | None]:
    async with Client(create_server(deps)) as client:
        state = await GRAPH.ainvoke(
            {"input": inp, "results": {}},
            context=AgentContext(mcp=client, deps=deps, fixer=fixer),
        )
    return CheckReport.from_results(inp, state["results"]), state.get("fixes")


async def run_checks(deps: CheckDeps, inp: CheckInput) -> CheckReport:
    """Checks only, no fixes."""
    report, _ = await run_agent(deps, inp)
    return report
