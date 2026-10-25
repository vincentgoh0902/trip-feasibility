"""Per-user and per-IP limits on expensive routes (PRD: e.g. 20 check runs per hour).

In memory, per process: right for one API container. Several containers would need the
counts in Postgres. A sliding window of one hour: each key keeps the times of its recent
calls.
"""

import time
from collections import defaultdict, deque

from trip_feasibility.errors import AppError

WINDOW_S = 3600.0


class SlidingWindowLimiter:
    def __init__(self, name: str, per_hour: int, window_s: float = WINDOW_S) -> None:
        self.name, self.per_hour, self.window_s = name, per_hour, window_s
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        """Count one call for `key`, or raise 429 if it has used up the window."""
        now = time.monotonic()
        calls = self._calls[key]
        while calls and calls[0] <= now - self.window_s:
            calls.popleft()
        if len(calls) >= self.per_hour:
            wait_min = max(1, round((calls[0] + self.window_s - now) / 60))
            raise AppError(
                429,
                "rate_limited",
                f"Too many {self.name} in the last hour (limit {self.per_hour}); "
                f"try again in about {wait_min} minute(s)",
            )
        calls.append(now)
        if len(self._calls) > 10_000:  # forget idle keys so memory stays bounded
            self._prune(now)

    def _prune(self, now: float) -> None:
        for key in [k for k, c in self._calls.items() if not c or c[-1] <= now - self.window_s]:
            del self._calls[key]
