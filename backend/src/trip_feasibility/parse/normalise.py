"""Parse step 3: day numbers become dates, and morning/afternoon/evening become the fixed
windows in itinerary.TIME_OF_DAY_WINDOWS. Consecutive stops in the same slot share its
window in text order, in equal parts ("Afternoon: a museum, then a walk along the river" is
13:00-15:00, then 15:00-17:00), so they don't overlap. Stops get ids s1, s2, ...
in text order."""

from datetime import date, datetime, time, timedelta

from trip_feasibility.itinerary import TIME_OF_DAY_WINDOWS, Stop
from trip_feasibility.parse.schema import ExtractedStop


def _slot_times(stops: list[ExtractedStop]) -> list[tuple[time | None, time | None]]:
    """(start, end) per stop: stated times, or its share of its slot's window."""
    times = [(s.start_time, s.end_time) for s in stops]
    i = 0
    while i < len(stops):
        slot = stops[i].time_of_day
        j = i + 1
        if slot is not None:
            while j < len(stops) and stops[j].time_of_day == slot and stops[j].day == stops[i].day:
                j += 1
            start, end = (datetime.combine(date.min, t) for t in TIME_OF_DAY_WINDOWS[slot])
            share = (end - start) / (j - i)
            for k in range(i, j):
                times[k] = (
                    (start + share * (k - i)).time(),
                    (start + share * (k - i + 1)).time(),
                )
        i = j
    return times


def normalise(stops: list[ExtractedStop], trip_start: date) -> list[Stop]:
    return [
        Stop(
            id=f"s{i}",
            day=s.day,
            date=trip_start + timedelta(days=s.day - 1),
            start_time=start,
            end_time=end,
            place_name=s.place_name,
            type=s.type,
        )
        for i, (s, (start, end)) in enumerate(zip(stops, _slot_times(stops), strict=True), 1)
    ]
