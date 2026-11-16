"""Prepared replies (decision 100): what's stored, what's served, and that a miss never
reaches a model."""

import pytest

from tests.parse.test_extract import GOOD, FakeClient, reply
from trip_feasibility.llm import LLMError, create_json
from trip_feasibility.parse.extract import ExtractionError
from trip_feasibility.prepared import PreparedClient, PreparingClient, request_key


class FakeCache:
    def __init__(self):
        self.rows = {}

    async def get(self, key, *, any_age=False):
        assert any_age  # prepared replies don't expire with the cache TTLs
        return self.rows.get(key)

    async def put(self, source, key, value, ttl):
        self.rows[key] = value


REQUEST = {"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "9-12 Louvre"}]}


async def test_preparing_stores_finished_replies_and_prepared_serves_them_free():
    cache = FakeCache()
    model = FakeClient(reply(GOOD, input_tokens=900, output_tokens=80))
    preparing = PreparingClient(model, cache)
    first = await preparing.messages.create(**REQUEST)
    assert first.usage.input_tokens == 900 and preparing.stored == 1
    again = await PreparedClient(cache).messages.create(**REQUEST)
    assert again.content[0].text == first.content[0].text
    assert (again.usage.input_tokens, again.usage.output_tokens) == (0, 0)  # nothing spent
    assert len(model.calls) == 1


async def test_cut_off_replies_are_not_stored():
    cache = FakeCache()
    await PreparingClient(FakeClient(reply(GOOD, stop_reason="max_tokens")), cache).messages.create(
        **REQUEST
    )
    assert cache.rows == {}


async def test_a_request_nobody_prepared_is_refused():
    cache = FakeCache()
    await PreparingClient(FakeClient(reply(GOOD)), cache).messages.create(**REQUEST)
    other = REQUEST | {"messages": [{"role": "user", "content": "9-12 Orsay"}]}
    assert request_key(other) != request_key(REQUEST)
    with pytest.raises(LLMError) as e:
        await PreparedClient(cache).messages.create(**other)
    assert e.value.code == "not_prepared"


async def test_callers_get_their_own_error_class():
    with pytest.raises(ExtractionError) as e:
        await create_json(PreparedClient(FakeCache()), ExtractionError, model="m", max_tokens=1,
                          system="s", messages=[], effort="low", schema={})  # fmt: skip
    assert e.value.code == "not_prepared"
