from pathlib import Path

import pytest

from tests.test_dataset_validate import example, hotels
from trip_feasibility.dataset.draft import (
    SCHEMA,
    apply_draft,
    build_prompt,
    check_output,
    draft_itinerary,
)
from trip_feasibility.dataset.models import DatasetItinerary
from trip_feasibility.dataset.validate import itinerary_errors
from trip_feasibility.evals.search_llm import CodexBackend, SearchResult, parse_codex_events


class FakeLLM:
    provider = "openai"
    model = "fake-1"
    label = "fake-1 via test"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def run(self, prompt, schema):
        assert schema is SCHEMA
        self.prompts.append(prompt)
        return SearchResult(output=self.outputs.pop(0), provider=self.provider, model=self.model)


def label(**fields):
    return {"check": "opening_hours", "day": 1, "place": "Louvre Museum", "from_place": None,
            "time": "9:00 – 12:00", "severity": "blocker", "evidence": "Closed Tuesdays",
            "source": "https://www.louvre.fr", "confidence": "verified", **fields}  # fmt: skip


def output(*violations, unknowns=(), other_issues=()):
    return {"violations": list(violations), "unknowns": list(unknowns),
            "other_issues": list(other_issues)}  # fmt: skip


def undrafted() -> DatasetItinerary:
    return example(violations=None, drafted_by=None, verified_by_human=False, unknowns=[],
                   other_issues=[])  # fmt: skip


def test_prompt_has_text_dates_and_preferences_but_no_stops():
    prompt = build_prompt(undrafted())
    assert undrafted().clean_text in prompt
    assert "Day 1: Tuesday 8 December 2026" in prompt
    assert "pace: moderate; walking limit 10 km" in prompt
    assert "staying at Hôtel Regina" in prompt  # the traveller's request
    assert "s2" not in prompt and "place_name" not in prompt  # nothing from the gold stops


async def test_a_clean_draft_takes_one_run():
    llm = FakeLLM(output(label(), other_issues=["Book the summit ahead"]))
    draft = await draft_itinerary(llm, undrafted())
    assert len(llm.prompts) == 1 and not draft.dropped
    assert [v.place for v in draft.violations] == ["Louvre Museum"]
    assert draft.other_issues == ["Book the summit ahead"]


async def test_bad_quotes_get_one_retry_then_are_dropped():
    first = output(label(place="The Louvre"), label(check="daily_load", place=None, time=None))
    second = output(label(place="Le Louvre"), label(check="daily_load", place=None, time=None))
    llm = FakeLLM(first, second)
    draft = await draft_itinerary(llm, undrafted())
    assert len(llm.prompts) == 2
    assert "place 'The Louvre' isn't copied from the cleaned text" in llm.prompts[1]
    assert [v.check for v in draft.violations] == ["daily_load"]
    assert draft.dropped == [
        "violation 1 (opening_hours, day 1): place 'Le Louvre' isn't copied from the cleaned text"
    ]


def test_invalid_shapes_are_problems_not_crashes():
    draft, problems = check_output(
        undrafted(),
        output(
            label(place=None),
            unknowns=[{"check": "daily_load", "day": 0, "place": None, "reason": "r"}],
        ),  # fmt: skip
    )
    assert draft.violations == [] and draft.unknowns == []
    assert problems[0].startswith("violation 1: Value error, opening_hours labels name a place")
    assert problems[1].startswith("unknown 1: Input should be greater than or equal to 1")


async def test_never_anthropic_and_dev_only():
    llm = FakeLLM()
    llm.provider = "anthropic"
    with pytest.raises(ValueError, match="never come from Anthropic"):
        await draft_itinerary(llm, undrafted())
    with pytest.raises(ValueError, match="dev only"):
        await draft_itinerary(FakeLLM(), undrafted().model_copy(update={"split": "test"}))


async def test_applied_draft_is_a_valid_unverified_file():
    unknown = {"check": "opening_hours", "day": 1, "place": "Bateaux Parisiens", "reason": "r"}
    llm = FakeLLM(output(label(), unknowns=[unknown]))
    itin = undrafted()
    draft = await draft_itinerary(llm, itin)
    draft.dropped = ["violation 2 (...): x"]
    raw = itin.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"text"})
    data = apply_draft(raw, draft, "fake-1 via test, draft-v1")
    assert data["drafted_by"] == "fake-1 via test, draft-v1"
    assert data["verified_by_human"] is False
    assert data["notes"].endswith("Draft entries dropped (still invalid after a retry): "
                                  "violation 2 (...): x")  # fmt: skip
    written = DatasetItinerary.model_validate(data | {"text": itin.text})
    assert itinerary_errors(written, hotels()) == [
        "still to do: verify the draft (verified_by_human: true)"
    ]


def test_codex_command_searches_read_only_with_the_schema():
    cmd = CodexBackend("gpt-x", "medium").command(Path("/w"), Path("/s.json"), Path("/o.json"))
    assert cmd[:3] == ["codex", "--search", "exec"]  # --search before exec, or it's rejected
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--model") + 1] == "gpt-x"
    assert 'model_reasoning_effort="medium"' in cmd
    assert cmd[cmd.index("--output-schema") + 1] == "/s.json" and cmd[-1] == "-"


def test_codex_events_give_searches_and_usage():
    events = "\n".join([
        '{"type":"item.started","item":{"type":"web_search"}}',
        '{"type":"item.completed","item":{"type":"web_search"}}',
        '{"type":"item.completed","item":{"type":"web_search"}}',
        "not json",
        '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":7}}',
    ])  # fmt: skip
    result = SearchResult(output={}, provider="openai", model="m")
    parse_codex_events(events, result)
    assert result.searches == 2 and result.usage == {"input_tokens": 100, "output_tokens": 7}
