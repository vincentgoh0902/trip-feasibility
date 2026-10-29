import json

import pytest

from tests.test_dataset_draft import FakeLLM, label, output, undrafted
from trip_feasibility.dataset.draft import RULES
from trip_feasibility.evals.baseline import BASELINE_PROMPT_VERSION, build_prompt, run_baseline
from trip_feasibility.evals.score import Finding
from trip_feasibility.evals.search_llm import (
    ClaudeCodeBackend,
    SearchLLMError,
    SearchResult,
    parse_claude_events,
)


def test_prompt_shares_the_rules_and_inputs_but_not_the_framing():
    prompt = build_prompt(undrafted())
    assert RULES in prompt and undrafted().clean_text in prompt
    assert "Day 1: Tuesday 8 December 2026" in prompt
    assert "answer key" not in prompt  # that's the drafter's framing


async def test_output_becomes_findings_and_is_cached(tmp_path):
    travel = label(check="travel_time", place="Eiffel Tower", from_place="Musée d'Orsay")
    llm = FakeLLM(output(label(place="The Louvre"), travel, label(place=None)))
    cache = tmp_path / "x.json"
    result = await run_baseline(llm, undrafted(), cache)
    # not verbatim is fine (matched fuzzily); a missing place isn't
    assert result.findings == [
        Finding("opening_hours", "blocker", 1, "The Louvre"),
        Finding("travel_time", "blocker", 1, "Eiffel Tower", "Musée d'Orsay"),
    ]
    assert result.invalid == ["violation 3: Value error, opening_hours labels name a place"]
    assert json.loads(cache.read_text())["prompt_version"] == BASELINE_PROMPT_VERSION

    again = await run_baseline(FakeLLM(), undrafted(), cache)  # no outputs left: must not run
    assert again.cached and again.findings == result.findings


async def test_cache_is_dropped_when_the_inputs_change(tmp_path):
    cache = tmp_path / "x.json"
    await run_baseline(FakeLLM(output()), undrafted(), cache)
    changed = undrafted().model_copy(update={"text": undrafted().text + "\nExtra line."})
    llm = FakeLLM(output(label()))
    result = await run_baseline(llm, changed, cache)
    assert not result.cached and len(llm.prompts) == 1


def test_claude_command_is_a_plain_model_with_search():
    cmd = ClaudeCodeBackend("claude-haiku-5-5").command({"type": "object"})
    tools = cmd.index("--tools")
    assert cmd[tools + 1 : tools + 3] == ["WebSearch", "WebFetch"]
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd and "--system-prompt" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"


def events(*items) -> str:
    return "\n".join(json.dumps(i) for i in items)


RESULT = {"type": "result", "subtype": "success", "is_error": False,
          "structured_output": {"violations": []}, "modelUsage": {"claude-haiku-5-5": {}},
          "usage": {"input_tokens": 10, "output_tokens": 3, "server_tool_use": {}}}  # fmt: skip
SEARCH = {"type": "assistant",
          "message": {"content": [{"type": "tool_use", "name": "WebSearch"},
                                  {"type": "tool_use", "name": "WebFetch"}]}}  # fmt: skip


def parse(*items) -> SearchResult:
    result = SearchResult(output={}, provider="anthropic", model="claude-haiku-5-5")
    parse_claude_events(events(*items), result)
    return result


def test_claude_events_give_output_searches_and_usage():
    hook = {"type": "system", "subtype": "hook_response", "hook_name": "SessionStart",
            "stdout": "{}\n"}  # fmt: skip
    result = parse(hook, SEARCH, SEARCH, RESULT)
    assert result.output == {"violations": []} and result.searches == 2
    assert result.usage == {"input_tokens": 10, "output_tokens": 3}


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"type": "system", "subtype": "hook_response", "hook_name": "SessionStart",
           "stdout": '{"additionalContext": "x"}'}, RESULT], "added to the context"),
        ([{**RESULT, "modelUsage": {"claude-sonnet-5-5": {}}}], "not claude-haiku-5-5"),
        ([{**RESULT, "is_error": True}], "run failed"),
        ([SEARCH], "no result"),
        ([{**RESULT, "structured_output": None}], "no structured output"),
    ],
)  # fmt: skip
def test_claude_events_refuse_bad_runs(items, message):
    with pytest.raises(SearchLLMError, match=message):
        parse(*items)
