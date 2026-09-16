"""Shared pacing + adaptive concurrency.

Pacing must be ONE SHARED token bucket. N workers each sleeping independently multiplies the
request rate by N and walks straight back into the secondary limit.

And the sleep happens OUTSIDE the lock. Holding the lock while sleeping serialises every
worker and silently undoes the concurrency you just added.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from ..config import (
    CONCURRENCY_MAX,
    CONCURRENCY_START,
    MIN_INTERVAL_S,
    THROTTLE_COOLDOWN_S,
)

log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, min_interval: float = MIN_INTERVAL_S) -> None:
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        # Instrument our own counter from rateLimit.cost: `gh api rate_limit` disagreed with
        # observed consumption three separate times, so the reported budget is not a
        # reliable predictor.
        self.points_spent = 0
        self.requests = 0
        self.backoffs = 0
        self.remaining: int | None = None

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            wait = max(0.0, self._next_at - now)
            # Reserve this slot, then release the lock BEFORE sleeping.
            self._next_at = max(now, self._next_at) + self.min_interval
        if wait > 0:
            await asyncio.sleep(wait)

    def observe(self, cost: int | None, remaining: int | None) -> None:
        self.requests += 1
        if cost:
            self.points_spent += cost
        if remaining is not None:
            self.remaining = remaining

    async def sleep_until_reset(self, reset_at: str | None) -> None:
        """Primary-budget exhaustion: sleep to resetAt, NEVER a fixed interval.

        A flat 60s sleep wakes into the same empty budget and burns the remainder of the
        window into hard RATE_LIMITED errors.
        """
        self.backoffs += 1
        delay = 60.0
        if reset_at:
            try:
                target = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                delay = max(1.0, (target - datetime.now(timezone.utc)).total_seconds() + 2)
            except ValueError:
                log.warning("unparseable resetAt %r; falling back to 60s", reset_at)
        log.warning("primary budget exhausted; sleeping %.0fs until %s", delay, reset_at)
        await asyncio.sleep(delay)


class AdaptiveConcurrency:
    """Start 3, +1 on a clean window, HALVE on the first 403, cap 8.

    Concurrency converts a latency problem into a budget problem; halving on the first 403 is
    what stops the secondary limit becoming the new wall.
    """

    def __init__(
        self,
        start: int = CONCURRENCY_START,
        cap: int = CONCURRENCY_MAX,
        clean_window: int = 20,
        throttle_cooldown: float = THROTTLE_COOLDOWN_S,
    ) -> None:
        self.cap = cap
        self.limit = max(1, min(start, cap))
        self.clean_window = clean_window
        # One throttling EPISODE must halve once, not once per in-flight request. Without
        # this, every worker airborne when the 403 burst lands reports it independently and
        # the limit collapses geometrically (observed: 8 -> 4 -> 2 -> 1 in two seconds),
        # after which clean_window successes per step make recovery glacial.
        self.throttle_cooldown = throttle_cooldown
        self._last_throttle = float("-inf")
        self._clean = 0
        self._sem = asyncio.Semaphore(self.limit)
        self._slack = 0

    async def __aenter__(self) -> "AdaptiveConcurrency":
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc: object) -> bool:
        # Shrinking cannot revoke a permit already handed out, so absorb the difference here
        # instead of releasing back into a semaphore that is now too wide.
        if self._slack > 0:
            self._slack -= 1
        else:
            self._sem.release()
        return False

    def on_success(self) -> None:
        self._clean += 1
        if self._clean >= self.clean_window and self.limit < self.cap:
            self._clean = 0
            self.limit += 1
            self._sem.release()
            log.info("concurrency up -> %d", self.limit)

    def on_throttle(self) -> None:
        self._clean = 0
        now = time.monotonic()
        if now - self._last_throttle < self.throttle_cooldown:
            # Same episode: the other in-flight requests are reporting the same burst.
            log.debug("throttle within cooldown; concurrency held at %d", self.limit)
            return
        self._last_throttle = now
        new_limit = max(1, self.limit // 2)
        if new_limit != self.limit:
            self._slack += self.limit - new_limit
            self.limit = new_limit
            log.warning("throttled; concurrency halved -> %d", self.limit)
