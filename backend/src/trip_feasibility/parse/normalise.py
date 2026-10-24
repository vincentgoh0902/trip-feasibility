"""Parse step 3: day numbers become dates, and morning/afternoon/evening become the fixed
windows in itinerary.TIME_OF_DAY_WINDOWS. Stops get ids s1, s2, ... in text order."""

from datetime import date, timedelta

from trip_feasibility.itinerary import TIME_OF_DAY_WINDOWS, Stop
from trip_feasibility.parse.schema import ExtractedStop


def normalise(stops: list[ExtractedStop], trip_start: date) -> list[Stop]:
    out = []
    for i, s in enumerate(stops, 1):
        start, end = s.start_time, s.end_time
        if s.time_of_day is not None:
            start, end = TIME_OF_DAY_WINDOWS[s.time_of_day]
        out.append(
            Stop(
                id=f"s{i}",
                day=s.day,
                date=trip_start + timedelta(days=s.day - 1),
                start_time=start,
                end_time=end,
                place_name=s.place_name,
                type=s.type,
            )
        )
    return out
