"""Running the checks over dataset itineraries and summarising the scores (see __init__)."""

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from trip_feasibility.agent.graph import CheckInput, CheckReport, Fixer, run_agent
from trip_feasibility.checks.config import CheckConfig
from trip_feasibility.checks.deps import CheckDeps, Unavailable
from trip_feasibility.config import Settings
from trip_feasibility.dataset.models import DatasetItinerary, Hotel
from trip_feasibility.dataset.split import UNSEEN_CITY
from trip_feasibility.dataset.validate import gold_violations
from trip_feasibility.evals.align import align
from trip_feasibility.evals.decisions import MatchDecisions
from trip_feasibility.evals.score import (
    PARSE_FIELDS,
    Counts,
    ParseCounts,
    Where,
    compare,
    findings,
    gold_label,
    parse_counts,
)
from trip_feasibility.itinerary import Stop
from trip_feasibility.parse.extract import MessagesClient
from trip_feasibility.parse.geocode import City, geocode_place, locate_city
from trip_feasibility.parse.normalise import normalise
from trip_feasibility.parse.pipeline import ParseError, parse_itinerary
from trip_feasibility.parse.schema import ExtractedStop
from trip_feasibility.sources.nominatim import fetch_osm_points, tags_key

Mode = Literal["gold", "parsed"]


@dataclass
class Outcome:
    """One itinerary's run."""

    id: str
    city: str
    counts: dict[str, Counts] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)  # check -> ok | unavailable
    unknown: dict[str, int] = field(default_factory=dict)  # check -> stops it couldn't judge
    parse: ParseCounts | None = None
    error: str | None = None  # why the run didn't happen (parse failure, data source down)
    hotel_unlabelled: bool = False  # hotel_red_flags left out of this itinerary's scores
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # fixes (PRD "fix validity"), when the run proposes them
    fixes_proposed: int = 0
    fixes_valid: int = 0
    fixable: int = 0  # warnings and blockers found
    fixed: int = 0  # of those, how many got a valid fix
    fix_tokens: tuple[int, int] = (0, 0)
    predicted: dict[str, str] = field(default_factory=dict)  # Finding.label() -> severity
    gold: dict[str, str] = field(default_factory=dict)
    undecided: int = 0  # uncertain name pairs still waiting for a human (evals/decisions.py)


def _gold_extracted(itin: DatasetItinerary) -> list[ExtractedStop]:
    assert itin.stops is not None
    return [ExtractedStop(**{f: getattr(s, f) for f in PARSE_FIELDS}) for s in itin.stops]


async def gold_stops(deps: CheckDeps, itin: DatasetItinerary, city: City) -> list[Stop]:
    """The gold stops as checkable Stops: gold ids, located from their OSM refs (the truth),
    or geocoded by name like the agent would when a stop has no ref."""
    assert itin.stops is not None
    refs = [s.osm for s in itin.stops if s.osm]
    points = await fetch_osm_points(deps, refs) if refs else {}
    out = []
    for stop, g in zip(normalise(_gold_extracted(itin), itin.trip_start), itin.stops, strict=True):
        update: dict[str, Any] = {"id": g.id, "osm": g.osm}
        point = points.get(tags_key(g.osm)) if g.osm else None
        if point is None:
            found = await geocode_place(deps, g.place_name, city)
            if found.place is not None:
                point = (found.place.lat, found.place.lon)
                update["osm"] = g.osm or found.place.osm
        if point is not None:
            update |= {"lat": point[0], "lon": point[1]}
        out.append(stop.model_copy(update=update))
    return out


async def evaluate_itinerary(
    deps: CheckDeps,
    itin: DatasetItinerary,
    hotels: dict[str, Hotel],
    city: City,
    *,
    mode: Mode,
    config: CheckConfig,
    settings: Settings,
    client: MessagesClient | None = None,
    fixes: bool = False,
    decisions: MatchDecisions | None = None,
) -> Outcome:
    out = Outcome(id=itin.id, city=itin.city)
    gold = gold_violations(itin, hotels)
    hotel = hotels.get(itin.hotel or "")
    out.hotel_unlabelled = hotel is not None and hotel.red_flags is None
    if out.hotel_unlabelled:
        gold = [g for g in gold if g.check != "hotel_red_flags"]
    out.gold = {gold_label(g): g.severity for g in gold}
    started = time.monotonic()

    try:
        if mode == "gold":
            # gold stops are dev only (parser debugging): this mode runs the checks alone
            stops = await gold_stops(deps, itin, city)
        else:
            assert client is not None
            parsed = await parse_itinerary(
                deps, client, settings, itin.text, city=city.name, trip_start=itin.trip_start
            )
            stops = parsed.stops
            x = parsed.extraction
            out.input_tokens, out.output_tokens = x.input_tokens, x.output_tokens
            if itin.stops is not None:
                pairs = align(x.extraction.stops, itin.stops)
                out.parse = parse_counts(x.extraction.stops, _gold_extracted(itin), pairs)
        fixer = None
        if fixes:
            assert client is not None
            fixer = Fixer(client, settings.fix_model, settings.fix_effort, city)
        report: CheckReport
        report, fix_report = await run_agent(
            deps,
            CheckInput(
                stops=stops,
                city=city.name,
                country_code=city.country_code.upper() if city.country_code else None,
                preferences=itin.preferences,
                config=config,
            ),
            fixer,
        )
        if fix_report is not None:
            out.fixes_proposed = len(fix_report.fixes)
            out.fixes_valid = sum(f.valid for f in fix_report.fixes)
            out.fixable = len(fix_report.violation_ids)
            out.fixed = out.fixable - len(fix_report.unfixed)
            out.fix_tokens = (fix_report.input_tokens, fix_report.output_tokens)
    except (ParseError, Unavailable) as e:
        out.error = e.code if isinstance(e, ParseError) else f"unavailable: {e}"
        if isinstance(e, ParseError) and itin.stops is not None:
            out.parse = ParseCounts(gold_stops=len(itin.stops))
        out.counts = compare([], gold)  # everything missed
        out.seconds = time.monotonic() - started
        return out

    predicted = findings(report.violations, stops)
    if out.hotel_unlabelled:
        predicted = [f for f in predicted if f.check != "hotel_red_flags"]
    out.predicted = {f.label(): f.severity for f in predicted}
    where = Where(itin.id, itin.split, decisions) if decisions is not None else None
    out.counts = compare(predicted, gold, itin.unknowns, where)
    if decisions is not None:
        out.undecided = sum(p.itinerary == itin.id for p in decisions.pending.values())
    out.statuses = {n: r.status for n, r in report.results.items()}
    out.unknown = {n: len(r.unknown) for n, r in report.results.items()}
    out.seconds = time.monotonic() - started
    return out


