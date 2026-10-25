import uuid

from sqlalchemy import func, select

from tests.api.conftest import ANA, BOB, ITINERARY, fake_stream, sse, violation
from tests.parse.test_extract import FakeClient
from tests.parse.test_geocode import search_route
from trip_feasibility import models
from trip_feasibility.agent.fixes import FIX_PROMPT_VERSION, Fix, FixReport, Operation
from trip_feasibility.main import app

V1 = "/api/v1"
CLOSED = violation("opening_hours", "blocker", ["s1"], "Closed on Tuesdays")
TIGHT = violation("travel_time", "warning", ["s1", "s2"], "Tight")
NOTE = violation("public_holidays", "info", ["s1"], "FYI")


def fixes_for(inp):
    """v1 = CLOSED (blocker, first); a valid fix moving s1, an invalid one removing s2."""
    moved = [inp.stops[0].model_copy(update={"day": 1}), inp.stops[1]]
    return FixReport(
        violation_ids={"v1": 0, "v2": 1},
        fixes=[
            Fix(id="f1", violation_ids=["v1"], description="Go on Wednesday",
                operations=[Operation(op="move", stop_id="s1", day=1),
                            Operation(op="add_transport_mode", mode="car")],
                valid=True, stops=moved, transport_modes=["walk", "transit", "car"]),
            Fix(id="f2", violation_ids=["v2"], description="Skip lunch",
                operations=[Operation(op="remove", stop_id="s2")], valid=False,
                problems=["creates a blocker: x"]),
        ],
        unfixed=["v2"],
        model="claude-haiku-5-5",
    )  # fmt: skip


