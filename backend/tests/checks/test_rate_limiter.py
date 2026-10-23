import asyncio
import time

import pytest

from trip_feasibility.checks.deps import RateLimiter, Unavailable


async def test_calls_are_spaced_by_the_minimum_interval():
    limiter = RateLimiter("svc", min_interval_s=0.1, cooldown_s=60)
    started = time.monotonic()
    await asyncio.gather(*(limiter.wait() for _ in range(3)))
    assert time.monotonic() - started >= 0.2


async def test_back_off_pauses_all_calls():
    limiter = RateLimiter("svc", min_interval_s=0, cooldown_s=60)
    await limiter.wait()
    limiter.back_off()
    with pytest.raises(Unavailable, match="svc asked us to back off"):
        await limiter.wait()


async def test_calls_resume_after_the_cooldown():
    limiter = RateLimiter("svc", min_interval_s=0, cooldown_s=0.05)
    limiter.back_off()
    await asyncio.sleep(0.06)
    await limiter.wait()
