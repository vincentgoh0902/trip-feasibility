from typing import Any, Literal

from pydantic import BaseModel

Severity = Literal["info", "warning", "blocker"]
SEVERITY_RANK: dict[Severity, int] = {"info": 0, "warning": 1, "blocker": 2}


class Violation(BaseModel):
    check_name: str
    severity: Severity
    stop_ids: list[str]
    message: str
    evidence: dict[str, Any]
    source: str


class UnknownStop(BaseModel):
    """A stop the check could not evaluate (e.g. no opening hours in OSM). Not a violation."""

    stop_id: str
    reason: str


class CheckResult(BaseModel):
    check_name: str
    status: Literal["ok", "unavailable"]
    violations: list[Violation] = []
    unknown: list[UnknownStop] = []
    checked_stop_ids: list[str] = []
    error: str | None = None

    @classmethod
    def unavailable(cls, check_name: str, error: str) -> "CheckResult":
        return cls(check_name=check_name, status="unavailable", error=error)