async def start(api, body, headers=None):
    r = await api.post(f"{V1}/checks", json=body, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()


async def events(api, run, headers=None):
    r = await api.get(run["events_url"], headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return sse(r.text)


async def saved_version(api, headers=ANA):
    r = await api.post(f"{V1}/itineraries", json=ITINERARY, headers=headers)
    return r.json()["versions"][0]["id"]


async def count(model):
    async with app.state.sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(model))


# --- demo ---


async def test_samples_are_listed(api):
    [sample] = (await api.get(f"{V1}/demo/samples")).json()
    assert sample["id"] == "paris-one-day" and "Louvre" in sample["text"]


async def test_demo_run_streams_and_saves_nothing(api, services, nominatim):
    services.runs.stream = fake_stream({"opening_hours": [CLOSED]})
    run = await start(api, {"sample_id": "paris-one-day"})
    assert run["demo"] is True
    got = await events(api, run)
    types = [e["type"] for e in got]
    assert types[0] == "step_started" and got[0]["step"] == "geocode"  # samples have no coords
    assert types.count("tool_result") == 5
    assert types[-1] == "done"
    [v] = [e["violation"] for e in got if e["type"] == "violation"]
    assert v["message"] == "Closed on Tuesdays"
    result = got[-1]["run"]
    assert result["violations"][0]["id"] == v["id"]
    assert all(s["lat"] for s in result["stops"])  # geocoded
    assert result["fixes_enabled"] is False
    r = await api.get(run["result_url"])
    assert r.json()["status"] == "done" and r.json()["demo"] is True
    assert await count(models.CheckRun) == 0
    assert await count(models.Violation) == 0


async def test_demo_uses_the_samples_preferences_and_default_checks(api, services, nominatim):
    services.runs.stream = stream = fake_stream()
    await events(api, await start(api, {"sample_id": "paris-one-day"}))
    inp = stream.calls[0]["input"]
    assert inp.country_code == "FR" and inp.city == "Paris"
    assert len(inp.config.enabled) == 5
    assert inp.preferences.transport_modes == ["walk", "transit"]


async def test_unknown_sample_and_bad_bodies(api):
    r = await api.post(f"{V1}/checks", json={"sample_id": "atlantis"})
    assert r.status_code == 404
    for body in ({}, {"sample_id": "paris-one-day", "version_id": str(uuid.uuid4())}):
        assert (await api.post(f"{V1}/checks", json=body)).status_code == 422


async def test_demo_is_rate_limited_per_ip(api, services, nominatim):
    services.runs.stream = fake_stream()
    services.demo_limit.per_hour = 1
    await start(api, {"sample_id": "paris-one-day"})
    r = await api.post(f"{V1}/checks", json={"sample_id": "paris-one-day"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


# --- saved runs ---


async def test_saved_run_is_recorded(api, services, nominatim):
    services.runs.stream = fake_stream(
        {"opening_hours": [CLOSED], "travel_time": [TIGHT], "public_holidays": [NOTE]},
        fixes=fixes_for,
    )
    services.runs.llm = FakeClient()  # "fixes are on"; the fake stream never calls it
    run = await start(api, {"version_id": await saved_version(api)}, ANA)
    assert run["demo"] is False
    got = await events(api, run, ANA)
    types = [e["type"] for e in got]
    assert "step_started" in types and {"step": "fixes", "type": "step_started"} in got
    [fix_event] = [e["fix"] for e in got if e["type"] == "fix"]  # the valid one only
    done = got[-1]["run"]
    closed_id = done["violations"][0]["id"]  # blocker first
    assert fix_event["violation_ids"] == [closed_id]
    assert done["unfixed"] == [done["violations"][1]["id"]]
    # from the database once it's finished
    saved = (await api.get(run["result_url"], headers=ANA)).json()
    assert saved["status"] == "done"
    assert [v["id"] for v in saved["violations"]] == [v["id"] for v in done["violations"]]
    assert [f["id"] for f in saved["fixes"]] == [fix_event["id"]]
    assert saved["results"]["opening_hours"]["violations"] == 1
    assert await count(models.Fix) == 2  # the invalid one is kept for the fix-validity metric
    async with app.state.sessionmaker() as session:
        row = await session.get(models.CheckRun, uuid.UUID(run["run_id"]))
    assert row.status == "done" and row.latency_ms is not None and row.finished_at
    assert row.model_id == "claude-haiku-5-5" and row.prompt_version == FIX_PROMPT_VERSION
    assert row.config_snapshot["config"]["travel_buffer_min"] == 10
    assert row.cost_usd == 0  # the fake model made no calls
    # a finished run's event stream is its result
    [again] = await events(api, run, ANA)
    assert again["type"] == "done" and again["run"]["id"] == run["run_id"]


async def test_saved_runs_use_the_travellers_settings(api, services, nominatim):
    services.runs.stream = stream = fake_stream()
    await api.put(f"{V1}/me/preferences", json={"pace": "relaxed", "transport_modes": ["bike"]},
                  headers=ANA)  # fmt: skip
    await api.put(
        f"{V1}/me/check-config",
        json={"enabled": ["travel_time"], "travel_buffer_min": 20, "hotel_min_reviews": 3},
        headers=ANA,
    )
    await events(api, await start(api, {"version_id": await saved_version(api)}, ANA), ANA)
    inp = stream.calls[0]["input"]
    assert inp.preferences.pace == "relaxed" and inp.preferences.transport_modes == ["bike"]
    assert inp.config.enabled == ["travel_time"] and inp.config.travel_buffer_min == 20


async def test_saved_runs_need_the_owner(api, services, nominatim):
    services.runs.stream = fake_stream()
    version_id = await saved_version(api)
    r = await api.post(f"{V1}/checks", json={"version_id": version_id})
    assert r.status_code == 401
    r = await api.post(f"{V1}/checks", json={"version_id": version_id}, headers=BOB)
    assert r.status_code == 404
    run = await start(api, {"version_id": version_id}, ANA)
    await events(api, run, ANA)
    for headers in (None, BOB):
        assert (await api.get(run["result_url"], headers=headers)).status_code == 404
        assert (await api.get(run["events_url"], headers=headers)).status_code == 404


async def test_a_failed_run_is_an_error_event_and_recorded(api, services, respx_mock):
    search_route(respx_mock, q="Paris", featureType="settlement").respond(json=[])
    services.runs.stream = fake_stream()
    run = await start(api, {"version_id": await saved_version(api)}, ANA)
    [event] = await events(api, run, ANA)
    assert event == {"type": "error", "code": "city_not_found",
                     "message": "Couldn't find the city 'Paris'"}  # fmt: skip
    saved = (await api.get(run["result_url"], headers=ANA)).json()
    assert saved["status"] == "error" and saved["error"].startswith("city_not_found")


async def test_an_agent_crash_is_reported_not_raised(api, services, nominatim):
    async def broken(mcp, deps, inp, fixer):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    services.runs.stream = broken
    run = await start(api, {"sample_id": "paris-one-day"})
    [*_, event] = await events(api, run)
    assert event["type"] == "error" and event["code"] == "internal"


async def test_saved_runs_are_rate_limited_per_user(api, services, nominatim):
    services.runs.stream = fake_stream()
    services.check_limit.per_hour = 1
    version_id = await saved_version(api)
    await start(api, {"version_id": version_id}, ANA)
    r = await api.post(f"{V1}/checks", json={"version_id": version_id}, headers=ANA)
    assert r.status_code == 429


async def test_unfinished_runs_are_marked_interrupted_at_startup(api, services):
    version_id = uuid.UUID(await saved_version(api))
    async with app.state.sessionmaker() as session:
        session.add(
            models.CheckRun(
                id=uuid.uuid4(),
                version_id=version_id,
                status="running",
                config_snapshot={},
                model_id="x",
                prompt_version="x",
            )
        )
        await session.commit()
    await services.runs.mark_interrupted()
    async with app.state.sessionmaker() as session:
        statuses = (await session.scalars(select(models.CheckRun.status))).all()
    assert statuses == ["error"]


# --- fixes ---


async def run_with_fix(api, services):
    services.runs.stream = fake_stream(
        {"opening_hours": [CLOSED], "travel_time": [TIGHT]}, fixes=fixes_for
    )
    services.runs.llm = FakeClient()
    run = await start(api, {"version_id": await saved_version(api)}, ANA)
    done = (await events(api, run, ANA))[-1]["run"]
    return done["fixes"][0]


async def test_accepting_a_fix_makes_a_new_version(api, services, nominatim):
    fix = await run_with_fix(api, services)
    r = await api.post(f"{V1}/fixes/{fix['id']}/accept", headers=ANA)
    assert r.status_code == 201, r.text
    version = r.json()["version"]
    assert version["version_no"] == 2 and version["source"] == "fix"
    assert version["transport_modes"] == ["walk", "transit", "car"]  # the fix allowed a taxi
    again = await api.post(f"{V1}/fixes/{fix['id']}/accept", headers=ANA)
    assert again.status_code == 409
    # the next run on that version travels with the allowed modes
    services.runs.stream = stream = fake_stream()
    await events(api, await start(api, {"version_id": version["id"]}, ANA), ANA)
    assert stream.calls[0]["input"].preferences.transport_modes == ["walk", "transit", "car"]


async def test_fixes_belong_to_their_owner_and_invalid_ones_cant_be_accepted(
    api, services, nominatim
):
    fix = await run_with_fix(api, services)
    assert (await api.post(f"{V1}/fixes/{fix['id']}/accept", headers=BOB)).status_code == 404
    async with app.state.sessionmaker() as session:
        invalid = await session.scalar(select(models.Fix.id).where(~models.Fix.valid))
    assert (await api.post(f"{V1}/fixes/{invalid}/accept", headers=ANA)).status_code == 404
    assert (await api.post(f"{V1}/fixes/{fix['id']}/reject", headers=ANA)).status_code == 204
    assert (await api.post(f"{V1}/fixes/{fix['id']}/accept", headers=ANA)).status_code == 409
