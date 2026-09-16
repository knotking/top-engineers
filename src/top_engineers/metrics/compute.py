"""Compute all 33 metrics per person from the raw layer.

Two definitional traps live here and both are easy to get quietly wrong:

  * ``ci_first_pass_rate`` keys off the FIRST pushed commit, not the head. A PR that went red
    and was fixed up did not pass first time.
  * ``revert_rate`` drops PRs merged in the final 14 days from the DENOMINATOR entirely --
    they cannot yet be observed for a full revert window. Leaving them in silently rewards
    whoever merged most recently.

Autonomy handling is asymmetric on purpose: ``fully_autonomous`` PRs leave the BUILDER
denominators (dispatched, not authored) but stay in the REVIEWER ones (reviewing an agent's
PR is real review work).
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import duckdb

from ..config import REVERT_CENSOR_DAYS
from ..identity import Actors
from .definitions import BY_KEY

log = logging.getLogger(__name__)

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|\.(test|spec)\.[jt]sx?$|_test\.py$|test_.*\.py$", re.I)
REVERT_TITLE = re.compile(r'^\s*revert\b|^\s*revert\s+"', re.I)
REVERTS_PR = re.compile(r"reverts?\s+(?:#|.*?/pull/)(\d+)", re.I)


def _hours(a: Any, b: Any) -> float | None:
    if not a or not b:
        return None
    if isinstance(a, str):
        a = datetime.fromisoformat(a.replace("Z", "+00:00"))
    if isinstance(b, str):
        b = datetime.fromisoformat(b.replace("Z", "+00:00"))
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return (b - a).total_seconds() / 3600.0


def _safe(num: float, den: float) -> float | None:
    return None if not den else num / den


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def load_frames(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    prs = con.execute("""
        SELECT number, title, url, created_at, merged_at, updated_at, additions, deletions,
               changed_files, author_login, author_type, autonomy, hydrated,
               ci_first_state, ci_head_state
        FROM raw_pr WHERE hydrated
    """).fetchall()
    cols = ("number", "title", "url", "created_at", "merged_at", "updated_at", "additions",
            "deletions", "changed_files", "author_login", "author_type", "autonomy", "hydrated",
            "ci_first_state", "ci_head_state")
    pr_rows = [dict(zip(cols, r)) for r in prs]

    reviews = con.execute("""
        SELECT pr_number, seq, author_login, author_type, state, submitted_at, body_text
        FROM raw_review
    """).fetchall()
    threads = con.execute("""
        SELECT pr_number, seq, is_resolved, is_outdated, comment_count, author_login, body_text
        FROM raw_review_thread
    """).fetchall()
    commits = con.execute("""
        SELECT pr_number, seq, oid, committed_date, conclusion FROM raw_commit ORDER BY pr_number, seq
    """).fetchall()
    files = con.execute("SELECT pr_number, path, additions, deletions FROM raw_file").fetchall()
    return {"prs": pr_rows, "reviews": reviews, "threads": threads, "commits": commits, "files": files}


def compute_all(
    con: duckdb.DuckDBPyConnection,
    actors: Actors,
    until: Any,
    cohort: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    f = load_frames(con)
    prs = {p["number"]: p for p in f["prs"]}

    if isinstance(until, str):
        until = datetime.fromisoformat(until).date()
    censor_cutoff = until - timedelta(days=REVERT_CENSOR_DAYS)

    # -- index children by PR ------------------------------------------------
    reviews_by_pr: dict[int, list[tuple]] = defaultdict(list)
    for r in f["reviews"]:
        reviews_by_pr[r[0]].append(r)
    threads_by_pr: dict[int, list[tuple]] = defaultdict(list)
    for t in f["threads"]:
        threads_by_pr[t[0]].append(t)
    commits_by_pr: dict[int, list[tuple]] = defaultdict(list)
    for c in f["commits"]:
        commits_by_pr[c[0]].append(c)
    files_by_pr: dict[int, list[tuple]] = defaultdict(list)
    for fl in f["files"]:
        files_by_pr[fl[0]].append(fl)

    reverted = _detect_reverts(prs, files_by_pr)

    acc: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(list))

    # -- authored ------------------------------------------------------------
    for number, pr in prs.items():
        author = pr["author_login"]
        if not author or not actors.is_human(author, pr["author_type"]):
            continue
        author = actors.canonical(author)
        a = acc[author]
        autonomy = pr.get("autonomy")
        a["_authored_all"].append(number)

        if autonomy == "fully_autonomous":
            # Dispatched, not authored: excluded from builder scoring but still reported.
            a["_dispatched"].append(number)
        if autonomy == "human_driven":
            a["_agent_assisted"].append(number)
        if autonomy == "unknown" or autonomy is None:
            a["_autonomy_unknown"].append(number)

        size = (pr["additions"] or 0) + (pr["deletions"] or 0)
        a["_size"].append(size)
        a["_files_changed"].append(pr["changed_files"] or 0)
        a["_commits_n"].append(len(commits_by_pr[number]))
        cycle = _hours(pr["created_at"], pr["merged_at"])
        if cycle is not None:
            a["_cycle"].append(cycle)

        paths = [x[1] or "" for x in files_by_pr[number]]
        a["_paths"].extend(paths)
        has_tests = any(TEST_PATH.search(p) for p in paths)
        if paths:
            a["_test_share"].append(sum(1 for p in paths if TEST_PATH.search(p)) / len(paths))

        pr_reviews = reviews_by_pr[number]
        approvals = [r for r in pr_reviews if r[4] == "APPROVED"
                     and r[2] and actors.canonical(r[2]) != author]
        a["_self_merge"].append(0 if approvals else 1)
        first_review_at = min((r[5] for r in pr_reviews if r[5]), default=None)
        if first_review_at:
            later = [c for c in commits_by_pr[number]
                     if c[3] and _hours(first_review_at, c[3]) and _hours(first_review_at, c[3]) > 0]
            a["_rework"].append(1 if later else 0)
        first_approval = min((r[5] for r in pr_reviews if r[4] == "APPROVED" and r[5]), default=None)
        lag = _hours(first_approval, pr["merged_at"])
        if lag is not None and lag >= 0:
            a["_merge_lag"].append(lag)

        # ---- builder metrics exclude fully-autonomous --------------------
        if autonomy != "fully_autonomous":
            a["_builder_prs"].append(number)
            a["_test_habit"].append(1 if has_tests else 0)
            a["_risk"].append(_risk_weight(pr, paths))
            rounds = sum(1 for r in pr_reviews if r[4] == "CHANGES_REQUESTED")
            a["_rounds"].append(rounds)

            # statusCheckRollup states: SUCCESS / FAILURE / ERROR (PENDING/EXPECTED -> NULL).
            first_state = pr.get("ci_first_state")
            if first_state:
                a["_ci_first_pass"].append(1 if first_state == "SUCCESS" else 0)
            head_state = pr.get("ci_head_state")
            if head_state:
                a["_ci_head_pass"].append(1 if head_state == "SUCCESS" else 0)

            merged_date = pr["merged_at"].date() if hasattr(pr["merged_at"], "date") else None
            # The 14-day censor: recent merges leave the denominator, not just the numerator.
            if merged_date and merged_date <= censor_cutoff:
                a["_revert_denom"].append(number)
                if number in reverted:
                    a["_revert_num"].append(number)
            if number in reverted:
                a["_reverted_n"].append(number)
        if REVERT_TITLE.match(pr["title"] or ""):
            a["_revert_authored"].append(number)

    # -- reviewed (fully-autonomous PRs are KEPT here) -----------------------
    for number, pr in prs.items():
        pr_author = actors.canonical(pr["author_login"] or "")
        seen_in_pr: set[str] = set()
        for r in reviews_by_pr[number]:
            login, atype, state, submitted, body = r[2], r[3], r[4], r[5], r[6]
            if not login or not actors.is_human(login, atype):
                continue
            login = actors.canonical(login)
            if login == pr_author:
                continue
            a = acc[login]
            a["_reviews"].append(number)
            a.setdefault("_authors_reviewed", set()).add(pr_author)
            if state == "APPROVED":
                a["_approvals"].append(number)
                inline = sum(1 for t in threads_by_pr[number] if t[5] and actors.canonical(t[5]) == login)
                # Rubber stamp: an approval with no body AND no inline comment.
                a["_rubber"].append(1 if (not (body or "").strip() and inline == 0) else 0)
            if state == "CHANGES_REQUESTED":
                a["_changes_requested"].append(number)
            inline_n = sum(1 for t in threads_by_pr[number] if t[5] and actors.canonical(t[5]) == login)
            a["_comments_per_review"].append(inline_n)
            if login not in seen_in_pr:
                seen_in_pr.add(login)
                lat = _hours(pr["created_at"], submitted)
                if lat is not None and lat >= 0:
                    a["_latency"].append(lat)

        for t in threads_by_pr[number]:
            login = t[5]
            if not login or not actors.is_human(login, None):
                continue
            login = actors.canonical(login)
            if login == pr_author:
                continue
            acc[login]["_threads_opened"].append(number)
            if t[2]:
                acc[login]["_threads_resolved"].append(number)

    total_prs = len(prs)
    out = {
        login: _finalize(login, a, total_prs)
        for login, a in acc.items()
        if cohort is None or login in cohort
    }
    return out


def _risk_weight(pr: dict[str, Any], paths: list[str]) -> float:
    """Risk rises with breadth, not just size, so a wide shallow change still counts."""
    lines = (pr["additions"] or 0) + (pr["deletions"] or 0)
    files_n = pr["changed_files"] or len(paths) or 1
    dirs = {"/".join(p.split("/")[:2]) for p in paths if p}
    import math
    return round(
        math.log1p(lines) * 0.5 + math.log1p(files_n) * 0.3 + math.log1p(len(dirs)) * 0.2, 4
    )


def _detect_reverts(prs: dict[int, dict], files_by_pr: dict[int, list]) -> set[int]:
    """Which PRs were later reverted, from PR titles and bodies referencing them.

    This is the API-only path. The git-clone path (blobless shallow clone) is optional and
    timeboxed; revert_rate carries 6.45% and renormalises away cleanly if absent.
    """
    reverted: set[int] = set()
    by_title: dict[str, int] = {}
    for n, pr in prs.items():
        t = (pr["title"] or "").strip().lower()
        by_title.setdefault(t, n)
    for n, pr in prs.items():
        title = pr["title"] or ""
        if not REVERT_TITLE.match(title):
            continue
        m = REVERTS_PR.search(title)
        if m:
            target = int(m.group(1))
            if target in prs:
                reverted.add(target)
                continue
        # `Revert "fix(x): thing"` -> find the PR whose title was the quoted string.
        quoted = re.search(r'"(.+)"', title)
        if quoted:
            target = by_title.get(quoted.group(1).strip().lower())
            if target and target != n:
                reverted.add(target)
    return reverted


def _finalize(login: str, a: dict[str, Any], total_prs: int) -> dict[str, Any]:
    authored_all = a.get("_authored_all", [])
    builder_prs = a.get("_builder_prs", [])
    reviews = a.get("_reviews", [])
    approvals = a.get("_approvals", [])

    m: dict[str, tuple[float | None, int]] = {}

    def put(key: str, value: float | None, n: int) -> None:
        m[key] = (value, n)

    # ---- scored ----
    put("revert_rate", _safe(len(a.get("_revert_num", [])), len(a.get("_revert_denom", []))),
        len(a.get("_revert_denom", [])))
    put("ci_first_pass_rate", _mean(a.get("_ci_first_pass", [])), len(a.get("_ci_first_pass", [])))
    put("review_rounds_per_pr", _mean(a.get("_rounds", [])), len(a.get("_rounds", [])))
    put("risk_weighted_contribution", _mean(a.get("_risk", [])), len(a.get("_risk", [])))
    put("test_habit", _mean(a.get("_test_habit", [])), len(a.get("_test_habit", [])))
    put("rubber_stamp_rate", _mean(a.get("_rubber", [])), len(a.get("_rubber", [])))
    put("comment_acceptance_rate",
        _safe(len(a.get("_threads_resolved", [])), len(a.get("_threads_opened", []))),
        len(a.get("_threads_opened", [])))
    put("review_latency_hours", _median(a.get("_latency", [])), len(a.get("_latency", [])))
    put("author_breadth", float(len(a.get("_authors_reviewed", set()) or set())), len(reviews))
    put("review_to_authoring_ratio",
        _safe(len(reviews), len(builder_prs)) if builder_prs else None,
        min(len(reviews), len(builder_prs)) if builder_prs else 0)

    # ---- context ----
    put("merged_pr_count", float(len(authored_all)), len(authored_all))
    put("reviews_given", float(len(reviews)), len(reviews))
    put("dispatched_n", float(len(a.get("_dispatched", []))), len(authored_all))
    put("agent_assisted_rate", _safe(len(a.get("_agent_assisted", [])), len(authored_all)), len(authored_all))
    put("autonomy_unknown_rate", _safe(len(a.get("_autonomy_unknown", [])), len(authored_all)), len(authored_all))
    put("median_pr_size_lines", _median(a.get("_size", [])), len(a.get("_size", [])))
    put("median_files_changed", _median(a.get("_files_changed", [])), len(a.get("_files_changed", [])))
    put("commits_per_pr", _mean(a.get("_commits_n", [])), len(a.get("_commits_n", [])))
    put("median_cycle_time_hours", _median(a.get("_cycle", [])), len(a.get("_cycle", [])))
    put("time_to_merge_after_approval", _median(a.get("_merge_lag", [])), len(a.get("_merge_lag", [])))
    put("ci_head_pass_rate", _mean(a.get("_ci_head_pass", [])), len(a.get("_ci_head_pass", [])))
    put("rework_rate", _mean(a.get("_rework", [])), len(a.get("_rework", [])))
    put("reverted_pr_n", float(len(a.get("_reverted_n", []))), len(builder_prs))
    put("revert_authored_n", float(len(a.get("_revert_authored", []))), len(authored_all))
    put("self_merge_rate", _mean(a.get("_self_merge", [])), len(a.get("_self_merge", [])))
    put("approval_rate", _safe(len(approvals), len(reviews)), len(reviews))
    put("changes_requested_rate", _safe(len(a.get("_changes_requested", [])), len(reviews)), len(reviews))
    put("comments_per_review", _mean(a.get("_comments_per_review", [])), len(reviews))
    put("unique_files_touched", float(len(set(a.get("_paths", [])))), len(authored_all))
    put("unique_dirs_touched",
        float(len({"/".join(p.split("/")[:2]) for p in a.get("_paths", []) if p})), len(authored_all))
    put("test_file_share", _mean(a.get("_test_share", [])), len(a.get("_test_share", [])))
    put("review_participation_rate", _safe(len(reviews), total_prs), total_prs)
    # Fix-only cohort: no variance by construction. Kept so the zero variance is visible.
    put("change_value_mix", 0.0, len(authored_all))

    unknown = set(m) - set(BY_KEY)
    if unknown:
        raise KeyError(f"computed metrics not in registry: {sorted(unknown)}")

    return {
        "login": login,
        "metrics": m,
        "authored_n": len(authored_all),
        "builder_n": len(builder_prs),
        "reviewed_n": len(reviews),
        "dispatched_n": len(a.get("_dispatched", [])),
        "evidence": _evidence_pool(a),
    }


def _evidence_pool(a: dict[str, Any]) -> dict[str, list[int]]:
    return {
        "reverted": list(a.get("_reverted_n", [])),
        "revert_denom": list(a.get("_revert_denom", [])),
        "builder_prs": list(a.get("_builder_prs", [])),
        "dispatched": list(a.get("_dispatched", [])),
        "approvals": list(a.get("_approvals", [])),
        "reviews": list(a.get("_reviews", [])),
    }
