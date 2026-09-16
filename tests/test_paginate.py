"""Date-slice recursion around the SILENT search cap, and overflow drain."""

from __future__ import annotations

import logging
from datetime import date

import pytest

from top_engineers.config import SEARCH_RESULT_CAP
from top_engineers.gh.paginate import drain_overflow, iter_search


class FakeClient:
    """Stands in for GraphQLClient; records every query it is asked to run."""

    def __init__(self, counts, page_size=100):
        self.counts = counts          # {(since, until): issueCount}
        self.page_size = page_size
        self.queries: list[str] = []

    def _count_for(self, q):
        for (since, until), n in self.counts.items():
            if f"merged:{since}..{until}" in q:
                return n
        return 0

    async def execute(self, query, variables=None):
        variables = variables or {}
        self.queries.append(query)
        if "issueCount" in query and "nodes" not in query:
            return {"search": {"issueCount": self._count_for(variables["q"])},
                    "rateLimit": {"cost": 1, "remaining": 5000}}
        total = self._count_for(variables["q"])
        served = min(total, self.page_size)
        return {
            "search": {
                "issueCount": total,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"number": i, "title": "fix: x"} for i in range(served)],
            },
            "rateLimit": {"cost": 1, "remaining": 5000},
        }


async def test_range_under_cap_is_not_split():
    c = FakeClient({("2026-06-18", "2026-06-25"): 50})
    out = [n async for n in iter_search(c, "fix", date(2026, 6, 18), date(2026, 6, 25))]
    assert len(out) == 50


async def test_range_over_cap_splits_recursively():
    """Search caps at 1,000 SILENTLY -- no error, no warning, just a short result set."""
    counts = {
        ("2026-06-18", "2026-06-25"): SEARCH_RESULT_CAP + 500,   # over cap -> must split
        ("2026-06-18", "2026-06-21"): 400,
        ("2026-06-22", "2026-06-25"): 400,
    }
    c = FakeClient(counts, page_size=400)
    out = [n async for n in iter_search(c, "fix", date(2026, 6, 18), date(2026, 6, 25))]
    assert len(out) == 800, "both halves must be fetched"


async def test_single_day_over_cap_logs_data_loss(caplog):
    """A single day cannot be split further -- that is REAL data loss the operator must know."""
    c = FakeClient({("2026-06-18", "2026-06-18"): SEARCH_RESULT_CAP + 250}, page_size=100)
    stats = {}
    with caplog.at_level(logging.ERROR):
        [n async for n in iter_search(c, "fix", date(2026, 6, 18), date(2026, 6, 18), stats)]
    assert any("DATA LOSS" in r.message for r in caplog.records)
    assert stats["uncapped_days"][0]["day"] == "2026-06-18"
    assert stats["uncapped_days"][0]["issue_count"] == SEARCH_RESULT_CAP + 250


async def test_zero_results_short_circuits():
    c = FakeClient({("2026-06-18", "2026-06-25"): 0})
    out = [n async for n in iter_search(c, "hotfix", date(2026, 6, 18), date(2026, 6, 25))]
    assert out == []


class DrainClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    async def execute(self, query, variables=None):
        self.calls += 1
        page = self.pages.pop(0)
        return {"repository": {"pullRequest": {"reviews": page}},
                "rateLimit": {"cost": 1, "remaining": 5000}}


async def test_drain_follows_has_next_page():
    """Silent truncation is BIASED -- it deflates the busiest, most-reviewed PRs."""
    pr = {
        "number": 1,
        "reviews": {
            "totalCount": 45,
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "nodes": [{"state": "APPROVED"} for _ in range(20)],
        },
    }
    client = DrainClient([
        {"pageInfo": {"hasNextPage": True, "endCursor": "c2"},
         "nodes": [{"state": "COMMENTED"} for _ in range(20)]},
        {"pageInfo": {"hasNextPage": False, "endCursor": None},
         "nodes": [{"state": "COMMENTED"} for _ in range(5)]},
    ])
    drained = await drain_overflow(client, pr)
    assert drained["reviews"] == 25
    assert len(pr["reviews"]["nodes"]) == 45, "must end with the full set"
    assert pr["reviews"]["pageInfo"]["hasNextPage"] is False


async def test_no_drain_when_no_overflow():
    pr = {"number": 1, "reviews": {"pageInfo": {"hasNextPage": False}, "nodes": []}}
    client = DrainClient([])
    assert await drain_overflow(client, pr) == {}
    assert client.calls == 0
