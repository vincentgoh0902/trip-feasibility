from datetime import date, time

from trip_feasibility.itinerary import OsmRef, Stop


def stop(
    stop_id: str,
    on: date,
    start: str | None = None,
    end: str | None = None,
    *,
    type: str = "venue",
    osm: tuple[str, int] | None = ("way", 1),
    name: str | None = None,
) -> Stop:
    return Stop(
        id=stop_id,
        day=1,
        date=on,
        start_time=time.fromisoformat(start) if start else None,
        end_time=time.fromisoformat(end) if end else None,
        place_name=name or f"Place {stop_id}",
        type=type,
        osm=OsmRef(type=osm[0], id=osm[1]) if osm else None,
    )
