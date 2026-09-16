"""Build the serving layer.

The UI must do ZERO computation: every number, label, note and evidence link is materialised
here. That is what makes <10s structural rather than something to tune later.
"""

from __future__ import annotations

import logging
from typing import Any

import duckdb

from ..config import DISPLAY_SIZE, GATE_MIN_MERGED_PRS, GATE_MIN_REVIEWS, RATIO_BAND, REVERT_CENSOR_DAYS
from ..metrics.definitions import BAND, BY_KEY, LOWER, SCORED_KEYS
from .load import insert_rows, set_meta

log = logging.getLogger(__name__)


def _fmt(key: str, value: float | None) -> str:
    if value is None:
        return "--"
    m = BY_KEY[key]
    if m.label_unit == "%":
        return f"{value * 100:.0f}%"
    if m.label_unit == "h":
        return f"{value:.1f}h" if value < 48 else f"{value / 24:.1f}d"
    if m.label_unit == "x":
        return f"{value:.2f}x"
    if m.label_unit:
        return f"{value:,.0f}{m.label_unit}"
    return f"{value:.2f}"


def persist_scores(
    con: duckdb.DuckDBPyConnection,
    results: dict[str, Any],
    people: dict[str, Any],
) -> None:
    con.execute("DELETE FROM person_metric")
    con.execute("DELETE FROM person_score")

    metric_rows = []
    for login, r in results.items():
        person = people[login]
        for key in BY_KEY:
            metric = BY_KEY[key]
            raw, n = person["metrics"].get(key, (None, 0))
            chain = r["metrics"].get(key, {})
            if raw is None and not chain:
                continue  # hide metrics with no value at all
            metric_rows.append((
                login, key, metric.pillar, metric.sub_pillar, metric.direction,
                metric.scored and bool(chain), n,
                raw, chain.get("winsorized"), chain.get("shrunk"),
                chain.get("percentile"), chain.get("weight"),
                chain.get("contribution"), chain.get("std_error"),
            ))
    insert_rows(
        con, "person_metric",
        ("login", "metric", "pillar", "sub_pillar", "direction", "scored", "n",
         "raw", "winsorized", "shrunk", "percentile", "weight", "contribution", "std_error"),
        metric_rows,
    )

    score_rows = [
        (
            login, r["score"], r["std_error"], r["ci_low"], r["ci_high"], r["tie_band"], r["rank"],
            r["pillar_scores"].get("builder"), r["pillar_scores"].get("reviewer"),
            r["pillar_scores"].get("cross_cutting"),
            r["authored_n"], r["reviewed_n"], r["dispatched_n"],
            r["builder_eligible"], r["reviewer_eligible"],
        )
        for login, r in results.items()
    ]
    insert_rows(
        con, "person_score",
        ("login", "score", "std_error", "ci_low", "ci_high", "tie_band", "rank",
         "builder_score", "reviewer_score", "cross_score",
         "authored_n", "reviewed_n", "dispatched_n", "builder_eligible", "reviewer_eligible"),
        score_rows,
    )


def build_serving(
    con: duckdb.DuckDBPyConnection,
    results: dict[str, Any],
    people: dict[str, Any],
    meta: dict[str, str] | None = None,
) -> None:
    for table in ("serving_leaderboard", "serving_metric_chain", "serving_evidence", "serving_caveats"):
        con.execute(f"DELETE FROM {table}")

    ranked = sorted(
        [r for r in results.values() if r["rank"]],
        key=lambda r: r["rank"],
    )
    insert_rows(
        con, "serving_leaderboard",
        ("rank", "login", "avatar_url", "profile_url", "score", "ci_low", "ci_high", "tie_band",
         "builder_score", "reviewer_score", "cross_score", "authored_n", "reviewed_n",
         "dispatched_n", "builder_eligible", "reviewer_eligible", "is_display"),
        [
            (
                r["rank"], r["login"],
                # No API call or id lookup needed for an avatar.
                f"https://github.com/{r['login']}.png?size=160",
                f"https://github.com/{r['login']}",
                r["score"], r["ci_low"], r["ci_high"], r["tie_band"],
                r["pillar_scores"].get("builder"), r["pillar_scores"].get("reviewer"),
                r["pillar_scores"].get("cross_cutting"),
                r["authored_n"], r["reviewed_n"], r["dispatched_n"],
                r["builder_eligible"], r["reviewer_eligible"],
                r["rank"] <= DISPLAY_SIZE,
            )
            for r in ranked
        ],
    )

    chain_rows = []
    for login, r in results.items():
        person = people[login]
        for key, metric in BY_KEY.items():
            raw, n = person["metrics"].get(key, (None, 0))
            chain = r["metrics"].get(key, {})
            if raw is None and not chain:
                continue  # Hide metrics with no value.
            chain_rows.append((
                login, key, metric.label, metric.pillar, metric.sub_pillar, metric.direction,
                metric.scored and bool(chain), n, raw,
                chain.get("winsorized"), chain.get("shrunk"), chain.get("percentile"),
                chain.get("weight"), chain.get("contribution"), chain.get("std_error"),
                _fmt(key, raw), metric.how,
            ))
    insert_rows(
        con, "serving_metric_chain",
        ("login", "metric", "label", "pillar", "sub_pillar", "direction", "scored", "n", "raw",
         "winsorized", "shrunk", "percentile", "weight", "contribution", "std_error",
         "display_value", "how_note"),
        chain_rows,
    )

    _build_evidence(con, results, people)
    _build_caveats(con)
    for k, v in (meta or {}).items():
        set_meta(con, k, v)


