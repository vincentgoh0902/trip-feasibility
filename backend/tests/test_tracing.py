import contextlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.parse.test_extract import FakeClient, reply
from trip_feasibility.config import Settings
from trip_feasibility.errors import AppError
from trip_feasibility.pricing import call_cost
from trip_feasibility.ratelimit import SlidingWindowLimiter
from trip_feasibility.tracing import NoTracer, TracedClient, TracedMcp, Usage, make_tracer


class RecordingTracer:
    def __init__(self, capture_content=False):
        self.capture_content = capture_content
        self.observed = []

    @contextlib.contextmanager
    def observe(self, name, *, as_type="span", **kwargs):
        record = {"name": name, "as_type": as_type, **kwargs, "updates": []}
        self.observed.append(record)
        yield SimpleNamespace(trace_id="t1", update=lambda **kw: record["updates"].append(kw))

    def flush(self):
        pass


def test_haiku_prices_and_its_long_prompt_rate():
    assert call_cost("claude-haiku-5-5", 100_000, 0) == Decimal("0.01")  # $0.10/M
    assert call_cost("claude-haiku-5-5", 0, 1_000_000) == Decimal("0.50")
    # past 100K prompt tokens the whole call is billed at $0.50/M in, $2.50/M out
    assert call_cost("claude-haiku-5-5", 200_000, 1000) == Decimal("0.1025")
    assert call_cost("some-new-model", 10, 10) is None


def test_usage_adds_up_and_unknown_models_make_cost_unknown():
    u = Usage()
    u.add("claude-haiku-5-5", 1000, 100)
    u.add("claude-haiku-5-5", 1000, 100)
    assert (u.calls, u.input_tokens, u.cost_usd) == (2, 2000, Decimal("0.0003"))
    u.add("mystery", 1, 1)
    assert u.cost_usd is None and u.as_dict()["models"] == ["claude-haiku-5-5", "mystery"]


async def test_traced_client_records_a_generation_without_content_by_default():
    tracer, usage = RecordingTracer(), Usage()
    client = TracedClient(FakeClient(reply({"ok": 1}, input_tokens=500, output_tokens=50)),
                          tracer, usage, "fixes")  # fmt: skip
    await client.messages.create(model="claude-haiku-5-5", system="secret prompt",
                                 messages=[{"role": "user", "content": "my trip"}],
                                 output_config={"effort": "low"})  # fmt: skip
    [gen] = tracer.observed
    assert gen["name"] == "fixes" and gen["as_type"] == "generation"
    assert gen["input"] is None  # the traveller's itinerary doesn't leave by default
    [update] = gen["updates"]
    assert update["output"] is None
    assert update["usage_details"] == {"input": 500, "output": 50}
    assert update["cost_details"]["total"] == pytest.approx(500 * 0.1e-6 + 50 * 0.5e-6)
    assert usage.calls == 1


async def test_traced_client_sends_content_when_asked():
    tracer = RecordingTracer(capture_content=True)
    client = TracedClient(FakeClient(reply({"ok": 1})), tracer, Usage(), "extraction")
    await client.messages.create(model="m", system="s", messages=[{"role": "user", "content": "x"}])
    assert tracer.observed[0]["input"]["system"] == "s"
    assert tracer.observed[0]["updates"][0]["output"] == '{"ok": 1}'


async def test_traced_mcp_passes_results_through():
    result = SimpleNamespace(structured_content={"status": "unavailable", "violations": [],
                                                 "unknown": [], "error": "down"})  # fmt: skip

    class Mcp:
        async def call_tool(self, name, arguments):
            return result

    tracer = RecordingTracer()
    assert await TracedMcp(Mcp(), tracer).call_tool("travel_time", {}) is result
    [span] = tracer.observed
    assert span["as_type"] == "tool"
    assert span["updates"][0]["level"] == "WARNING"


def test_tracing_is_off_without_both_keys():
    assert isinstance(make_tracer(Settings(langfuse_public_key="pk")), NoTracer)
    assert isinstance(
        make_tracer(Settings(langfuse_public_key="", langfuse_secret_key="")), NoTracer
    )


def test_rate_limiter_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("trip_feasibility.ratelimit.time.monotonic", lambda: now[0])
    limiter = SlidingWindowLimiter("check runs", per_hour=2)
    limiter.hit("u1")
    limiter.hit("u1")
    with pytest.raises(AppError) as e:
        limiter.hit("u1")
    assert e.value.status == 429 and "60 minute" in e.value.message
    limiter.hit("u2")  # per key
    now[0] += 3601
    limiter.hit("u1")  # the window moved on
