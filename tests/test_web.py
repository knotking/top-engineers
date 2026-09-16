"""Web layer: read-only, zero computation, and loud about an empty database."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from top_engineers.identity import Actors
from top_engineers.metrics.compute import compute_all
from top_engineers.scoring.score import score_cohort
from top_engineers.store.ingest import load_pr_batch
from top_engineers.store.load import connect, init_schema
from top_engineers.store.serving import build_serving, persist_scores

UNTIL = date(2026, 9, 16)


def _pr(number, author, merged, reviewer):
    return {
        "number": number, "title": f"fix: thing {number}",
        "url": f"https://github.com/PostHog/posthog/pull/{number}",
        "state": "MERGED", "createdAt": (merged - timedelta(days=1)).isoformat(),
        "mergedAt": merged.isoformat(), "updatedAt": merged.isoformat(),
        "additions": 40, "deletions": 3, "changedFiles": 2,
        "author": {"login": author, "__typename": "User"},
        "bodyText": "Autonomy: Human-driven (agent-assisted)",
        "mergeCommit": {"oid": "abc"}, "baseRefName": "master",
        "files": {"nodes": [{"path": "src/a.py", "additions": 5, "deletions": 1},
                            {"path": "tests/test_a.py", "additions": 9, "deletions": 0}]},
        "commits": {"nodes": [{"commit": {"oid": "c1", "committedDate": merged.isoformat()}}]},
        "firstCommit": {"nodes": [{"commit": {"oid": "c1", "statusCheckRollup": {"state": "SUCCESS"}}}]},
        "headCommit": {"nodes": [{"commit": {"oid": "c2", "statusCheckRollup": {"state": "SUCCESS"}}}]},
        "reviews": {"nodes": [{"author": {"login": reviewer, "__typename": "User"},
                               "state": "APPROVED",
                               "submittedAt": (merged - timedelta(hours=2)).isoformat(),
                               "bodyText": "looks good"}]},
        "reviewThreads": {"nodes": []}, "comments": {"nodes": []},
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "w.duckdb"
    con = connect(db)
    init_schema(con)
    old = datetime(2026, 7, 1, tzinfo=timezone.utc)
    prs = []
    n = 0
    for a, b in (("alice", "bob"), ("bob", "alice")):
        for i in range(12):
            n += 1
            prs.append(_pr(n, a, old + timedelta(days=i), b))
    load_pr_batch(con, prs)
    people = compute_all(con, Actors(), UNTIL)
    results = score_cohort(people)
    persist_scores(con, results, people)
    build_serving(con, results, people, meta={"window": "2026-06-18..2026-09-16",
                                              "display_size": "10", "normalise_pool": "50"})
    con.close()

    monkeypatch.setenv("TP_DB_PATH", str(db))
    monkeypatch.setenv("TP_DATA_DIR", str(tmp_path))
    import top_engineers.web.app as appmod

    appmod._con = None  # reset the module-level connection between tests
    return TestClient(appmod.app)


@pytest.mark.parametrize("path", ["/healthz", "/_health"])
def test_health_ok_when_leaderboard_populated(client, path):
    """Both paths must work: Cloud Run's frontend intercepts /healthz."""
    r = client.get(path)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["leaderboard_rows"] > 0


def test_index_renders_with_caveats_and_avatars(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "Top engineers" in html
    assert "github.com/alice.png?size=160" in html
    assert "conversation" in html.lower(), "caveats panel must be always visible"
    assert "tie band" in html.lower()


def test_person_api_returns_chain_and_evidence(client):
    r = client.get("/api/person/alice")
    assert r.status_code == 200
    body = r.json()
    assert body["person"]["login"] == "alice"
    assert body["metrics"], "drill-down needs the metric chain"
    scored = [m for m in body["metrics"] if m["scored"]]
    assert scored
    for m in scored:
        assert m["how_note"], "every scored metric needs a how-note"
        assert m["contribution"] is not None


def test_unknown_person_is_404(client):
    assert client.get("/api/person/nobody").status_code == 404


def test_leaderboard_api_is_ordered(client):
    rows = client.get("/api/leaderboard").json()
    assert rows == sorted(rows, key=lambda r: r["rank"])


def test_empty_database_fails_health_loudly(tmp_path, monkeypatch):
    """An empty leaderboard is the classic silent failure from a bad TP_DB_PATH.

    It must 503, not serve a blank page forever.
    """
    db = tmp_path / "empty.duckdb"
    con = connect(db)
    init_schema(con)
    con.close()
    monkeypatch.setenv("TP_DB_PATH", str(db))
    import top_engineers.web.app as appmod

    appmod._con = None
    c = TestClient(appmod.app)
    r = c.get("/_health")
    assert r.status_code == 503
    assert r.json()["ok"] is False
