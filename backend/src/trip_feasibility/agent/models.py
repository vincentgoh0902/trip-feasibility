"""What goes into a check run and what comes out of it."""

from pydantic import BaseModel

from trip_feasibility.checks.config import CHECK_NAMES, CheckConfig
from trip_feasibility.checks.models import SEVERITY_RANK, CheckResult, Violation
from trip_feasibility.itinerary import Stop, TravelPreferences


class CheckInput(BaseModel):
    stops: list[Stop]
    city: str  # in English, e.g. "Paris"
    country_code: str | None  # ISO 3166-1 alpha-2; holiday checks need it
    regions: list[str] = []  # ISO 3166-2, any level; decides whether regional holidays apply
    preferences: TravelPreferences = TravelPreferences()
    config: CheckConfig = CheckConfig()


class CheckReport(BaseModel):
    input: CheckInput
    results: dict[str, CheckResult]  # in CHECK_NAMES order, enabled checks only
    violations: list[Violation]  # all checks, most severe first, then by first stop

    @classmethod
    def from_results(cls, inp: CheckInput, results: dict[str, CheckResult]) -> "CheckReport":
        order = {s.id: i for i, s in enumerate(inp.stops)}

        def key(v: Violation) -> tuple:
            first = min((order.get(i, len(order)) for i in v.stop_ids), default=len(order))
            return (-SEVERITY_RANK[v.severity], first, v.check_name)

        results = {n: results[n] for n in CHECK_NAMES if n in results}
        violations = sorted((v for r in results.values() for v in r.violations), key=key)
        return cls(input=inp, results=results, violations=violations)
