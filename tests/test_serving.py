"""The serving layer must let the UI do ZERO computation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, date

import pytest

from top_engineers.identity import Actors
from top_engineers.metrics.compute import compute_all
from top_engineers.metrics.definitions import SCORED
from top_engineers.scoring.score import score_cohort
from top_engineers.store.ingest import load_pr_batch
from top_engineers.store.load import connect, init_schema
from top_engineers.store.serving import build_serving, persist_scores

UNTIL = date(2026, 9, 16)


def _pr(number, author, merged, reviews=(), files=("src/a.py",), lines=10, first_ci="SUCCESS"):
    return {
        "number": number, "title": f"fix: thing {number}", "url": f"https://github.com/x/pull/{number}",
        "state": "MERGED", "createdAt": (merged - timedelta(days=2)).isoformat(),
        "mergedAt": merged.isoformat(), "updatedAt": merged.isoformat(),
        "additions": lines, "deletions": 2, "changedFiles": len(files),
        "author": {"login": author, "__typename": "User"},
        "bodyText": "Autonomy: Human-driven (agent-assisted)",
        "mergeCommit": {"oid": "abc"}, "baseRefName": "master",
        "files": {"nodes": [{"path": p, "additions": 5, "deletions": 1} for p in files]},
        "commits": {"nodes": [{"commit": {"oid": "c1", "committedDate": merged.isoformat()}}]},
        "firstCommit": {"nodes": [{"commit": {"oid": "c1", "statusCheckRollup": {"state": first_ci}}}]},
        "headCommit": {"nodes": [{"commit": {"oid": "c2", "statusCheckRollup": {"state": "SUCCESS"}}}]},
        "reviews": {"nodes": [
            {"author": {"login": r, "__typename": "User"}, "state": "APPROVED",
             "submittedAt": (merged - timedelta(hours=3)).isoformat(), "bodyText": ""}
            for r in reviews]},
        "reviewThreads": {"nodes": []}, "comments": {"nodes": []},
    }


@pytest.fixture
def built(tmp_path):
    con = connect(tmp_path / "s.duckdb")
    init_schema(con)
    old = datetime(2026, 7, 1, tzinfo=timezone.utc)
    prs = []
    n = 0
    for author, reviewer in (("alice", "bob"), ("bob", "alice")):
        for i in range(12):
            n += 1
            prs.append(_pr(n, author, old + timedelta(days=i),
                           reviews=(reviewer,), lines=10 + i * 300,
                           first_ci="FAILURE" if i % 3 == 0 else "SUCCESS"))
    load_pr_batch(con, prs)
    people = compute_all(con, Actors(), UNTIL)
    results = score_cohort(people)
    persist_scores(con, results, people)
    build_serving(con, results, people, meta={"window": "2026-06-18..2026-09-16"})
    return con


def test_leaderboard_is_materialised(built):
    rows = built.execute(
        "SELECT login, rank, avatar_url, profile_url, score FROM serving_leaderboard ORDER BY rank"
    ).fetchall()
    assert rows
    for login, rank, avatar, profile, score in rows:
        # No API call or id lookup needed for an avatar.
        assert avatar == f"https://github.com/{login}.png?size=160"
        assert profile == f"https://github.com/{login}"
        assert score is not None


def test_full_arithmetic_chain_is_available_per_metric(built):
    rows = built.execute("""
        SELECT metric, raw, winsorized, shrunk, percentile, weight, contribution, std_error
        FROM serving_metric_chain WHERE scored
    """).fetchall()
    assert rows
    for r in rows:
        assert all(v is not None for v in r[1:]), f"incomplete chain for {r[0]}"


def test_contribution_equals_percentile_times_weight(built):
    """Drill-down must explain the rank arithmetically."""
    for metric, pct, w, contrib in built.execute(
        "SELECT metric, percentile, weight, contribution FROM serving_metric_chain WHERE scored"
    ).fetchall():
        assert contrib == pytest.approx(pct * w, abs=1e-9), metric


def test_score_equals_sum_of_contributions(built):
    for login, score in built.execute("SELECT login, score FROM serving_leaderboard").fetchall():
        total = built.execute(
            "SELECT coalesce(sum(contribution),0) FROM serving_metric_chain WHERE login = ? AND scored",
            (login,),
        ).fetchone()[0]
        assert score == pytest.approx(total, abs=1e-9)


def test_every_scored_metric_carries_a_how_note(built):
    rows = built.execute(
        "SELECT metric, how_note FROM serving_metric_chain WHERE scored"
    ).fetchall()
    assert rows
    for metric, how in rows:
        assert how and len(how) > 40, f"{metric} has no usable how-note"


def test_how_notes_mention_the_exclusions(built):
    """A number whose denominator is a mystery cannot be argued with."""
    notes = dict(built.execute("SELECT metric, how_note FROM serving_metric_chain").fetchall())
    assert "14 days" in notes["revert_rate"] or "DENOMINATOR" in notes["revert_rate"]
    assert "FIRST" in notes["ci_first_pass_rate"]
    assert "autonomous" in notes["rubber_stamp_rate"].lower()


def test_evidence_favours_adverse_prs(built):
    rows = built.execute(
        "SELECT login, metric, url, reason, is_adverse FROM serving_evidence ORDER BY is_adverse DESC"
    ).fetchall()
    assert rows, "evidence must exist"
    assert any(r[4] for r in rows), "must surface PRs that explain a BAD score"
    for r in rows:
        assert r[2].startswith("https://"), "evidence needs a clickable PR link"


def test_caveats_are_always_available(built):
    titles = [r[0] for r in built.execute("SELECT title FROM serving_caveats ORDER BY seq").fetchall()]
    body = " ".join(
        r[0] for r in built.execute("SELECT body FROM serving_caveats").fetchall()
    ).lower()
    assert len(titles) >= 7
    for expected in ("volume-selected", "censoring", "autonomy", "small-pool", "tie band"):
        assert any(expected.lower() in t.lower() for t in titles), f"missing caveat: {expected}"
    assert "conversation" in body, "must say this is a conversation starter, not a verdict"


def test_context_metrics_are_marked_unscored(built):
    scored, context = built.execute("""
        SELECT count(*) FILTER (WHERE scored), count(*) FILTER (WHERE NOT scored)
        FROM serving_metric_chain
    """).fetchone()
    assert scored > 0 and context > 0, "UI must be able to split Scored vs Context-only"


def test_metrics_with_no_value_are_hidden(built):
    """Hide metrics with no value."""
    nulls = built.execute(
        "SELECT count(*) FROM serving_metric_chain WHERE raw IS NULL AND NOT scored"
    ).fetchone()[0]
    assert nulls == 0