async def evaluate(
    deps: CheckDeps,
    itineraries: list[DatasetItinerary],
    hotels: dict[str, Hotel],
    *,
    mode: Mode,
    config: CheckConfig,
    settings: Settings,
    client: MessagesClient | None = None,
    fixes: bool = False,
    decisions: MatchDecisions | None = None,
    progress: Any = None,
) -> list[Outcome]:
    cities: dict[str, City | None] = {}
    outcomes = []
    for itin in itineraries:
        if itin.city not in cities:
            cities[itin.city] = await locate_city(deps, itin.city.title())
        city = cities[itin.city]
        if city is None:
            outcome = Outcome(id=itin.id, city=itin.city, error="city_not_found")
        else:
            outcome = await evaluate_itinerary(
                deps,
                itin,
                hotels,
                city,
                mode=mode,
                config=config,
                settings=settings,
                client=client,
                fixes=fixes,
                decisions=decisions,
            )
        outcomes.append(outcome)
        if progress:
            progress(outcome)
    return outcomes


def _check_metrics(outcomes: list[Outcome]) -> dict[str, Any]:
    per_check: dict[str, Counts] = {}
    overall = Counts()
    for o in outcomes:
        for check, c in o.counts.items():
            per_check.setdefault(check, Counts()).add(c)
            overall.add(c)
    return {
        "overall": overall.metrics(),
        "per_check": {k: per_check[k].metrics() for k in sorted(per_check)},
    }


def summarise(outcomes: list[Outcome]) -> dict[str, Any]:
    """The metrics stored in eval_runs. Seen and unseen (Vienna) cities are also reported
    apart whenever Vienna is in the run (the final test run only)."""
    summary: dict[str, Any] = {"itineraries": len(outcomes), **_check_metrics(outcomes)}
    if any(o.city == UNSEEN_CITY for o in outcomes):
        summary["seen_cities"] = _check_metrics([o for o in outcomes if o.city != UNSEEN_CITY])
        summary["unseen_city"] = _check_metrics([o for o in outcomes if o.city == UNSEEN_CITY])

    unavailable: dict[str, int] = {}
    unknown: dict[str, int] = {}
    for o in outcomes:
        for check, status in o.statuses.items():
            unavailable[check] = unavailable.get(check, 0) + (status == "unavailable")
        for check, n in o.unknown.items():
            unknown[check] = unknown.get(check, 0) + n
    summary["coverage"] = {"unavailable_runs": unavailable, "unknown_stops": unknown}
    summary["errors"] = {o.id: o.error for o in outcomes if o.error}
    summary["hotel_unlabelled"] = [o.id for o in outcomes if o.hotel_unlabelled]
    summary["undecided_pairs"] = sum(o.undecided for o in outcomes)

    parses = [o.parse for o in outcomes if o.parse is not None]
    if parses:
        total = ParseCounts()
        for p in parses:
            total.add(p)
        summary["parse"] = total.metrics()
        summary["tokens"] = {
            "input": sum(o.input_tokens for o in outcomes),
            "output": sum(o.output_tokens for o in outcomes),
        }
    proposed = sum(o.fixes_proposed for o in outcomes)
    if proposed or any(o.fixable for o in outcomes):
        fixable = sum(o.fixable for o in outcomes)
        summary["fixes"] = {
            "proposed": proposed,
            "valid": sum(o.fixes_valid for o in outcomes),
            "validity": sum(o.fixes_valid for o in outcomes) / proposed if proposed else None,
            "violations": fixable,
            "violations_fixed": sum(o.fixed for o in outcomes),
            "coverage": sum(o.fixed for o in outcomes) / fixable if fixable else None,
            "tokens": {
                "input": sum(o.fix_tokens[0] for o in outcomes),
                "output": sum(o.fix_tokens[1] for o in outcomes),
            },
        }
    seconds = sorted(o.seconds for o in outcomes)
    if seconds:
        p95 = statistics.quantiles(seconds, n=20)[-1] if len(seconds) > 1 else seconds[0]
        summary["seconds"] = {"median": statistics.median(seconds), "p95": p95}
    return summary
