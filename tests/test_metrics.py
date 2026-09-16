"""Metric definitions and the two traps that are easy to get quietly wrong."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from top_engineers.config import REVERT_CENSOR_DAYS
from top_engineers.identity import Actors
from top_engineers.metrics.compute import compute_all
from top_engineers.metrics.definitions import (
    ABSOLUTE_WEIGHTS,
    ALL_METRICS,
    BY_KEY,
    PILLAR_WEIGHTS,
    SCORED,
)
from top_engineers.store.ingest import load_pr_batch
from top_engineers.store.load import connect, init_schema

UNTIL = date(2026, 9, 16)


def test_registry_shape():
    assert len(ALL_METRICS) == 33, "plan specifies 33 computed"
    # 9, not the plan's 10: revert_rate was demoted to context-only because it failed the
    # plan's own "separates people" criterion on this window (1 of 50 people nonzero).
    assert len(SCORED) == 9
    assert len({m.key for m in ALL_METRICS}) == 33, "keys must be unique"


def test_no_volume_metric_is_scored():
    """Volume metrics reward agent throughput -- that is the whole point of excluding them."""
    volume = {"merged_pr_count", "reviews_given", "median_cycle_time_hours",
              "median_pr_size_lines", "dispatched_n"}
    assert not (volume & {m.key for m in SCORED})


def test_pillar_weights_match_plan():
    for pillar, expected in PILLAR_WEIGHTS.items():
        got = sum(w for k, w in ABSOLUTE_WEIGHTS.items() if BY_KEY[k].pillar == pillar)
        assert got == pytest.approx(expected), f"{pillar} must still carry {expected}"


def test_demoting_a_metric_rebalances_its_pillar_exactly():
    """Removing revert_rate must not leave builder short of 45%."""
    assert sum(ABSOLUTE_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-12)
    assert "revert_rate" not in ABSOLUTE_WEIGHTS


def test_every_scored_metric_has_a_how_note():
    """A number whose denominator is a mystery cannot be argued with."""
    for m in SCORED:
        assert m.how and len(m.how) > 40, f"{m.key} needs a plain-English computation note"


def _pr(number, author, merged, autonomy="Human-driven (agent-assisted)",
        first_ci="SUCCESS", head_ci="SUCCESS", files=("src/a.py",), reviews=()):
    return {
        "number": number, "title": f"fix: thing {number}", "url": f"https://x/{number}",
        "state": "MERGED", "createdAt": (merged - timedelta(days=1)).isoformat(),
        "mergedAt": merged.isoformat(), "updatedAt": merged.isoformat(),
        "additions": 10, "deletions": 2, "changedFiles": len(files),
        "author": {"login": author, "__typename": "User"},
        "bodyText": f"Autonomy: {autonomy}",
        "mergeCommit": {"oid": "abc"}, "baseRefName": "master",
        "files": {"nodes": [{"path": p, "additions": 5, "deletions": 1} for p in files]},
        "commits": {"nodes": [{"commit": {"oid": "c1", "committedDate": merged.isoformat()}}]},
        "firstCommit": {"nodes": [{"commit": {"oid": "c1", "statusCheckRollup": {"state": first_ci}}}]},
        "headCommit": {"nodes": [{"commit": {"oid": "c2", "statusCheckRollup": {"state": head_ci}}}]},
        "reviews": {"nodes": [
            {"author": {"login": r, "__typename": "User"}, "state": "APPROVED",
             "submittedAt": merged.isoformat(), "bodyText": "lgtm"} for r in reviews]},
        "reviewThreads": {"nodes": []}, "comments": {"nodes": []},
    }


@pytest.fixture
def con(tmp_path):
    c = connect(tmp_path / "m.duckdb")
    init_schema(c)
    return c


def test_recent_merges_leave_the_revert_denominator(con):
    """PRs merged in the final 14 days cannot yet be observed for a full revert window.

    They must leave the DENOMINATOR entirely -- not be counted as clean.
    """
    old = datetime(2026, 7, 1, tzinfo=timezone.utc)
    recent = datetime(2026, 9, 14, tzinfo=timezone.utc)  # inside the censor window
    load_pr_batch(con, [
        _pr(1, "alice", old), _pr(2, "alice", old), _pr(3, "alice", recent),
        _pr(4, "alice", recent), _pr(5, "alice", recent),
    ])
    res = compute_all(con, Actors(), UNTIL)
    _, n = res["alice"]["metrics"]["revert_rate"]
    assert n == 2, f"only pre-censor PRs belong in the denominator, got {n}"


def test_censor_boundary_is_inclusive(con):
    cutoff = datetime(2026, 9, 16, tzinfo=timezone.utc) - timedelta(days=REVERT_CENSOR_DAYS)
    load_pr_batch(con, [_pr(1, "bob", cutoff), _pr(2, "bob", cutoff + timedelta(days=1))])
    res = compute_all(con, Actors(), UNTIL)
    assert res["bob"]["metrics"]["revert_rate"][1] == 1


def test_ci_first_pass_uses_first_commit_not_head(con):
    """A PR that went red and was fixed up did NOT pass first time."""
    merged = datetime(2026, 7, 1, tzinfo=timezone.utc)
    load_pr_batch(con, [
        _pr(1, "carol", merged, first_ci="FAILURE", head_ci="SUCCESS"),
        _pr(2, "carol", merged, first_ci="SUCCESS", head_ci="SUCCESS"),
    ])
    res = compute_all(con, Actors(), UNTIL)
    first_pass, n = res["carol"]["metrics"]["ci_first_pass_rate"]
    head_pass, _ = res["carol"]["metrics"]["ci_head_pass_rate"]
    assert n == 2
    assert first_pass == pytest.approx(0.5), "the fixed-up PR must not count as first-pass"
    assert head_pass == pytest.approx(1.0), "both ended green"


def test_pending_ci_is_no_signal_not_a_pass(con):
    merged = datetime(2026, 7, 1, tzinfo=timezone.utc)
    load_pr_batch(con, [_pr(1, "dave", merged, first_ci="PENDING")])
    res = compute_all(con, Actors(), UNTIL)
    assert res["dave"]["metrics"]["ci_first_pass_rate"][1] == 0, "PENDING must not enter the denominator"


def test_fully_autonomous_excluded_from_builder_kept_for_reviewer(con):
    """Dispatched, not authored -- but reviewing an agent's PR is real review work."""
    merged = datetime(2026, 7, 1, tzinfo=timezone.utc)
    load_pr_batch(con, [
        _pr(1, "eve", merged, autonomy="Fully autonomous", reviews=("frank",)),
        _pr(2, "eve", merged, autonomy="Fully autonomous", reviews=("frank",)),
        _pr(3, "eve", merged, autonomy="Human-driven (agent-assisted)", reviews=("frank",)),
    ])
    res = compute_all(con, Actors(), UNTIL)
    eve = res["eve"]
    assert eve["authored_n"] == 3
    assert eve["builder_n"] == 1, "autonomous PRs must leave builder denominators"
    assert eve["dispatched_n"] == 2, "excluded-from-scoring is not did-not-happen"
    assert eve["metrics"]["test_habit"][1] == 1
    # The reviewer keeps full credit for all three.
    assert res["frank"]["metrics"]["author_breadth"][0] == 1
    assert res["frank"]["reviewed_n"] == 3


