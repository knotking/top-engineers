"""The six scoring steps, and the properties the plan calls non-negotiable."""

from __future__ import annotations

import pytest

from top_engineers.metrics.definitions import (
    ABSOLUTE_WEIGHTS,
    BY_KEY,
    SCORED,
    effective_weights,
)
from top_engineers.scoring.score import band_score, score_cohort


def person(login, n_authored=20, n_reviewed=20, **metrics):
    base = {k: (None, 0) for k in BY_KEY}
    base.update(metrics)
    return {
        "login": login, "metrics": base,
        "authored_n": n_authored, "builder_n": n_authored, "reviewed_n": n_reviewed,
        "dispatched_n": 0, "evidence": {},
    }


def test_weights_sum_to_one_per_pillar():
    assert abs(sum(ABSOLUTE_WEIGHTS.values()) - 1.0) < 1e-9


def test_missing_metrics_renormalise_never_count_as_zero():
    w = effective_weights({"ci_first_pass_rate", "test_habit"})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert set(w) == {"ci_first_pass_rate", "test_habit"}


def test_context_only_metrics_carry_no_weight():
    """revert_rate is computed and shown, but must not influence the score."""
    assert "revert_rate" not in ABSOLUTE_WEIGHTS
    assert effective_weights({"revert_rate", "test_habit"}) == {"test_habit": 1.0}


def test_shrinkage_beats_small_sample_perfection():
    """Without shrinkage a perfect record over 3 PRs wins. Non-negotiable."""
    people = {
        "tiny": person("tiny", 6, test_habit=(1.00, 3)),
        "solid": person("solid", 40, test_habit=(0.90, 40)),
        "mid": person("mid", 20, test_habit=(0.50, 20)),
        "low": person("low", 20, test_habit=(0.20, 20)),
    }
    res = score_cohort(people)
    assert res["tiny"]["metrics"]["test_habit"]["shrunk"] < 1.00
    assert res["solid"]["score"] > res["tiny"]["score"]


def test_shrink_formula_is_exact_and_follows_winsorization():
    """Order matters: winsorize (step 2) feeds shrink (step 3), not the raw value."""
    people = {
        "a": person("a", 10, test_habit=(1.0, 5)),
        "b": person("b", 10, test_habit=(0.0, 5)),
    }
    res = score_cohort(people)
    mean = 0.5  # cohort mean of the raw values
    for lg in ("a", "b"):
        chain = res[lg]["metrics"]["test_habit"]
        # p5/p95 over [0.0, 1.0] clamps the extremes before shrinkage touches them.
        assert chain["winsorized"] == pytest.approx(0.95 if lg == "a" else 0.05, abs=1e-9)
        expected = (chain["winsorized"] * 5 + mean * 5) / (5 + 5)
        assert chain["shrunk"] == pytest.approx(expected, abs=1e-9)
    # And the composed result pulls both extremes toward the middle.
    assert 0.5 < res["a"]["metrics"]["test_habit"]["shrunk"] < 1.0
    assert 0.0 < res["b"]["metrics"]["test_habit"]["shrunk"] < 0.5


def test_lower_is_better_is_sign_flipped():
    people = {
        "slow": person("slow", 20, review_latency_hours=(48.0, 20)),
        "fast": person("fast", 20, review_latency_hours=(2.0, 20)),
    }
    res = score_cohort(people)
    assert res["fast"]["metrics"]["review_latency_hours"]["percentile"] > \
           res["slow"]["metrics"]["review_latency_hours"]["percentile"]


def test_ratio_is_banded_not_monotonic():
    """Scoring it higher-is-better ranks someone who only reviews above someone who does both."""
    assert band_score(1.0) == 1.0
    assert band_score(0.5) == 1.0
    assert band_score(2.0) == 1.0
    assert band_score(20.0) < band_score(1.5)
    assert band_score(0.05) < band_score(1.0)


def test_gate_withholds_rank_but_keeps_metrics():
    people = {
        "big": person("big", 40, 40, test_habit=(0.8, 40)),
        "small": person("small", 2, 1, test_habit=(0.9, 2)),
    }
    res = score_cohort(people)
    assert res["big"]["rank"] is not None
    assert res["small"]["rank"] is None
    assert res["small"]["builder_eligible"] is False


def test_overlapping_intervals_share_a_tie_band():
    """Do not fake a 1-2-3-4-5 ordering the data cannot support."""
    people = {
        "a": person("a", 30, test_habit=(0.8, 30)),
        "b": person("b", 30, test_habit=(0.8, 30)),
        "c": person("c", 30, test_habit=(0.8, 30)),
        "hi": person("hi", 30, test_habit=(0.99, 30)),
        "lo": person("lo", 30, test_habit=(0.05, 30)),
    }
    res = score_cohort(people)
    bands = {res[k]["tie_band"] for k in ("a", "b", "c")}
    assert len(bands) == 1, "indistinguishable people must share a band"
    assert res["hi"]["tie_band"] != res["lo"]["tie_band"]


def test_every_intermediate_is_persisted():
    """Drill-down must be able to explain a rank arithmetically."""
    people = {"a": person("a", 20, test_habit=(0.8, 20)),
              "b": person("b", 20, test_habit=(0.2, 20))}
    res = score_cohort(people)
    chain = res["a"]["metrics"]["test_habit"]
    for step in ("raw", "winsorized", "shrunk", "percentile", "weight", "contribution", "std_error"):
        assert step in chain, f"missing intermediate: {step}"


def test_std_error_shrinks_with_denominator():
    people = {
        "few": person("few", 20, test_habit=(0.5, 6)),
        "many": person("many", 40, test_habit=(0.5, 200)),
    }
    res = score_cohort(people)
    assert res["few"]["metrics"]["test_habit"]["std_error"] > \
           res["many"]["metrics"]["test_habit"]["std_error"]


def test_tie_bands_do_not_chain_transitively():
    """A must not share a band with C just because both overlap B.

    Tracking a running minimum of ci_low chains transitively and collapses a smooth
    distribution into one meaningless band -- observed on real data, where rank 1
    [0.637,0.766] and rank 50 [0.051,0.368] shared a band despite no overlap.
    """
    people = {f"p{i}": person(f"p{i}", 30, test_habit=(v, 30))
              for i, v in enumerate([0.99, 0.80, 0.60, 0.40, 0.20, 0.01])}
    res = score_cohort(people)
    ranked = sorted([r for r in res.values() if r["rank"]], key=lambda r: r["rank"])
    top, bottom = ranked[0], ranked[-1]
    assert top["ci_low"] > bottom["ci_high"], "fixture must have a genuine separation"
    assert top["tie_band"] != bottom["tie_band"], "non-overlapping people must not share a band"


def test_band_members_all_overlap_the_band_leader():
    people = {f"p{i}": person(f"p{i}", 30, test_habit=(v, 30))
              for i, v in enumerate([0.99, 0.95, 0.90, 0.50, 0.10, 0.02])}
    res = score_cohort(people)
    ranked = sorted([r for r in res.values() if r["rank"]], key=lambda r: r["rank"])
    leaders = {}
    for r in ranked:
        leaders.setdefault(r["tie_band"], r)
        assert r["ci_high"] >= leaders[r["tie_band"]]["ci_low"], \
            f"{r['login']} does not overlap its band leader"
