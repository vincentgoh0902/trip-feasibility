"""The check graph (LangGraph): one branch per enabled check, run in parallel through the
in-process MCP tool server, then a report.

    START ─► run_check (opening_hours) ─┐
          ├► run_check (travel_time) ───┼─► END
          └► ... one per enabled check ─┘

Each branch calls one MCP tool and stores its CheckResult. A tool error or a broken call
becomes an "unavailable" result for that check, so the run always finishes (PRD:
reliability). No LLM is involved: which checks run is configuration, not a model's choice.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from mcp import Client
from pydantic import BaseModel

from trip_feasibility.checks.config import CHECK_NAMES, CheckConfig, CheckName
from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.checks.models import SEVERITY_RANK, CheckResult, Violation
from trip_feasibility.itinerary import Stop, TravelPreferences
from trip_feasibility.mcp_server import create_server


class CheckInput(BaseModel):
    stops: list[Stop]
    city: str  # in English, e.g. "Paris"
    country_code: str | None  # ISO 3166-1 alpha-2; holiday checks need it
    preferences: TravelPreferences = TravelPreferences()
    config: CheckConfig = CheckConfig()


class CheckState(TypedDict):
    input: CheckInput
    results: Annotated[dict[str, CheckResult], operator.or_]


class CheckTask(TypedDict):
    input: CheckInput
    check: CheckName


@dataclass
class AgentContext:
    mcp: Client  # connected


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


def fan_out(state: CheckState) -> list[Send]:
    enabled = state["input"].config.enabled
    return [
        Send("run_check", CheckTask(input=state["input"], check=name))
        for name in CHECK_NAMES
        if name in enabled
    ]


async def run_check(task: CheckTask, runtime: Runtime[AgentContext]) -> dict:
    name = task["check"]
    args = tool_arguments(name, task["input"])
    if isinstance(args, str):
        return {"results": {name: CheckResult.unavailable(name, args)}}
    try:
        called = await runtime.context.mcp.call_tool(name, args)
    except Exception as e:  # a broken call must not sink the run
        return {"results": {name: CheckResult.unavailable(name, f"tool call failed: {e!r}")}}
    if called.is_error or called.structured_content is None:
        detail = " ".join(getattr(c, "text", "") for c in called.content).strip()
        return {"results": {name: CheckResult.unavailable(name, detail or "tool error")}}
    return {"results": {name: CheckResult.model_validate(called.structured_content)}}


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(CheckState, context_schema=AgentContext)
    graph.add_node("run_check", run_check)
    graph.add_conditional_edges(START, fan_out, ["run_check"])
    return graph.compile()


GRAPH = build_graph()


class CheckReport(BaseModel):
    input: CheckInput
    results: dict[str, CheckResult]  # in CHECK_NAMES order, enabled checks only
    violations: list[Violation]  # all checks, most severe first, then by first stop

    @classmethod
    def from_results(cls, inp: CheckInput, results: dict[str, CheckResult]) -> "CheckReport":
        order = {s.id: i for i, s in enumerate(inp.stops)}

        def key(v: Violation) -> tuple:
            first = min((order.get(i, len(order)) for i in v.stop_ids), default=len(order))
            return (-SEVERITY_RANK[v.severity], first, v.check_name)

        results = {n: results[n] for n in CHECK_NAMES if n in results}
        violations = sorted((v for r in results.values() for v in r.violations), key=key)
        return cls(input=inp, results=results, violations=violations)


async def run_checks(deps: CheckDeps, inp: CheckInput) -> CheckReport:
    async with Client(create_server(deps)) as client:
        state = await GRAPH.ainvoke({"input": inp, "results": {}}, context=AgentContext(mcp=client))
    return CheckReport.from_results(inp, state["results"])
