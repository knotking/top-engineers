"""Flatten GraphQL PR nodes into store rows.

Kept separate from fetching so the `load-from-raw` restore path and the live fetch share one
definition of what a row is -- otherwise committed raw drifts from the database it rebuilds.
"""

from __future__ import annotations

from typing import Any

from .autonomy import parse_autonomy

PR_COLUMNS = (
    "number", "title", "url", "state", "created_at", "merged_at", "updated_at",
    "additions", "deletions", "changed_files", "author_login", "author_type",
    "merge_commit_oid", "base_ref_name", "body_text", "variant", "autonomy", "hydrated",
    "ci_first_state", "ci_head_state",
)
REVIEW_COLUMNS = ("pr_number", "seq", "author_login", "author_type", "state", "submitted_at", "body_text")
THREAD_COLUMNS = ("pr_number", "seq", "is_resolved", "is_outdated", "comment_count", "author_login", "body_text")
COMMIT_COLUMNS = ("pr_number", "seq", "oid", "committed_date", "conclusion")
FILE_COLUMNS = ("pr_number", "path", "additions", "deletions")
COMMENT_COLUMNS = ("pr_number", "seq", "author_login", "author_type", "created_at", "body_text")


def _actor(node: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not node:
        return None, None
    return node.get("login"), node.get("__typename")


def _nodes(conn: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not conn:
        return []
    return [n for n in (conn.get("nodes") or []) if n]


def _rollup_state(conn: dict[str, Any] | None) -> str | None:
    """CI state of the single commit in an aliased `firstCommit` / `headCommit` connection.

    statusCheckRollup is requested on exactly two commits rather than across the whole
    commits connection -- measured 3.22s vs 0.88s per 25-PR batch for identical point cost.

    PENDING/EXPECTED return None: an unfinished run is no signal, not a pass.
    """
    nodes = _nodes(conn)
    if not nodes:
        return None
    rollup = (nodes[0].get("commit") or {}).get("statusCheckRollup") or {}
    state = rollup.get("state")
    return None if state in ("PENDING", "EXPECTED", None) else state


def pr_row(pr: dict[str, Any], variant: str | None = None, hydrated: bool = True) -> tuple:
    login, atype = _actor(pr.get("author"))
    body = pr.get("bodyText")
    return (
        pr["number"],
        pr.get("title"),
        pr.get("url"),
        pr.get("state"),
        pr.get("createdAt"),
        pr.get("mergedAt"),
        pr.get("updatedAt"),
        pr.get("additions"),
        pr.get("deletions"),
        pr.get("changedFiles"),
        login,
        atype,
        (pr.get("mergeCommit") or {}).get("oid"),
        pr.get("baseRefName"),
        body,
        variant or pr.get("_variant"),
        parse_autonomy(body),
        hydrated,
        _rollup_state(pr.get("firstCommit")),
        _rollup_state(pr.get("headCommit")),
    )


def child_rows(pr: dict[str, Any]) -> dict[str, list[tuple]]:
    n = pr["number"]
    reviews = []
    for i, r in enumerate(_nodes(pr.get("reviews"))):
        login, atype = _actor(r.get("author"))
        reviews.append((n, i, login, atype, r.get("state"), r.get("submittedAt"), r.get("bodyText")))

    threads = []
    for i, t in enumerate(_nodes(pr.get("reviewThreads"))):
        comments = _nodes(t.get("comments"))
        first = comments[0] if comments else {}
        login, _ = _actor(first.get("author"))
        threads.append((
            n, i, t.get("isResolved"), t.get("isOutdated"),
            (t.get("comments") or {}).get("totalCount", len(comments)),
            login, first.get("bodyText"),
        ))

    # Per-commit CI state is no longer fetched (see _rollup_state); the PR-level first and
    # head states live on raw_pr instead.
    commits = []
    for i, c in enumerate(_nodes(pr.get("commits"))):
        commit = c.get("commit") or {}
        commits.append((n, i, commit.get("oid"), commit.get("committedDate"), None))

    files = [
        (n, f.get("path"), f.get("additions"), f.get("deletions"))
        for f in _nodes(pr.get("files"))
    ]

    comments = []
    for i, c in enumerate(_nodes(pr.get("comments"))):
        login, atype = _actor(c.get("author"))
        comments.append((n, i, login, atype, c.get("createdAt"), c.get("bodyText")))

    return {
        "raw_review": reviews,
        "raw_review_thread": threads,
        "raw_commit": commits,
        "raw_file": files,
        "raw_comment": comments,
    }


CHILD_COLUMNS = {
    "raw_review": REVIEW_COLUMNS,
    "raw_review_thread": THREAD_COLUMNS,
    "raw_commit": COMMIT_COLUMNS,
    "raw_file": FILE_COLUMNS,
    "raw_comment": COMMENT_COLUMNS,
}


def child_counts(pr: dict[str, Any]) -> dict[str, int]:
    """Per-PR child counts, for the post-tuning regression check.

    Verify these are unchanged after tuning page sizes; silent truncation is biased toward
    the busiest, most-reviewed PRs.
    """
    return {
        field: len(_nodes(pr.get(field)))
        for field in ("files", "commits", "reviews", "reviewThreads", "comments")
    }
