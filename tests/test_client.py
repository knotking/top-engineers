"""The three-limit taxonomy and the guards around .json()."""

from __future__ import annotations

import httpx
import pytest

from top_engineers.gh.client import FatalGraphQLError, GraphQLClient, TransientError
from top_engineers.gh.limiter import AdaptiveConcurrency, RateLimiter

QUERY = "query { viewer { login } }"


def make_client(handler) -> GraphQLClient:
    c = GraphQLClient(
        limiter=RateLimiter(min_interval=0.0),
        concurrency=AdaptiveConcurrency(start=2, cap=2),
        token="fake",
    )
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


async def test_html_error_page_with_http_200_is_transient():
    """GitHub serves HTML error pages with HTTP 200.

    One unguarded .json() destroyed a 90-minute run at 65%.
    """
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text="<html><body>Server Error</body></html>")
        return httpx.Response(200, json={"data": {"viewer": {"login": "x"},
                                                  "rateLimit": {"cost": 1, "remaining": 4999}}})

    c = make_client(handler)
    data = await c.execute(QUERY)
    assert data["viewer"]["login"] == "x"
    assert calls["n"] == 2, "should have retried rather than crashed"
    await c.close()


async def test_rate_limited_inside_200_body_is_transient(monkeypatch):
    import asyncio

    async def no_sleep(d):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={
                "errors": [{"type": "RATE_LIMITED", "message": "quota"}],
                "data": {"rateLimit": {"resetAt": "2026-09-16T12:00:00Z"}},
            })
        return httpx.Response(200, json={"data": {"ok": 1, "rateLimit": {"cost": 1, "remaining": 4999}}})

    c = make_client(handler)
    assert (await c.execute(QUERY))["ok"] == 1
    assert calls["n"] == 2
    await c.close()


async def test_403_halves_concurrency_and_retries(monkeypatch):
    import asyncio

    async def no_sleep(d):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, text="secondary rate limit")
        return httpx.Response(200, json={"data": {"ok": 1, "rateLimit": {"cost": 1, "remaining": 4999}}})

    c = make_client(handler)
    before = c.concurrency.limit
    await c.execute(QUERY)
    assert c.concurrency.limit == max(1, before // 2), "403 must HALVE concurrency"
    await c.close()


async def test_5xx_is_retried(monkeypatch):
    import asyncio

    async def no_sleep(d):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json={"data": {"ok": 1, "rateLimit": {"cost": 1, "remaining": 4999}}})

    c = make_client(handler)
    await c.execute(QUERY)
    assert calls["n"] == 3
    await c.close()


async def test_query_error_is_fatal_not_retried():
    """Retrying an invalid query just burns the window."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"errors": [{"type": "INVALID", "message": "bad field"}]})

    c = make_client(handler)
    with pytest.raises(FatalGraphQLError):
        await c.execute(QUERY)
    assert calls["n"] == 1, "must not retry a malformed query"
    await c.close()


async def test_cost_is_accumulated_from_response():
    def handler(request):
        return httpx.Response(200, json={"data": {"ok": 1, "rateLimit": {"cost": 14, "remaining": 4986}}})

    c = make_client(handler)
    await c.execute(QUERY)
    assert c.limiter.points_spent == 14
    await c.close()
