"""Tracing (Langfuse) and LLM usage accounting for one run.

`make_tracer` returns a Langfuse tracer when both keys are set, otherwise one that records
nothing, so code never checks whether tracing is on. Two wrappers do the instrumenting
without touching the agent code:

- `TracedClient` wraps the Anthropic client: every `messages.create` becomes a generation
  with model, tokens and cost, and is added to the run's `Usage`.
- `TracedMcp` wraps the MCP client: every tool call (a check, including fix re-checks)
  becomes a tool span with its status and counts.

Content (prompts, stops, model output) only goes to Langfuse with TRACE_CONTENT=true: the
itinerary is the traveller's data, and Langfuse Cloud is a third party.
"""

import contextlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from trip_feasibility.config import Settings
from trip_feasibility.pricing import call_cost

log = logging.getLogger(__name__)


class Observation(Protocol):
    trace_id: str | None

    def update(self, **kwargs: Any) -> Any: ...


class Tracer(Protocol):
    capture_content: bool

    def observe(self, name: str, *, as_type: str = "span", **kwargs: Any) -> Any:
        """A context manager yielding an Observation, nested under the current one."""
        ...

    def flush(self) -> None: ...


class _NoObservation:
    trace_id = None

    def update(self, **kwargs: Any) -> None:
        return None


class NoTracer:
    capture_content = False

    @contextlib.contextmanager
    def observe(self, name: str, *, as_type: str = "span", **kwargs: Any) -> Iterator[Any]:
        yield _NoObservation()

    def flush(self) -> None:
        return None


class LangfuseTracer:
    def __init__(self, settings: Settings) -> None:
        from langfuse import Langfuse  # imported only when tracing is on (~12 MB)

        assert settings.langfuse_secret_key is not None
        self._client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
        )
        self.capture_content = settings.trace_content

    def observe(self, name: str, *, as_type: str = "span", **kwargs: Any) -> Any:
        return self._client.start_as_current_observation(name=name, as_type=as_type, **kwargs)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # tracing must never break a run
            log.warning("Langfuse flush failed", exc_info=True)


def make_tracer(settings: Settings) -> Tracer:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        return LangfuseTracer(settings)
    return NoTracer()


@dataclass
class Usage:
    """LLM calls in one run (or one parse): tokens, and cost when every model is priced."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal | None = Decimal(0)
    models: list[str] = field(default_factory=list)

    def add(self, model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if model not in self.models:
            self.models.append(model)
        cost = call_cost(model, input_tokens, output_tokens)
        self.cost_usd = None if cost is None or self.cost_usd is None else self.cost_usd + cost
        return cost

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": None if self.cost_usd is None else float(self.cost_usd),
            "models": self.models,
        }


class _TracedMessages:
    def __init__(self, inner: Any, tracer: Tracer, usage: Usage, name: str) -> None:
        self._inner, self._tracer, self._usage, self._name = inner, tracer, usage, name

    async def create(self, **kwargs: Any) -> Any:
        content = self._tracer.capture_content
        model = kwargs.get("model", "")
        with self._tracer.observe(
            self._name,
            as_type="generation",
            model=model,
            model_parameters={"effort": kwargs.get("output_config", {}).get("effort")},
            input={"system": kwargs.get("system"), "messages": kwargs.get("messages")}
            if content
            else None,
        ) as gen:
            message = await self._inner.create(**kwargs)
            usage = message.usage
            cost = self._usage.add(model, usage.input_tokens, usage.output_tokens)
            gen.update(
                output="".join(getattr(b, "text", "") for b in message.content)
                if content
                else None,
                usage_details={"input": usage.input_tokens, "output": usage.output_tokens},
                cost_details=None if cost is None else {"total": float(cost)},
                metadata={"stop_reason": message.stop_reason},
            )
            return message


class TracedClient:
    """An Anthropic client whose calls are traced and counted. `name` labels the calls."""

    def __init__(self, inner: Any, tracer: Tracer, usage: Usage, name: str) -> None:
        self.messages = _TracedMessages(inner.messages, tracer, usage, name)


class TracedMcp:
    """An MCP client whose tool calls (checks) are traced."""

    def __init__(self, inner: Any, tracer: Tracer) -> None:
        self._inner, self._tracer = inner, tracer

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        with self._tracer.observe(name, as_type="tool") as span:
            result = await self._inner.call_tool(name, arguments)
            out = getattr(result, "structured_content", None) or {}
            span.update(
                output={
                    "status": out.get("status", "error"),
                    "violations": len(out.get("violations", [])),
                    "unknown": len(out.get("unknown", [])),
                    "error": out.get("error"),
                },
                level="WARNING" if out.get("status") != "ok" else "DEFAULT",
            )
            return result