def _build_evidence(con: duckdb.DuckDBPyConnection, results: dict[str, Any], people: dict[str, Any]) -> None:
    """Evidence favours PRs explaining a BAD score.

    The reverted change, the 3-week PR, the 4,000-line diff, the approval with no comment --
    those are what people will challenge, so those are what must be one click away.
    """
    pr_meta = {
        row[0]: {"url": row[1], "title": row[2], "additions": row[3] or 0,
                 "deletions": row[4] or 0, "created": row[5], "merged": row[6]}
        for row in con.execute(
            "SELECT number, url, title, additions, deletions, created_at, merged_at FROM raw_pr"
        ).fetchall()
    }

    rows: list[tuple] = []
    for login, r in results.items():
        ev = people[login].get("evidence", {})
        chains = r["metrics"]

        for number in ev.get("reverted", [])[:5]:
            meta = pr_meta.get(number)
            if meta:
                rows.append((login, "revert_rate", number, meta["url"], meta["title"],
                             "This PR was later reverted", True, 1000.0))

        # Biggest diffs, as the concrete case behind a risk/rounds score.
        builder_prs = ev.get("builder_prs", [])
        by_size = sorted(
            (n for n in builder_prs if n in pr_meta),
            key=lambda n: -(pr_meta[n]["additions"] + pr_meta[n]["deletions"]),
        )[:3]
        for number in by_size:
            meta = pr_meta[number]
            size = meta["additions"] + meta["deletions"]
            rows.append((login, "risk_weighted_contribution", number, meta["url"], meta["title"],
                         f"{size:,} lines changed", size > 1000, float(size)))

        # Slowest merges, as the case behind a latency/rounds score.
        by_age = []
        for number in builder_prs:
            meta = pr_meta.get(number)
            if meta and meta["created"] and meta["merged"]:
                hours = (meta["merged"] - meta["created"]).total_seconds() / 3600
                by_age.append((hours, number))
        for hours, number in sorted(by_age, reverse=True)[:3]:
            meta = pr_meta[number]
            rows.append((login, "review_rounds_per_pr", number, meta["url"], meta["title"],
                         f"open {hours / 24:.1f} days", hours > 24 * 14, hours))

        # Rubber-stamped approvals: the approval with no comment.
        if chains.get("rubber_stamp_rate", {}).get("raw", 0):
            for number in ev.get("approvals", [])[:3]:
                meta = pr_meta.get(number)
                if meta:
                    rows.append((login, "rubber_stamp_rate", number, meta["url"], meta["title"],
                                 "approved with no comment", True, 500.0))

        for number in ev.get("dispatched", [])[:3]:
            meta = pr_meta.get(number)
            if meta:
                rows.append((login, "dispatched_n", number, meta["url"], meta["title"],
                             "Fully autonomous -- excluded from builder scoring", False, 1.0))

    insert_rows(
        con, "serving_evidence",
        ("login", "metric", "pr_number", "url", "title", "reason", "is_adverse", "sort_key"),
        rows,
    )


def _build_caveats(con: duckdb.DuckDBPyConnection) -> None:
    caveats = [
        ("Conversation starter, not a verdict",
         "These ranks describe HOW a narrow slice of work landed. They are a prompt for a "
         "conversation, not a performance review, and they cannot see design, mentoring, "
         "incident response, or anything that never became a pull request."),
        ("Volume-selected cohort",
         "People entered this cohort by participating in the most fix/bug PRs, then were "
         "judged on non-volume metrics. High participation is a selection criterion here, "
         "never a score."),
        ("Fix and bug PRs only",
         "Only PRs whose titles carry a fix/revert conventional-commit prefix are in scope. "
         "Feature work, refactors and chores are invisible to every number on this page."),
        (f"{REVERT_CENSOR_DAYS}-day revert censoring",
         f"PRs merged in the last {REVERT_CENSOR_DAYS} days are excluded from the revert-rate "
         "DENOMINATOR entirely -- they have not existed long enough to be observed for a full "
         "revert window. They are not counted as clean."),
        ("Autonomy exclusion",
         "PRs self-declared 'Fully autonomous' are excluded from builder metrics -- they were "
         "dispatched, not authored -- but KEPT in reviewer metrics, because reviewing an "
         "agent's PR is real review work. The excluded count is shown per person as "
         "'dispatched'; excluded-from-scoring is not did-not-happen."),
        ("Small-pool percentiles",
         "Percentiles are computed within a wider normalisation pool and only the head of it "
         "is displayed. Even so, ranks over a small pool are coarse buckets, and the "
         "shrinkage prior is noisy where denominators are small."),
        ("Overlapping intervals share a tie band",
         "Where two people's uncertainty intervals overlap they are placed in the same tie "
         "band. Ranks inside a band are not a real ordering and should not be read as one."),
        (f"Ranks are withheld below the gate",
         f"A builder rank needs at least {GATE_MIN_MERGED_PRS} merged PRs and a reviewer rank "
         f"at least {GATE_MIN_REVIEWS} reviews. Below the gate metrics are still computed and "
         "shown, but no rank is claimed."),
        ("Balance is a band, not a target",
         f"Review-to-authoring ratio is scored by distance from a healthy band "
         f"({RATIO_BAND[0]}-{RATIO_BAND[1]}x). Reviewing far more than you author is not "
         "better than doing both."),
    ]
    insert_rows(
        con, "serving_caveats", ("seq", "title", "body"),
        [(i, t, b) for i, (t, b) in enumerate(caveats)],
    )
