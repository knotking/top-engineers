"""Six-step scoring, every intermediate persisted.

gate -> winsorize -> shrink -> percentile -> weight -> uncertainty

Each step writes its output into `person_metric` so a drill-down can explain a rank
arithmetically. A number whose denominator is a mystery cannot be argued with.
"""

from __future__ import annotations

import logging
import math
import statistics
from typing import Any

from ..config import (
    GATE_MIN_MERGED_PRS,
    GATE_MIN_REVIEWS,
    RATIO_BAND,
    SHRINK_PRIOR_K,
    WINSOR_HI,
    WINSOR_LO,
)
from ..metrics.definitions import (
    BAND,
    BY_KEY,
    LOWER,
    PILLAR_WEIGHTS,
    RATE_METRICS,
    SCORED,
    SCORED_KEYS,
    effective_weights,
)

log = logging.getLogger(__name__)


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def gate(person: dict[str, Any]) -> tuple[bool, bool]:
    """Below the gate we still COMPUTE and SHOW metrics -- we withhold only the rank."""
    return (
        person["builder_n"] >= GATE_MIN_MERGED_PRS,
        person["reviewed_n"] >= GATE_MIN_REVIEWS,
    )


def band_score(value: float, band: tuple[float, float] = RATIO_BAND) -> float:
    """Distance from a healthy band, mapped to 0-1 (1 = inside the band).

    review_to_authoring_ratio is NOT monotonic: scoring it higher-is-better ranks someone who
    only reviews above someone who does both.
    """
    lo, hi = band
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, value / lo) if lo > 0 else 0.0
    # Above the band, decay on a log scale so 4x is bad but not catastrophic.
    return max(0.0, 1.0 / (1.0 + math.log(value / hi)))


def score_cohort(people: dict[str, dict[str, Any]], pool: set[str] | None = None) -> dict[str, Any]:
    """Score everyone, normalising within the eligible pool."""
    pool = pool or set(people)
    rows: list[dict[str, Any]] = []
    gates = {login: gate(p) for login, p in people.items()}

    # ---- step 1+2: gate, then winsorize within the ELIGIBLE pool ----------
    bounds: dict[str, tuple[float, float]] = {}
    cohort_mean: dict[str, float] = {}
    for key in SCORED_KEYS:
        metric = BY_KEY[key]
        vals = [
            p["metrics"][key][0]
            for login, p in people.items()
            if login in pool
            and p["metrics"][key][0] is not None
            and _eligible_for(metric, gates[login])
        ]
        if not vals:
            continue
        bounds[key] = (_quantile(vals, WINSOR_LO), _quantile(vals, WINSOR_HI))
        cohort_mean[key] = statistics.fmean(vals)

    # ---- step 3: shrink rate metrics toward the cohort mean ---------------
    staged: dict[str, dict[str, dict[str, Any]]] = {}
    for login, person in people.items():
        staged[login] = {}
        for key in SCORED_KEYS:
            metric = BY_KEY[key]
            raw, n = person["metrics"][key]
            if raw is None or not _eligible_for(metric, gates[login]) or key not in bounds:
                continue
            lo, hi = bounds[key]
            wins = min(max(raw, lo), hi)
            if key in RATE_METRICS and n:
                # (x*n + mean*k)/(n+k). Without this a perfect record over 3 PRs wins.
                mean = cohort_mean[key]
                shrunk = (wins * n + mean * SHRINK_PRIOR_K) / (n + SHRINK_PRIOR_K)
            else:
                shrunk = wins
            staged[login][key] = {"raw": raw, "n": n, "winsorized": wins, "shrunk": shrunk}

    # ---- step 4: percentile within the eligible pool ----------------------
    for key in SCORED_KEYS:
        metric = BY_KEY[key]
        vals = [
            (login, s[key]["shrunk"])
            for login, s in staged.items()
            if key in s and login in pool
        ]
        if not vals:
            continue
        if metric.direction == BAND:
            scored_vals = [(lg, band_score(v)) for lg, v in vals]
        else:
            scored_vals = list(vals)
        ordered = sorted(v for _, v in scored_vals)
        for login, v in scored_vals:
            pct = _percentile_of(ordered, v)
            if metric.direction == LOWER:
                pct = 1.0 - pct  # sign-flip lower-is-better
            for lg, s in staged.items():
                if lg == login and key in s:
                    s[key]["percentile"] = pct
                    s[key]["band_value"] = v if metric.direction == BAND else None
        # People outside the pool are scored against the pool's distribution, not their own.
        for login, s in staged.items():
            if key in s and "percentile" not in s[key]:
                v = band_score(s[key]["shrunk"]) if metric.direction == BAND else s[key]["shrunk"]
                pct = _percentile_of(ordered, v)
                if metric.direction == LOWER:
                    pct = 1.0 - pct
                s[key]["percentile"] = pct

    # ---- step 5+6: weight, contribution, uncertainty ---------------------
    results: dict[str, Any] = {}
    for login, person in people.items():
        present = set(staged[login])
        weights = effective_weights(present)  # missing metrics RENORMALISE, never count as zero
        score = 0.0
        var = 0.0
        pillar_scores: dict[str, float] = {p: 0.0 for p in PILLAR_WEIGHTS}
        pillar_weight: dict[str, float] = {p: 0.0 for p in PILLAR_WEIGHTS}

        for key, s in staged[login].items():
            metric = BY_KEY[key]
            w = weights.get(key, 0.0)
            contribution = w * s["percentile"]
            score += contribution
            se = _std_error(key, s)
            s["weight"] = w
            s["contribution"] = contribution
            s["std_error"] = se
            var += (w * se) ** 2
            pillar_scores[metric.pillar] += contribution
            pillar_weight[metric.pillar] += w

        std_error = math.sqrt(var)
        results[login] = {
            "login": login,
            "score": score,
            "std_error": std_error,
            "ci_low": max(0.0, score - 1.96 * std_error),
            "ci_high": min(1.0, score + 1.96 * std_error),
            "metrics": staged[login],
            "builder_eligible": gates[login][0],
            "reviewer_eligible": gates[login][1],
            "ranked": gates[login][0] or gates[login][1],
            "authored_n": person["authored_n"],
            "builder_n": person["builder_n"],
            "reviewed_n": person["reviewed_n"],
            "dispatched_n": person["dispatched_n"],
            "pillar_scores": {
                p: (pillar_scores[p] / pillar_weight[p] if pillar_weight[p] else None)
                for p in PILLAR_WEIGHTS
            },
            "evidence": person.get("evidence", {}),
        }

    _assign_ranks_and_tie_bands(results)
    return results


