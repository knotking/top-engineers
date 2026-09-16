"""Metric registry: 33 computed, 10 scored.

Selection criteria, in order: has data on the window; SEPARATES people rather than clustering
them; measures HOW work landed, not HOW MUCH.

No volume metrics are scored (PR count, cycle time, LOC). They reward agent throughput -- and
one measured account was 91% fully-autonomous at 15 PRs/day, which is exactly the person a
volume metric crowns.

WEIGHTS ARE EXPLICIT, not inherited from equal-splitting. Equal-split-within-sub-pillar lets a
lone survivor swallow its whole sub-pillar share; these numbers pin that concentration down so
it is a decision rather than an accident. `effective_weights()` reports what actually applies
after gating and renormalisation.
"""

from __future__ import annotations

from dataclasses import dataclass

HIGHER = "higher_is_better"
LOWER = "lower_is_better"
BAND = "band"

BUILDER, REVIEWER, CROSS, CONTEXT = "builder", "reviewer", "cross_cutting", "context"
PILLAR_WEIGHTS = {BUILDER: 0.45, REVIEWER: 0.40, CROSS: 0.15}


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    pillar: str
    sub_pillar: str
    direction: str
    scored: bool
    # Share WITHIN its pillar, not of the total. Absolute weights are derived in
    # ABSOLUTE_WEIGHTS so that demoting a metric rebalances its pillar exactly, instead of
    # leaving hand-tuned constants that silently stop summing to the pillar weight.
    weight: float
    label_unit: str = ""
    how: str = ""          # plain-English computation note, INCLUDING the exclusions


SCORED: tuple[Metric, ...] = (
    # ---- BUILDER (45%) -------------------------------------------------------
    Metric(
        "ci_first_pass_rate", "CI first-pass rate", BUILDER, "outcome_quality", HIGHER, True, 0.0645, "%",
        "Share of your PRs whose FIRST pushed commit passed CI -- not the head commit. A PR "
        "that went red and was fixed up did not pass first time. PRs with no CI signal are "
        "excluded. Fully-autonomous PRs are excluded.",
    ),
    Metric(
        "review_rounds_per_pr", "Review rounds per PR", BUILDER, "outcome_quality", LOWER, True, 0.0645, "",
        "Mean number of changes-requested rounds before your PRs merged. Counts distinct "
        "CHANGES_REQUESTED reviews, not comments. Fully-autonomous PRs are excluded.",
    ),
    Metric(
        "risk_weighted_contribution", "Risk-weighted contribution", BUILDER, "contribution", HIGHER, True, 0.117, "",
        "Mean risk weight of the PRs you landed, where risk rises with files touched, lines "
        "changed and breadth of directories. Rewards landing hard changes well, not landing "
        "many. Fully-autonomous PRs are excluded.",
    ),
    Metric(
        "test_habit", "Test habit", BUILDER, "craft", HIGHER, True, 0.1395, "%",
        "Share of your merged PRs that touched at least one test file. Fully-autonomous PRs "
        "are excluded.",
    ),
    # ---- REVIEWER (40%) ------------------------------------------------------
    Metric(
        "rubber_stamp_rate", "Rubber-stamp rate", REVIEWER, "review_quality", LOWER, True, 0.10, "%",
        "Share of your APPROVALS that carried no review body and no inline comment. "
        "Fully-autonomous PRs are KEPT here -- reviewing an agent's PR is real review work.",
    ),
    Metric(
        "comment_acceptance_rate", "Comment acceptance rate", REVIEWER, "review_quality", HIGHER, True, 0.10, "%",
        "Share of the review threads you opened that ended resolved. Measures whether your "
        "comments landed, not how many you left.",
    ),
    Metric(
        "review_latency_hours", "Review latency", REVIEWER, "responsiveness", LOWER, True, 0.10, "h",
        "Median hours from PR creation to your first review on it. Only PRs you actually "
        "reviewed count.",
    ),
    Metric(
        "author_breadth", "Author breadth", REVIEWER, "reach", HIGHER, True, 0.10, "",
        "Number of DISTINCT authors whose PRs you reviewed. Reviewing widely spreads context; "
        "reviewing one teammate repeatedly does not.",
    ),
    # ---- CROSS-CUTTING (15%) -------------------------------------------------
    Metric(
        "review_to_authoring_ratio", "Review : authoring balance", CROSS, "balance", BAND, True, 0.15, "x",
        "Reviews given divided by PRs authored, scored by DISTANCE FROM A HEALTHY BAND "
        "(0.5-2.0). This is NOT higher-is-better: scoring it that way ranks someone who only "
        "reviews above someone who does both.",
    ),
)

