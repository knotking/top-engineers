"""Store invariants: named-column inserts, idempotent migrations, restore path."""

from __future__ import annotations

import duckdb
import pytest

from top_engineers.rawio import write_jsonl_gz
from top_engineers.store.ingest import load_cohort, load_pr_batch
from top_engineers.store.load import (
    apply_migrations,
    connect,
    init_schema,
    insert_rows,
    table_count,
)
from top_engineers.store.load_from_raw import COHORT_FILE, PRS_FILE, restore


@pytest.fixture
def con(tmp_path):
    c = connect(tmp_path / "t.duckdb")
    init_schema(c)
    return c


def test_migrations_are_idempotent(con):
    """DuckDB has no ADD COLUMN IF NOT EXISTS; re-running must not raise."""
    for _ in range(3):
        apply_migrations(con)


def test_migration_rollback_does_not_poison_transaction(con):
    """A swallowed duplicate-column error aborts the transaction in DuckDB.

    Without an explicit ROLLBACK the NEXT migration fails with an opaque
    TransactionException instead of the duplicate error we expect.
    """
    apply_migrations(con)
    apply_migrations(con)
    cols = {r[0] for r in con.execute("DESCRIBE raw_pr").fetchall()}
    assert {"variant", "autonomy", "hydrated", "ci_first_state", "ci_head_state"} <= cols


def test_named_column_insert_survives_a_widened_table(con):
    """A positional INSERT breaks silently the moment a migration widens the table."""
    insert_rows(con, "raw_pr", ("number", "title"), [(1, "fix: a")])
    con.execute("ALTER TABLE raw_pr ADD COLUMN extra_col VARCHAR")
    insert_rows(con, "raw_pr", ("number", "title"), [(2, "fix: b")])
    assert table_count(con, "raw_pr") == 2


def test_reload_does_not_duplicate_children(con):
    pr = {
        "number": 1, "title": "fix: x", "author": {"login": "a", "__typename": "User"},
        "bodyText": "Autonomy: Human-driven (agent-assisted)",
        "files": {"nodes": [{"path": "a.py", "additions": 1, "deletions": 0}]},
        "reviews": {"nodes": [{"author": {"login": "b"}, "state": "APPROVED"}]},
        "commits": {"nodes": []}, "reviewThreads": {"nodes": []}, "comments": {"nodes": []},
    }
    load_pr_batch(con, [pr])
    load_pr_batch(con, [pr])
    assert table_count(con, "raw_pr") == 1
    assert table_count(con, "raw_file") == 1, "re-hydration must not duplicate children"
    assert table_count(con, "raw_review") == 1


def test_load_from_raw_restores_a_working_database(tmp_path):
    """Without this path, committed raw is an audit trail, not a restore path."""
    raw = tmp_path / "raw"
    raw.mkdir()
    prs = [{
        "number": n, "title": f"fix: {n}", "url": f"https://x/{n}",
        "author": {"login": "a", "__typename": "User"},
        "bodyText": "Autonomy: Fully autonomous",
        "files": {"nodes": [{"path": "a.py", "additions": 1, "deletions": 0}]},
        "reviews": {"nodes": []}, "commits": {"nodes": []},
        "reviewThreads": {"nodes": []}, "comments": {"nodes": []},
    } for n in (3, 1, 2)]
    write_jsonl_gz(raw / PRS_FILE, prs)
    write_jsonl_gz(raw / COHORT_FILE, [
        {"login": "a", "authored_n": 3, "reviewed_n": 0, "participation": 3,
         "rank": 1, "in_display": True, "in_normalise": True}], sort_key="rank")

    con = connect(tmp_path / "restored.duckdb")
    stats = restore(con, raw)
    assert stats["prs"] == 3
    assert table_count(con, "raw_pr") == 3
    assert table_count(con, "raw_file") == 3
    assert table_count(con, "cohort") == 1
    assert con.execute("SELECT autonomy FROM raw_pr LIMIT 1").fetchone()[0] == "fully_autonomous"


def test_restore_is_idempotent(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write_jsonl_gz(raw / PRS_FILE, [{
        "number": 1, "title": "fix: a", "author": {"login": "a", "__typename": "User"},
        "bodyText": "", "files": {"nodes": []}, "reviews": {"nodes": []},
        "commits": {"nodes": []}, "reviewThreads": {"nodes": []}, "comments": {"nodes": []},
    }])
    con = connect(tmp_path / "r.duckdb")
    restore(con, raw)
    restore(con, raw)
    assert table_count(con, "raw_pr") == 1