def test_unedited_template_does_not_count_as_human_driven(con):
    merged = datetime(2026, 7, 1, tzinfo=timezone.utc)
    load_pr_batch(con, [
        _pr(1, "gina", merged, autonomy="Fully autonomous | Human-driven (agent-assisted)")])
    res = compute_all(con, Actors(), UNTIL)
    assert res["gina"]["metrics"]["autonomy_unknown_rate"][0] == pytest.approx(1.0)
    assert res["gina"]["dispatched_n"] == 0


def test_test_habit_detects_test_paths(con):
    merged = datetime(2026, 7, 1, tzinfo=timezone.utc)
    load_pr_batch(con, [
        _pr(1, "hank", merged, files=("src/a.py", "tests/test_a.py")),
        _pr(2, "hank", merged, files=("src/b.py",)),
    ])
    res = compute_all(con, Actors(), UNTIL)
    assert res["hank"]["metrics"]["test_habit"][0] == pytest.approx(0.5)


def test_bots_never_enter_the_ranking(con):
    merged = datetime(2026, 7, 1, tzinfo=timezone.utc)
    load_pr_batch(con, [_pr(1, "realbot", merged), _pr(2, "human", merged)])
    res = compute_all(con, Actors(bots={"realbot"}), UNTIL)
    assert "realbot" not in res
    assert "human" in res
