"""The parse pipeline end to end: text → extracted, normalised, geocoded stops.

Step 5 (the user reviews the stops) happens in the frontend; checks run on the stops the
user confirms, not on this output directly.
"""

from dataclasses import dataclass
from datetime import date

from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.config import Settings
from trip_feasibility.itinerary import Stop
from trip_feasibility.parse.extract import (
    ExtractionError,
    ExtractionResult,
    MessagesClient,
    extract_stops,
)
from trip_feasibility.parse.geocode import City, Geocode, geocode_stops, locate_city
from trip_feasibility.parse.normalise import normalise


class ParseError(Exception):
    """code: city_not_found, or an ExtractionError code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedItinerary:
    city: City
    stops: list[Stop]  # with osm/lat/lon where geocoding found the place
    geocodes: dict[str, Geocode]  # by stop id; place is None when unresolved
    extraction: ExtractionResult

    @property
    def unresolved(self) -> list[str]:
        return [stop_id for stop_id, g in self.geocodes.items() if g.place is None]


async def parse_itinerary(
    deps: CheckDeps,
    client: MessagesClient,
    settings: Settings,
    text: str,
    *,
    city: str,
    trip_start: date,
) -> ParsedItinerary:
    located_city = await locate_city(deps, city)
    if located_city is None:
        raise ParseError("city_not_found", f"Couldn't find the city {city!r} in OpenStreetMap")
    try:
        extraction = await extract_stops(
            client,
            text,
            city=located_city.name,
            trip_start=trip_start,
            model=settings.extraction_model,
            effort=settings.extraction_effort,
        )
    except ExtractionError as e:
        raise ParseError(e.code, str(e)) from e
    stops = normalise(extraction.extraction.stops, trip_start)
    stops, geocodes = await geocode_stops(deps, stops, located_city)
    return ParsedItinerary(located_city, stops, geocodes, extraction)