def _eligible_for(metric, gates: tuple[bool, bool]) -> bool:
    builder_ok, reviewer_ok = gates
    if metric.pillar == "builder":
        return builder_ok
    if metric.pillar == "reviewer":
        return reviewer_ok
    return builder_ok or reviewer_ok


def _percentile_of(ordered: list[float], value: float) -> float:
    if not ordered:
        return 0.5
    if len(ordered) == 1:
        return 0.5
    below = sum(1 for v in ordered if v < value)
    equal = sum(1 for v in ordered if v == value)
    # Midrank, so ties share a percentile instead of being ordered arbitrarily.
    return (below + 0.5 * equal) / len(ordered)


def _std_error(key: str, s: dict[str, Any]) -> float:
    """Analytic propagation from each metric's OWN denominator.

    A true bootstrap is 33 metrics x N people x 1,000 resamples -- not viable. This answers
    the same question: how much of this number is noise from a small denominator?
    """
    n = s.get("n") or 0
    if n <= 1:
        return 0.5  # a single observation is almost pure noise
    if key in RATE_METRICS:
        p = min(max(s["shrunk"], 0.0), 1.0)
        return math.sqrt(max(p * (1 - p), 1e-6) / n)
    # Non-rates: percentile-space noise falls off with sqrt(n), floored so it never vanishes.
    return min(0.5, 1.0 / math.sqrt(n))


def _assign_ranks_and_tie_bands(results: dict[str, Any]) -> None:
    """Overlapping intervals share a TIE BAND.

    Do not fake a 1-2-3-4-5 ordering the data cannot support.
    """
    ranked = sorted(
        [r for r in results.values() if r["ranked"]],
        key=lambda r: (-r["score"], r["login"]),
    )
    band = 0
    leader_low: float | None = None
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
        # Anchor on the band LEADER's lower bound, not a running minimum. Tracking the
        # minimum chains transitively (A~B, B~C => A,B,C one band) and on any smooth score
        # distribution that collapses everyone into a single band, which says nothing.
        if leader_low is None or r["ci_high"] < leader_low:
            band += 1
            leader_low = r["ci_low"]
        r["tie_band"] = band
    for r in results.values():
        if not r["ranked"]:
            r["rank"] = None
            r["tie_band"] = None
