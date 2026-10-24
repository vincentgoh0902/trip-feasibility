from mcp import Client

from trip_feasibility.mcp_server import server


async def test_tools_are_listed_with_output_schemas(check_deps):
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "public_holidays",
        "opening_hours",
        "travel_time",
        "daily_load",
        "hotel_red_flags",
    }
    assert all(t.output_schema for t in tools.values())


async def test_call_tool_returns_structured_check_result(check_deps, respx_mock):
    respx_mock.get("https://nominatim.test/lookup").respond(
        json=[
            {
                "osm_type": "way",
                "osm_id": 1,
                "category": "tourism",
                "type": "museum",
                "extratags": {"opening_hours": "Tu-Su 10:00-18:00"},
            }
        ]
    )
    stops = [
        {
            "id": "s1",
            "day": 1,
            "date": "2026-10-26",
            "start_time": "11:00",
            "end_time": "12:00",
            "place_name": "Museum",
            "type": "venue",
            "osm": {"type": "way", "id": 1},
        }
    ]
    async with Client(server) as client:
        result = await client.call_tool("opening_hours", {"stops": stops, "country_code": "FR"})
    assert not result.is_error
    out = result.structured_content
    assert out["status"] == "ok"
    assert [(v["severity"], v["stop_ids"]) for v in out["violations"]] == [("blocker", ["s1"])]


async def test_invalid_input_is_a_tool_error(check_deps):
    async with Client(server) as client:
        result = await client.call_tool(
            "public_holidays", {"stops": [{"id": "x"}], "country_code": "FR"}
        )
    assert result.is_error
