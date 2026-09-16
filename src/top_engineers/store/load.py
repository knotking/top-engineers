"""DuckDB access and loading.

Two rules the hard way:

  * NAMED-COLUMN inserts only. A positional ``INSERT INTO x VALUES (...)`` breaks silently
    the moment a migration widens the table.
  * DuckDB has no ``ADD COLUMN IF NOT EXISTS``. Attempt each migration and swallow the
    duplicate-column error to stay idempotent.

DuckDB is also SINGLE-WRITER: even a read-only server connection holds a lock, so stop the
server before rebuilding. (Moot on Cloud Run -- the image is immutable.)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Columns added after the initial schema shipped. Each is attempted and its duplicate error
# swallowed, which is what makes re-running the migration safe.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("raw_pr", "variant", "VARCHAR"),
    ("raw_pr", "autonomy", "VARCHAR"),
    ("raw_pr", "hydrated", "BOOLEAN DEFAULT FALSE"),
    ("raw_pr", "ci_first_state", "VARCHAR"),
    ("raw_pr", "ci_head_state", "VARCHAR"),
)


def connect(db_path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path), read_only=read_only)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_PATH.read_text())
    apply_migrations(con)


def apply_migrations(con: duckdb.DuckDBPyConnection) -> None:
    for table, column, ddl in MIGRATIONS:
        # Each migration gets its own transaction. DuckDB ABORTS the surrounding transaction
        # on a failed statement, so without the rollback the *next* migration fails with an
        # opaque TransactionException instead of the duplicate-column error we expect.
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except duckdb.Error as exc:
            con.execute("ROLLBACK")
            # Idempotency: an already-present column is the expected steady state.
            if "already exists" not in str(exc).lower():
                raise
        else:
            con.execute("COMMIT")
            log.info("migrated: %s.%s added", table, column)


def insert_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> int:
    """Named-column insert. Never positional."""
    rows = list(rows)
    if not rows:
        return 0
    cols = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    con.executemany(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", rows)
    return len(rows)


def upsert_pr(con: duckdb.DuckDBPyConnection, rows: Sequence[Sequence[Any]], columns: Sequence[str]) -> int:
    """Replace PRs by number so a resumed run is idempotent."""
    if not rows:
        return 0
    numbers = [r[columns.index("number")] for r in rows]
    con.executemany("DELETE FROM raw_pr WHERE number = ?", [(n,) for n in numbers])
    return insert_rows(con, "raw_pr", columns, rows)


def delete_children(con: duckdb.DuckDBPyConnection, pr_numbers: Sequence[int]) -> None:
    """Clear child rows before reloading a PR, so re-hydration cannot duplicate them."""
    if not pr_numbers:
        return
    params = [(n,) for n in pr_numbers]
    for table in ("raw_review", "raw_review_thread", "raw_commit", "raw_file", "raw_comment"):
        con.executemany(f"DELETE FROM {table} WHERE pr_number = ?", params)


def table_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    con.execute("DELETE FROM serving_meta WHERE key = ?", (key,))
    insert_rows(con, "serving_meta", ("key", "value"), [(key, value)])


def get_meta(con: duckdb.DuckDBPyConnection, key: str) -> str | None:
    row = con.execute("SELECT value FROM serving_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None
