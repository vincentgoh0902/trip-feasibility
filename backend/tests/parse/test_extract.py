"""Parse step 1 against a fake Anthropic client: nothing here calls the real API."""

import json
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from trip_feasibility.parse.extract import (
    EXTRACTION_SCHEMA,
    PROMPT_VERSION,
    ExtractionError,
    extract_stops,
)

TRIP = date(2026, 12, 8)
GOOD = {"stops": [{"day": 1, "start_time": "09:00", "end_time": "12:00", "time_of_day": None,
                   "place_name": "Louvre Museum", "type": "venue"}]}  # fmt: skip
# valid JSON for the schema, but breaks a rule only Pydantic checks
BOTH_TIMES = {"stops": [{**GOOD["stops"][0], "time_of_day": "morning"}]}


def reply(body, *, stop_reason="end_turn", input_tokens=100, output_tokens=50):
    text = body if isinstance(body, str) else json.dumps(body)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


async def run(client):
    return await extract_stops(
        client, "9-12: Louvre Museum", city="Paris", trip_start=TRIP, model="claude-haiku-5-5"
    )


async def test_extracts_stops():
    client = FakeClient(reply(GOOD))
    result = await run(client)
    assert [s.place_name for s in result.extraction.stops] == ["Louvre Museum"]
    assert result.attempts == 1 and result.prompt_version == PROMPT_VERSION
    assert (result.input_tokens, result.output_tokens) == (100, 50)
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": EXTRACTION_SCHEMA}
    assert "Tuesday 08 December 2026" in call["messages"][0]["content"]
    assert "<itinerary>\n9-12: Louvre Museum\n</itinerary>" in call["messages"][0]["content"]


async def test_retries_once_with_the_validation_error():
    client = FakeClient(reply(BOTH_TIMES), reply(GOOD))
    result = await run(client)
    assert result.attempts == 2
    assert result.input_tokens == 200
    retry = client.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert "not both" in retry[2]["content"]


async def test_two_failures_are_a_parse_error():
    client = FakeClient(reply(BOTH_TIMES), reply("not json"))
    with pytest.raises(ExtractionError) as e:
        await run(client)
    assert e.value.code == "parse_failed" and "not valid JSON" in str(e.value)


@pytest.mark.parametrize(
    ("stop_reason", "code"), [("refusal", "refused"), ("max_tokens", "truncated")]
)
async def test_refusal_and_truncation(stop_reason, code):
    with pytest.raises(ExtractionError) as e:
        await run(FakeClient(reply("", stop_reason=stop_reason)))
    assert e.value.code == code


async def test_api_failures_are_unavailable():
    request = httpx2.Request("POST", "https://api.anthropic.test/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    with pytest.raises(ExtractionError) as e:
        await run(FakeClient(error))
    assert e.value.code == "unavailable"


def test_schema_has_no_coordinates_and_requires_every_field():
    item = EXTRACTION_SCHEMA["properties"]["stops"]["items"]
    assert set(item["required"]) == set(item["properties"])
    assert not {"lat", "lon", "osm"} & set(item["properties"])
    assert item["additionalProperties"] is False
