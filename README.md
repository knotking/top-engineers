# top-engineers

Ranks engineers in [PostHog/posthog](https://github.com/PostHog/posthog) on **how** their
fix/bug work landed — not how much of it there was — over a rolling 90-day window.

No volume metrics are scored. PR count, cycle time and lines changed all reward agent
throughput, and on this repo one account authored **1,285 PRs in 90 days, 90% of them
self-declared fully autonomous**. A volume leaderboard crowns that account; this one does not.

```
 #  band  engineer           score      interval     authored/reviewed/dispatched
 1    1   VojtechBartos     0.709  [0.583,0.835]      49/40/0
 2    1   tatoalo           0.685  [0.619,0.750]     192/119/0
 3    1   dmarticus         0.675  [0.570,0.779]      50/31/0
...
27    2   Gilbert09         0.506  [    …      ]    1285/173/1160
```

## What it measures

**9 scored metrics** across three pillars, plus **24 shown as zero-weight context**:

| Pillar | Weight | Metrics |
|---|---|---|
| Builder | 45% | `test_habit` 16.3% · `risk_weighted_contribution` 13.7% · `ci_first_pass_rate` 7.5% · `review_rounds_per_pr` 7.5% |
| Reviewer | 40% | `rubber_stamp_rate` · `comment_acceptance_rate` · `review_latency_hours` · `author_breadth` — 10% each |
| Cross-cutting | 15% | `review_to_authoring_ratio`, scored by **distance from a healthy 0.5–2.0 band** |

Everything is deterministic. There is no LLM in the scoring path.

## What makes the numbers defensible

- **Agent-dispatched work is excluded from builder metrics** and kept for reviewer metrics —
  reviewing an agent's PR is real review work. The excluded count is shown per person, because
  excluded-from-scoring is not did-not-happen.
- **Small samples are shrunk** toward the cohort mean (k=5), so a perfect record over 3 PRs
  does not beat a strong record over 40.
- **Ranks are withheld below a gate** (5 merged PRs / 10 reviews). Metrics are still shown.
- **Overlapping uncertainty intervals share a tie band.** The current data supports 3 bands,
  not a 1-to-50 ordering, and the UI says so.
- **Every intermediate is persisted** — raw → winsorized → shrunk → percentile → weight →
  contribution — so any rank can be explained arithmetically in the drill-down.
- **Evidence favours PRs that explain a BAD score**: the reverted change, the 4,000-line diff,
  the approval with no comment. Those are what people will challenge.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
gh auth login                    # or export GITHUB_TOKEN

top-engineers skim               # Phase 1: sweep, rank participation, pick the cohort
top-engineers hydrate            # Phase 2: deep-fetch only the cohort's PRs (resumable)
top-engineers score              # metrics -> scoring -> serving layer

uvicorn top_engineers.web.app:app --port 8080
```

See [docs/usage.md](docs/usage.md) for every command and [docs/architecture.md](docs/architecture.md)
for how it works and why.

## Status

| | |
|---|---|
| Window | 90 days ending 2026-09-16, UTC |
| Skimmed / hydrated | 5,945 / 5,136 PRs |
| Fetch cost | ~1,550 of 5,000 points — **one rate-limit window** |
| Autonomy | 62.3% agent-assisted · 23.2% fully autonomous · 14.5% undeclared |
| Tests | 111 |
| Committed raw | 19MB, byte-stable, rebuilds the database via `top-engineers restore` |

## Caveats

This is a **conversation starter, not a performance verdict**. The cohort is volume-selected
then judged on non-volume metrics; only fix/bug PRs are in scope; reverts within the last 14
days cannot be observed yet and leave the denominator entirely; percentiles over a 50-person
pool are coarse. All of this is surfaced in the UI, permanently, not buried in a footnote.

It cannot see design work, mentoring, incident response, or anything that never became a pull
request.