CONTEXT_METRICS: tuple[Metric, ...] = tuple(
    Metric(key, label, CONTEXT, sub, direction, False, 0.0, unit, how)
    for key, label, sub, direction, unit, how in (
        ("merged_pr_count", "Merged PRs", "volume", HIGHER, "",
         "Merged PRs authored in window. NOT SCORED -- volume rewards agent throughput."),
        ("reviews_given", "Reviews given", "volume", HIGHER, "", "Reviews submitted in window. Not scored."),
        ("dispatched_n", "Dispatched (fully autonomous)", "autonomy", HIGHER, "",
         "PRs you marked Fully autonomous. EXCLUDED from builder scoring -- dispatched, not "
         "authored -- but shown because excluded-from-scoring is not did-not-happen."),
        ("agent_assisted_rate", "Agent-assisted share", "autonomy", HIGHER, "%",
         "Share of your PRs declared Human-driven (agent-assisted)."),
        ("autonomy_unknown_rate", "Autonomy undeclared", "autonomy", LOWER, "%",
         "Share of your PRs with no parseable Autonomy field, including unedited templates."),
        ("median_pr_size_lines", "Median PR size", "shape", HIGHER, " lines", "Median additions+deletions. Not scored."),
        ("median_files_changed", "Median files changed", "shape", HIGHER, "", "Median changed files per PR."),
        ("commits_per_pr", "Commits per PR", "shape", HIGHER, "", "Mean commits per merged PR."),
        ("median_cycle_time_hours", "Median cycle time", "speed", LOWER, "h",
         "Median hours from PR creation to merge. NOT SCORED -- speed rewards throughput."),
        ("time_to_merge_after_approval", "Merge lag after approval", "speed", LOWER, "h",
         "Median hours from first approval to merge."),
        ("ci_head_pass_rate", "CI pass rate at merge", "outcome", HIGHER, "%",
         "Share of PRs whose FINAL commit passed CI. Contrast with first-pass rate: the gap "
         "between them is work that went red and was fixed up before merging."),
        ("rework_rate", "Rework rate", "outcome", LOWER, "%",
         "Share of PRs with commits pushed after the first review arrived."),
        ("reverted_pr_n", "PRs reverted", "outcome", LOWER, "", "Count of your PRs later reverted."),
        ("revert_authored_n", "Reverts authored", "outcome", HIGHER, "",
         "Reverts you wrote. Cleaning up is not a demerit; shown as context."),
        ("self_merge_rate", "Self-merge rate", "process", LOWER, "%",
         "Share of your merged PRs with no approving review from another person."),
        ("approval_rate", "Approval rate", "review_style", HIGHER, "%", "Share of your reviews that approved."),
        ("changes_requested_rate", "Changes-requested rate", "review_style", HIGHER, "%",
         "Share of your reviews that requested changes."),
        ("comments_per_review", "Comments per review", "review_style", HIGHER, "",
         "Mean inline comments per review you submitted."),
        ("unique_files_touched", "Distinct files touched", "reach", HIGHER, "", "Distinct file paths across your PRs."),
        ("unique_dirs_touched", "Distinct directories touched", "reach", HIGHER, "",
         "Distinct top-two-level directories across your PRs."),
        ("test_file_share", "Test-file share of diff", "craft", HIGHER, "%",
         "Share of changed files that were test files."),
        ("review_participation_rate", "Review participation", "balance", HIGHER, "%",
         "Share of in-scope PRs you reviewed."),
        # weekend_activity_share was deliberately dropped: surfacing weekend work in a
        # ranking tool invites exactly the misuse the caveats panel warns against.
        # DEMOTED from scored. Measured on the 90-day window: only 23 revert-titled PRs in
        # the corpus and just 1 of 50 people with a nonzero rate, so 49 people shared an
        # identical percentile. It added a constant to everyone -- no separation -- while
        # consuming 6.45% of the weight and diluting the metrics that do discriminate.
        # Fails the plan's own second selection criterion: "separates people rather than
        # clustering them". Still computed and shown, because a near-zero revert rate across
        # a team is itself worth seeing.
        ("revert_rate", "Revert rate", "outcome", LOWER, "%",
         "Share of your merged PRs later reverted. PRs merged in the final 14 days are "
         "EXCLUDED FROM THE DENOMINATOR entirely -- they cannot yet be observed for a full "
         "revert window. Fully-autonomous PRs are excluded. NOT SCORED: too few reverts in "
         "this window to separate anyone."),
        ("change_value_mix", "Change value mix", "shape", HIGHER, "",
         "Feature/fix/chore mix. NEAR-USELESS on a fix-only cohort -- no variance by "
         "construction. Retained only so the zero variance is visible."),
    )
)

# Metrics that are proportions on a bounded 0-1 scale. Only these get shrunk toward the
# cohort mean -- shrinking a duration or a raw count toward a mean is not meaningful.
RATE_METRICS = frozenset({
    "revert_rate", "ci_first_pass_rate", "test_habit",
    "rubber_stamp_rate", "comment_acceptance_rate",
})

ALL_METRICS: tuple[Metric, ...] = SCORED + CONTEXT_METRICS
BY_KEY = {m.key: m for m in ALL_METRICS}
SCORED_KEYS = tuple(m.key for m in SCORED)


def _absolute_weights() -> dict[str, float]:
    """Turn within-pillar shares into shares of the total score."""
    out: dict[str, float] = {}
    for pillar, pillar_weight in PILLAR_WEIGHTS.items():
        members = [m for m in SCORED if m.pillar == pillar]
        total_share = sum(m.weight for m in members)
        if not total_share:
            continue
        for m in members:
            out[m.key] = pillar_weight * m.weight / total_share
    return out


ABSOLUTE_WEIGHTS: dict[str, float] = _absolute_weights()


def _assert_weights() -> None:
    total = sum(ABSOLUTE_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"scored weights must sum to 1.0, got {total}"
    for pillar, expected in PILLAR_WEIGHTS.items():
        got = sum(w for k, w in ABSOLUTE_WEIGHTS.items() if BY_KEY[k].pillar == pillar)
        assert abs(got - expected) < 1e-9, f"{pillar} weights sum to {got}, expected {expected}"


_assert_weights()


def effective_weights(present: set[str]) -> dict[str, float]:
    """Weights after renormalising over the metrics actually present.

    Missing metrics RENORMALISE over what is present; they never count as zero.
    """
    live = {k: w for k, w in ABSOLUTE_WEIGHTS.items() if k in present}
    total = sum(live.values())
    if total <= 0:
        return {}
    return {k: w / total for k, w in live.items()}
