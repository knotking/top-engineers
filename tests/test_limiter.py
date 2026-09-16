"""Shared pacing and adaptive concurrency."""

from __future__ import annotations

import asyncio
import time

import pytest

from top_engineers.gh.limiter import AdaptiveConcurrency, RateLimiter


async def test_shared_bucket_paces_across_workers():
    """N workers must share ONE bucket.

    Per-worker sleeping multiplies the request rate by N and walks back into the secondary
    limit, which is exactly the failure this class exists to prevent.
    """
    limiter = RateLimiter(min_interval=0.05)
    stamps: list[float] = []

    async def worker():
        for _ in range(4):
            await limiter.acquire()
            stamps.append(time.monotonic())

    await asyncio.gather(*(worker() for _ in range(4)))

    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.04, f"bucket not shared; min gap {min(gaps):.4f}s"


async def test_sleep_happens_outside_the_lock():
    """Holding the lock while sleeping serialises workers and undoes the concurrency."""
    limiter = RateLimiter(min_interval=0.2)
    await limiter.acquire()  # prime: next acquire must wait

    start = time.monotonic()
    await asyncio.gather(*(limiter.acquire() for _ in range(4)))
    elapsed = time.monotonic() - start

    # Reservations are handed out immediately; only the sleeps overlap in time.
    assert elapsed < 0.2 * 6, f"workers serialised: {elapsed:.2f}s"


async def test_concurrency_halves_on_throttle_and_grows_on_clean():
    c = AdaptiveConcurrency(start=4, cap=8, clean_window=2)
    assert c.limit == 4
    c.on_throttle()
    assert c.limit == 2, "must HALVE on the first 403, not decrement"
    c.on_success()
    c.on_success()
    assert c.limit == 3, "+1 on a clean window"


async def test_concurrency_never_exceeds_cap():
    c = AdaptiveConcurrency(start=7, cap=8, clean_window=1)
    for _ in range(50):
        c.on_success()
    assert c.limit <= 8


async def test_concurrency_semaphore_bounds_in_flight():
    c = AdaptiveConcurrency(start=2, cap=2, clean_window=99)
    in_flight = 0
    peak = 0

    async def task():
        nonlocal in_flight, peak
        async with c:
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1

    await asyncio.gather(*(task() for _ in range(8)))
    assert peak <= 2, f"semaphore did not bound in-flight work (peak {peak})"


async def test_sleep_until_reset_uses_reset_at_not_fixed_interval(monkeypatch):
    """A flat 60s sleep wakes into the same empty budget and burns the window."""
    limiter = RateLimiter()
    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    from datetime import datetime, timedelta, timezone

    reset = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat().replace("+00:00", "Z")
    await limiter.sleep_until_reset(reset)
    assert slept and 290 < slept[0] < 310, f"expected ~300s to resetAt, got {slept}"


async def test_own_counter_tracks_cost():
    """Reported budget is not a reliable predictor; instrument from rateLimit.cost."""
    limiter = RateLimiter()
    limiter.observe(14, 4986)
    limiter.observe(14, 4972)
    assert limiter.points_spent == 28
    assert limiter.requests == 2


async def test_one_throttle_episode_halves_once(monkeypatch):
    """A 403 burst must not collapse concurrency geometrically.

    Observed in a live run: three in-flight workers each hit the same burst and each called
    on_throttle, taking the limit 8 -> 4 -> 2 -> 1 in two seconds. Recovery then needs
    clean_window successes per step, so the run crawls.
    """
    c = AdaptiveConcurrency(start=8, cap=8, clean_window=20, throttle_cooldown=15.0)
    c.on_throttle()
    c.on_throttle()
    c.on_throttle()
    assert c.limit == 4, "all three reports belong to one episode"


async def test_a_later_episode_halves_again(monkeypatch):
    import time as _time

    clock = {"t": 1000.0}
    monkeypatch.setattr(_time, "monotonic", lambda: clock["t"])
    from top_engineers.gh import limiter as limmod

    monkeypatch.setattr(limmod.time, "monotonic", lambda: clock["t"])

    c = AdaptiveConcurrency(start=8, cap=8, clean_window=20, throttle_cooldown=15.0)
    c.on_throttle()
    assert c.limit == 4
    clock["t"] += 20.0  # a genuinely new episode, past the cooldown
    c.on_throttle()
    assert c.limit == 2


async def test_cooldown_does_not_block_growth():
    c = AdaptiveConcurrency(start=8, cap=8, clean_window=2, throttle_cooldown=15.0)
    c.on_throttle()
    assert c.limit == 4
    for _ in range(2):
        c.on_success()
    assert c.limit == 5, "recovery must still work after a throttle"
