from datetime import date, time

from trip_feasibility.parse.normalise import normalise
from trip_feasibility.parse.schema import ExtractedStop, Extraction, validate_extraction

TUE = date(2026, 12, 8)


def test_valid_extraction_from_json():
    raw = '{"stops": [{"day": 1, "start_time": "9:00", "place_name": "Louvre", "type": "venue"}]}'
    result = validate_extraction(raw)
    assert isinstance(result, Extraction)
    assert result.stops[0].start_time == time(9)


def test_invalid_extraction_returns_the_error_for_a_retry():
    result = validate_extraction({"stops": [{"day": 0, "place_name": "x", "type": "museum"}]})
    assert isinstance(result, str)
    assert "day" in result and "type" in result


def test_unknown_fields_are_rejected():
    result = validate_extraction({"stops": [{"day": 1, "place_name": "x", "type": "venue",
                                             "lat": 48.8}]})  # fmt: skip
    assert isinstance(result, str) and "lat" in result


def test_durations_are_kept_apart_from_times():
    stop = ExtractedStop(day=1, start_time="09:15", duration_min=30, duration_max=40,
                         place_name="A", type="venue")  # fmt: skip
    assert stop.end_time is None and (stop.duration_min, stop.duration_max) == (30, 40)
    at_least = ExtractedStop(day=1, duration_min=60, place_name="A", type="venue")
    assert at_least.duration_max is None


def test_travel_claim_fields():
    stop = ExtractedStop(day=1, travel_start="9:15", travel_min=30, travel_max=40,
                         place_name="A", type="venue")  # fmt: skip
    assert stop.travel_start == time(9, 15) and stop.start_time is None
    result = validate_extraction({"stops": [{"day": 1, "place_name": "x", "type": "venue",
                                             "travel_max": 40}]})  # fmt: skip
    assert isinstance(result, str) and "travel_max" in result


def test_duration_max_needs_a_smaller_min():
    for bad in ({"duration_max": 40}, {"duration_min": 50, "duration_max": 40},
                {"duration_min": 0}):  # fmt: skip
        result = validate_extraction({"stops": [{"day": 1, "place_name": "x", "type": "venue",
                                                 **bad}]})  # fmt: skip
        assert isinstance(result, str) and "duration" in result


def test_normalise_dates_ids_and_time_windows():
    stops = normalise(
        [
            ExtractedStop(
                day=1, start_time="09:00", end_time="12:00", place_name="A", type="venue"
            ),  # fmt: skip
            ExtractedStop(day=2, time_of_day="afternoon", place_name="B", type="venue"),
            ExtractedStop(day=3, place_name="C", type="meal"),
        ],
        TUE,
    )
    assert [s.id for s in stops] == ["s1", "s2", "s3"]
    assert [s.date for s in stops] == [TUE, date(2026, 12, 9), date(2026, 12, 10)]
    assert (stops[0].start_time, stops[0].end_time) == (time(9), time(12))
    assert (stops[1].start_time, stops[1].end_time) == (time(13), time(17))
    assert (stops[2].start_time, stops[2].end_time) == (None, None)
    assert all(s.osm is None and s.lat is None for s in stops)
