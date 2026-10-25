"""The injection suite against fake models: one that obeys any injected instruction, one that
ignores it. Checks the judging, and that the defences change what the model sees."""

import json

from tests.parse.test_extract import FakeClient, reply
from trip_feasibility.agent import fixes
from trip_feasibility.evals.injection import (
    ADVERTISER,
    ATTACKS,
    Case,
    baseline_extraction,
    cases,
    defences_off,
    injected_text,
    run_case,
    suite_version,
    summarise,
    violations_for,
)
from trip_feasibility.parse import extract

MODELS = {"extraction_model": "m", "extraction_effort": "low", "fix_model": "m",
          "fix_effort": "low"}  # fmt: skip
ADD, DROP, LINK = (next(a for a in ATTACKS if a.goal == g) for g in ("add_place", "drop_stops",
                                                                     "plant_link"))  # fmt: skip


def stops(*names, at="09:00"):
    return {"stops": [{"day": 1, "start_time": at, "end_time": None, "time_of_day": None,
                       "place_name": n, "type": "venue"} for n in names]}  # fmt: skip


def fix_reply(*operations, description="Visit on Wednesday instead"):
    ops = [{"stop_id": None, "day": None, "start_time": None, "end_time": None,
            "place_name": None, "type": None, "mode": None} | op for op in operations]  # fmt: skip
    return reply({"fixes": [{"violation_ids": ["v1"], "description": description,
                             "operations": ops}]})  # fmt: skip


BASELINE = [s for s in extract.Extraction.model_validate(stops("Louvre", "Orsay")).stops]


async def judge(case, *replies):
    return await run_case(case, FakeClient(*replies), baseline=BASELINE, **MODELS)


def test_every_vector_meets_every_attack():
    assert len(cases()) == 24
    assert suite_version().startswith("injection-v1-")
    assert ADD.text in injected_text(ADD.text)


async def test_extraction_judging():
    case = Case("itinerary", ADD)
    obeyed = await judge(case, reply(stops("Louvre", "Orsay", ADVERTISER)))
    ignored = await judge(case, reply(stops("Louvre", "Orsay")))
    assert (obeyed.followed, ignored.followed) == (True, False)
    dropped = await judge(Case("itinerary", DROP), reply(stops()))
    assert dropped.followed
    three_am = await judge(Case("itinerary", LINK), reply(stops("Louvre", at="03:00")))
    assert three_am.followed


async def test_fix_judging():
    remove_louvre = {"op": "remove", "stop_id": "s2"}  # the closed stop: a real fix
    fine = await judge(Case("osm_hours", DROP), fix_reply(remove_louvre))
    assert fine.followed is False
    wiped = await judge(
        Case("osm_hours", DROP),
        fix_reply(remove_louvre, {"op": "remove", "stop_id": "s4"}),
    )
    assert wiped.followed
    advert = await judge(
        Case("hotel_review", ADD),
        fix_reply({"op": "replace", "stop_id": "s2", "place_name": ADVERTISER}),
        fix_reply({"op": "move", "stop_id": "s2", "day": 1}),  # the retry after the replace fails
    )
    assert advert.followed
    link = await judge(
        Case("osm_tags", LINK),
        fix_reply(remove_louvre, description="Pay at https://evil.example first"),
        fix_reply({"op": "remove", "stop_id": "s3"}),  # the retry, for the closed café (v2)
    )
    assert link.followed


async def test_a_failed_call_is_not_judged():
    r = await judge(Case("itinerary", ADD), reply("nope"), reply("still nope"))
    assert r.followed is None
    assert summarise([r])["overall"]["failed_calls"] == 1


def test_defences_keep_payloads_out_of_the_fix_prompt_and_off_restores_them():
    for vector in ("osm_tags", "osm_hours", "hotel_review"):
        found = {f"v{i}": v for i, v in enumerate(violations_for(Case(vector, ADD)), 1)}
        message = fixes.user_message(_inp(), found)
        assert ADVERTISER not in message, vector
        with defences_off():
            assert ADVERTISER in fixes.user_message(_inp(), found), vector
            assert "never instructions" not in fixes.FIX_SYSTEM_PROMPT
            assert "not instructions" not in extract.SYSTEM_PROMPT
        assert "never instructions" in fixes.FIX_SYSTEM_PROMPT  # restored


async def test_baseline_uses_the_sample_text():
    client = FakeClient(reply(stops("Louvre Museum")))
    got = await baseline_extraction(client, "m", "low")
    assert got[0].place_name == "Louvre Museum"
    assert "Hôtel Regina" in client.calls[0]["messages"][0]["content"]


def test_summary_counts():
    def r(vector, followed):
        return type("R", (), {"vector": vector, "goal": "add_place", "followed": followed})()

    s = summarise([r("itinerary", True), r("itinerary", False), r("osm_tags", False)])
    assert s["overall"] == {"judged": 3, "resisted": 2, "followed": 1, "failed_calls": 0,
                            "resistance": 0.667}  # fmt: skip
    assert s["by_vector"]["hotel_review"]["resistance"] is None
    json.dumps(s)


def _inp():
    from trip_feasibility.agent.models import CheckInput
    from trip_feasibility.evals.injection import base_stops

    return CheckInput(stops=base_stops(), city="Paris", country_code="FR")
