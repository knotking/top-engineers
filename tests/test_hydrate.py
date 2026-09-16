"""Hydration batching, resume, and the no-silent-loss guarantee."""

from __future__ import annotations

import pytest

from top_engineers.config import BATCH_REQUEUE_LIMIT, HYDRATE_BATCH
from top_engineers.pipeline.hydrate import batched, mean_child_counts, run_hydrate


class FakeCfg:
    def __init__(self, tmp_path):
        self.checkpoint_dir = tmp_path / "checkpoint"
        self.raw_dir = tmp_path / "raw"

    def ensure_dirs(self):
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)


def _pr(n):
    return {
        "number": n, "title": f"fix: {n}", "author": {"login": "a", "__typename": "User"},
        "bodyText": "Autonomy: Human-driven (agent-assisted)",
        "files": {"nodes": [{"path": "a.py"}], "pageInfo": {"hasNextPage": False}},
        "commits": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }


class FakeClient:
    """Returns PRs by alias, optionally failing specific batches."""

    def __init__(self, fail_containing=None, fail_times=99):
        from top_engineers.gh.limiter import AdaptiveConcurrency, RateLimiter

        self.limiter = RateLimiter(min_interval=0.0)
        self.concurrency = AdaptiveConcurrency(start=2, cap=2)
        self.fail_containing = fail_containing
        self.fail_times = fail_times
        self.failures = 0
        self.seen: list[int] = []

    async def execute(self, query, variables=None):
        import re

        nums = [int(m) for m in re.findall(r"pullRequest\(number: (\d+)\)", query)]
        if self.fail_containing is not None and self.fail_containing in nums \
                and self.failures < self.fail_times:
            self.failures += 1
            raise RuntimeError("simulated upstream failure")
        self.seen.extend(nums)
        repo = {f"p{i}": _pr(n) for i, n in enumerate(nums)}
        repo["__typename"] = None
        return {"repository": repo, "rateLimit": {"cost": 1, "remaining": 5000}}


def test_batching_uses_configured_size():
    assert batched(list(range(60)), HYDRATE_BATCH)[0] == list(range(HYDRATE_BATCH))
    assert sum(len(b) for b in batched(list(range(57)), 25)) == 57


async def test_hydrates_all_targets(tmp_path):
    cfg = FakeCfg(tmp_path)
    client = FakeClient()
    stats = await run_hydrate(client, cfg, list(range(1, 60)))
    assert stats["fetched"] == 59
    assert stats["failed_prs"] == []


async def test_resume_skips_checkpointed_prs(tmp_path):
    cfg = FakeCfg(tmp_path)
    targets = list(range(1, 60))
    await run_hydrate(FakeClient(), cfg, targets)

    second = FakeClient()
    stats = await run_hydrate(second, cfg, targets)
    assert stats["resumed"] == 59
    assert stats["fetched"] == 0
    assert second.seen == [], "resume must not refetch"


async def test_failed_batch_is_requeued_not_dropped(tmp_path):
    """A transient failure must not silently lose 25 PRs."""
    cfg = FakeCfg(tmp_path)
    client = FakeClient(fail_containing=1, fail_times=1)
    stats = await run_hydrate(client, cfg, list(range(1, 60)))
    assert client.failures == 1
    assert stats["fetched"] == 59, "re-queued batch must land"
    assert stats["failed_prs"] == []


async def test_permanently_failing_batch_is_reported_as_data_loss(tmp_path):
    """Giving up is allowed; giving up SILENTLY is not."""
    cfg = FakeCfg(tmp_path)
    client = FakeClient(fail_containing=1, fail_times=99)
    stats = await run_hydrate(client, cfg, list(range(1, 60)))
    assert client.failures == BATCH_REQUEUE_LIMIT + 1
    assert stats["failed_prs"], "operator must be told exactly which PRs are missing"
    assert 1 in stats["failed_prs"]
    assert stats["fetched"] == 59 - len(stats["failed_prs"])


async def test_batch_load_happens_per_batch(tmp_path):
    """Load each batch into the store as it arrives."""
    cfg = FakeCfg(tmp_path)
    loaded: list[int] = []
    await run_hydrate(FakeClient(), cfg, list(range(1, 60)),
                      on_batch=lambda b: loaded.extend(p["number"] for p in b))
    assert sorted(loaded) == list(range(1, 60))


def test_mean_child_counts_reports_for_regression_checking():
    acc = {"files": {"total": 39, "prs": 10}, "reviews": {"total": 52, "prs": 10}}
    assert mean_child_counts(acc) == {"files": 3.9, "reviews": 5.2}
