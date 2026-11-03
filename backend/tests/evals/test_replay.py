"""The replay client: a request seen before is answered from disk, never from the model."""

from tests.parse.test_extract import GOOD, TRIP, FakeClient, reply, run
from trip_feasibility.evals.replay import ReplayClient
from trip_feasibility.parse.extract import extract_stops


async def test_a_repeated_request_is_served_from_disk(tmp_path):
    first = ReplayClient(FakeClient(reply(GOOD, input_tokens=7, output_tokens=3)), tmp_path)
    a = await run(first)
    assert (first.hits, first.misses) == (0, 1)

    # a new client with nothing left to answer: only the saved reply can serve it
    second = ReplayClient(FakeClient(), tmp_path)
    b = await run(second)
    assert (second.hits, second.misses) == (1, 0)
    assert b.extraction == a.extraction
    assert (b.input_tokens, b.output_tokens) == (7, 3)


async def test_a_different_request_goes_to_the_model(tmp_path):
    await run(ReplayClient(FakeClient(reply(GOOD)), tmp_path))
    fake = FakeClient(reply(GOOD))
    client = ReplayClient(fake, tmp_path)
    await extract_stops(
        client, "9-12: Louvre Museum", city="Paris", trip_start=TRIP, model="claude-sonnet-5-5"
    )
    assert len(fake.calls) == 1 and client.misses == 1


async def test_unusable_replies_are_not_saved(tmp_path):
    refused = reply(GOOD, stop_reason="refusal")
    client = ReplayClient(FakeClient(refused), tmp_path)
    await client.messages.create(model="m", messages=[])
    assert list(tmp_path.iterdir()) == []
