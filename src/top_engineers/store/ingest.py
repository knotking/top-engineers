"""Load hydrated PR payloads into the raw layer.

Called from the checkpoint's on_flush, i.e. only AFTER the raw JSON is durably on disk.
Reversing that order yields store rows with no raw record behind them -- corruption that
stays invisible for weeks.
"""

from __future__ import annotations

from typing import Any, Sequence

import duckdb

from ..pipeline.records import CHILD_COLUMNS, PR_COLUMNS, child_rows, pr_row
from .load import delete_children, insert_rows, upsert_pr


def load_pr_batch(
    con: duckdb.DuckDBPyConnection,
    prs: Sequence[dict[str, Any]],
    hydrated: bool = True,
) -> int:
    if not prs:
        return 0
    numbers = [pr["number"] for pr in prs]
    # Re-hydration must not duplicate children, so clear them before reinserting.
    delete_children(con, numbers)
    upsert_pr(con, [pr_row(pr, hydrated=hydrated) for pr in prs], PR_COLUMNS)
    for pr in prs:
        for table, rows in child_rows(pr).items():
            insert_rows(con, table, CHILD_COLUMNS[table], rows)
    return len(prs)


def load_skim_batch(con: duckdb.DuckDBPyConnection, records: Sequence[dict[str, Any]]) -> int:
    """Skim records carry no children and are marked hydrated=FALSE until Phase 2."""
    if not records:
        return 0
    rows = [
        (
            rec["number"], rec.get("title"), None, None,
            rec.get("createdAt"), rec.get("mergedAt"), rec.get("updatedAt"),
            None, None, None,
            rec.get("author_login"), rec.get("author_type"),
            None, None, None, rec.get("variant"), None, False, None, None,
        )
        for rec in records
    ]
    return upsert_pr(con, rows, PR_COLUMNS)


def load_cohort(con: duckdb.DuckDBPyConnection, ranking: Sequence[dict[str, Any]]) -> int:
    con.execute("DELETE FROM cohort")
    return insert_rows(
        con,
        "cohort",
        ("login", "authored_n", "reviewed_n", "participation", "rank", "in_display", "in_normalise"),
        [
            (r["login"], r["authored_n"], r["reviewed_n"], r["participation"],
             r["rank"], r["in_display"], r["in_normalise"])
            for r in ranking
        ],
    )
