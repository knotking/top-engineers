"""Rebuild the database from committed raw files.

Without this path, committed raw is an audit trail rather than a restore path -- you can see
what was fetched but you cannot get back to a working database without re-fetching. This gap
was never closed in the previous build, so it is built here before it is needed.

It shares `pipeline.records` with the live fetch so the two cannot drift.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb

from ..rawio import read_jsonl_gz
from .ingest import load_cohort, load_pr_batch, load_skim_batch
from .load import init_schema

log = logging.getLogger(__name__)

PRS_FILE = "prs.jsonl.gz"
SKIM_FILE = "skim.jsonl.gz"
COHORT_FILE = "cohort.jsonl.gz"


def restore(con: duckdb.DuckDBPyConnection, raw_dir: Path, batch_size: int = 250) -> dict[str, int]:
    """Rebuild every table from raw_dir. Idempotent: safe to run onto a populated database."""
    init_schema(con)
    for table in ("raw_pr", "raw_review", "raw_review_thread", "raw_commit",
                  "raw_file", "raw_comment", "cohort"):
        con.execute(f"DELETE FROM {table}")

    stats = {"skim": 0, "prs": 0, "cohort": 0}

    skim = read_jsonl_gz(raw_dir / SKIM_FILE)
    if skim:
        stats["skim"] = load_skim_batch(con, skim)

    prs = read_jsonl_gz(raw_dir / PRS_FILE)
    for i in range(0, len(prs), batch_size):
        stats["prs"] += load_pr_batch(con, prs[i : i + batch_size])

    cohort = read_jsonl_gz(raw_dir / COHORT_FILE)
    if cohort:
        stats["cohort"] = load_cohort(con, cohort)

    log.info("restored from raw: %s", stats)
    return stats


def write_canonical(raw_dir: Path, name: str, records: list[dict[str, Any]], sort_key: str = "number") -> str:
    from ..rawio import write_jsonl_gz

    return write_jsonl_gz(raw_dir / name, records, sort_key=sort_key)
