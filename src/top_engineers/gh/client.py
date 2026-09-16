"""GraphQL client with the three-limit taxonomy.

THREE distinct rate limits, three different responses. Conflating them wastes hours:

  * primary points (5,000/hr, ``rateLimit.remaining``) -> sleep until ``resetAt``
  * secondary/burst (HTTP 403, no budget signal, not cured by waiting) -> proactive pacing
  * per-query node ceiling (~500k) -> cap page sizes

GraphQL only. REST search trips secondary limits within ~150 requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from .limiter import AdaptiveConcurrency, RateLimiter

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"
MAX_ATTEMPTS = 5


class TransientError(RuntimeError):
    """Retryable: 5xx, connection errors, non-JSON 200, RATE_LIMITED inside a 200 body."""


class FatalGraphQLError(RuntimeError):
    """A query-shaped problem. Retrying an invalid query just burns the window."""


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    # Fall back to the gh CLI's token so a logged-in operator needs no extra setup.
    import subprocess

    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=True
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError("No GITHUB_TOKEN and `gh auth token` unavailable") from exc


class GraphQLClient:
    def __init__(
        self,
        limiter: RateLimiter | None = None,
        concurrency: AdaptiveConcurrency | None = None,
        token: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.limiter = limiter or RateLimiter()
        self.concurrency = concurrency or AdaptiveConcurrency()
        self._token = token or _token()
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "top-engineers/0.1",
            },
        )

    async def __aenter__(self) -> "GraphQLClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        await self._client.aclose()
        return False

    async def close(self) -> None:
        await self._client.aclose()

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with self.concurrency:
                    await self.limiter.acquire()
                    return await self._once(query, variables or {})
            except TransientError as exc:
                last = exc
                backoff = min(30.0, 2.0**attempt)
                log.warning("transient (%s/%s): %s; retry in %.1fs", attempt, MAX_ATTEMPTS, exc, backoff)
                await asyncio.sleep(backoff)
        raise TransientError(f"exhausted {MAX_ATTEMPTS} attempts: {last}")

    async def _once(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(GRAPHQL_URL, json={"query": query, "variables": variables})
        except httpx.HTTPError as exc:
            raise TransientError(f"connection: {exc}") from exc

        if resp.status_code in (403, 429):
            # Secondary/burst limit: no budget signal, and waiting does not cure it. Back the
            # concurrency off rather than sleeping on a number we do not have.
            self.concurrency.on_throttle()
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                await asyncio.sleep(min(60, int(retry_after)))
            raise TransientError(f"secondary rate limit (HTTP {resp.status_code})")

        if resp.status_code >= 500:
            raise TransientError(f"server error HTTP {resp.status_code}")

        if resp.status_code != 200:
            raise FatalGraphQLError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        # GUARD EVERY .json(). GitHub serves HTML error pages with HTTP 200; one unguarded
        # call destroyed a 90-minute run at 65%.
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise TransientError(f"non-JSON 200 ({resp.text[:120]!r})") from exc
        if not isinstance(payload, dict):
            raise TransientError(f"unexpected JSON 200 payload type {type(payload).__name__}")

        errors = payload.get("errors") or []
        if errors:
            types = {e.get("type") for e in errors if isinstance(e, dict)}
            # RATE_LIMITED arrives INSIDE a 200 body -- transient, not a query bug.
            if "RATE_LIMITED" in types:
                reset_at = (payload.get("data") or {}).get("rateLimit", {}).get("resetAt")
                await self.limiter.sleep_until_reset(reset_at)
                raise TransientError("RATE_LIMITED in 200 body")
            raise FatalGraphQLError(json.dumps(errors)[:600])

        data = payload.get("data")
        if data is None:
            raise TransientError("200 with null data")

        rl = data.get("rateLimit") or {}
        self.limiter.observe(rl.get("cost"), rl.get("remaining"))
        self.concurrency.on_success()

        remaining = rl.get("remaining")
        if isinstance(remaining, int) and remaining < 100:
            await self.limiter.sleep_until_reset(rl.get("resetAt"))

        return data
