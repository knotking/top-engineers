# Where the time went

**Build: ~65 minutes**, 11:11 → 12:16 on 2026-09-16 — a single unbroken session, from
`git init` on an empty directory to a scored leaderboard and a container serving it.
**Docs and deployment added ~50 minutes** on top, 12:16 → 14:40, ending with the service live on
Cloud Run.

Output: **3300 lines of source**, **1523 lines of tests** (112 passing), 5,945 PRs
downloaded, 19MB of byte-stable committed raw.

## Distribution

| Phase | Time | Share | |
|---|---:|---:|---|
| Planning & API verification | 10 min | 15% | `███████▌` |
| Fetch layer (checkpoint, client, limiter, pagination) | 8 min | 12% | `██████` |
| Metrics & scoring | 11 min | 17% | `████████▌` |
| UI, storage, serving layer | 7 min | 11% | `█████▌` |
| Tests (112, written alongside) | 9 min | 14% | `███████` |
| **Production download** | **13 min** | **20%** | `██████████` |
| Debugging two live bugs | 5 min | 8% | `████` |
| Docs, Docker, deploy scaffolding | 2 min | 3% | `█▌` |

The single largest block is **waiting on GitHub**, not writing code.

## Detailed timeline

| Time | What |
|---|---|
| 11:11 | Empty repo. `/plan` invoked with no arguments — asked for the requirement rather than guessing |
| 11:12–11:21 | **Planning.** Verified 12 brief assumptions against the live API before writing any code. Four failed |
| 11:21–11:27 | Scaffold, config with env overrides, **checkpointing first**, byte-stable raw IO |
| 11:27–11:36 | GraphQL client, shared token bucket, adaptive concurrency, date-slice pagination |
| 11:36–11:41 | Identity, autonomy parser, records, skim, hydrate, DuckDB schema |
| 11:41–11:47 | Metrics registry, six-step scoring, serving layer, FastAPI + template, Dockerfile |
| 11:47–11:49 | End-to-end validation on a 7-day window |
| 11:49–11:53 | **Skim: 5,945 PRs** |
| 11:53–12:02 | **Hydrate leg 1** — stalled at 4,000 by the concurrency-cascade bug |
| 12:02–12:05 | Bug fixed; **resumed from checkpoint**, zero refetch, remaining 1,136 PRs |
| 12:05–12:06 | Score → tie-band chaining bug found and fixed |
| 12:06–12:16 | `revert_rate` demoted, weights made self-normalising, docs |

## Why planning was 15%

Ten minutes of live API queries before writing code found four incorrect assumptions in the
brief. Each would have cost more than ten minutes to discover later:

| Assumption | Reality |
|---|---|
| Bot exclusion cuts 38% | **8.9%** — and the bot list was stale: `app/trunk-io` had 0 PRs, two live bots were missing |
| `checkSuites` gives CI state | All-null conclusions on this repo — would have silently nulled a scored metric |
| RTT ~2.3s | **4.4s** — latency is 85% of wall-clock, not 74% |
| Target ~7,329 PRs | **5,945** |

The `checkSuites` one is the clearest case: it produces no error, just a metric that is quietly
always `None`. Found in planning it cost minutes; found after deployment it would have
invalidated every published Builder score.

## The 13 minutes of downloading

| | |
|---|---|
| Skim | 3m 30s — 5,945 PRs |
| Hydrate | 9m 30s across two legs — 5,136 PRs |
| Score | 40s |
| Cost | ~1,550 of 5,000 points — **one rate-limit window** |
| Reliability | 0 backoffs, 0 data loss, **77 transient errors absorbed** |

GitHub was unusually unhealthy during the run — 55 of those 77 were 502/504s, plus 4 truncated
JSON-200 bodies. The retry layer absorbed all of them without operator intervention. The
projected 5–7 minutes assumed a healthy upstream; 13 was the price of a flaky one.

## The 5 minutes of debugging

Both bugs were in code the tests passed, and both were found by **looking at production
output** rather than by the suite.

**Concurrency cascade (~3 min).** A 403 burst was reported independently by every in-flight
worker, each halving: 8 → 4 → 2 → 1 in two seconds. Recovery needs 20 clean requests per step,
so the run crawled rather than failing — the worst kind of bug, because nothing errors. Fixed
with a 15s cooldown so one episode halves once.

**Tie-band chaining (~2 min).** All 50 people landed in one band. Tracking a running minimum of
`ci_low` chains transitively, and on a smooth distribution that always collapses to a single
band. Rank 1 `[0.637,0.766]` and rank 50 `[0.051,0.368]` shared a band despite no overlap.
Fixed by anchoring on the band leader: 3 real bands.

Both now have regression tests.

## The deployment tail (~50 min)

Documentation and deployment took roughly as long as they usually do, and for an unusual reason:
**three configuration files disagreed about which artifact ships.**

| | |
|---|---|
| Docs (6 files, ~15k words) | ~20 min |
| Deploy debugging | ~20 min |
| Builds and uploads (waiting) | ~10 min |

Four failures, in order: Cloud Build's default service account lacked `storage.objects.get`;
a direct 358MB image push was too slow and was abandoned for a 19MB source build;
`gcloud builds submit` fell back to `.gitignore` and stripped the database out of the upload;
and `/healthz` turned out to be intercepted by Google's frontend, making the empty-leaderboard
guard unreachable at the path it was published on.

The third is the one worth remembering. The local Docker smoke test passed while the deploy was
broken, because `.dockerignore` and `.gitignore` make opposite decisions about `data/*.duckdb`.
A build that verifies a different artifact than it deploys is not a verification — so
`deploy.sh` now checks the deployed URL after rollout.

## What was skipped, and what it saved

- **git-clone revert detection** — timeboxed at 10 minutes and skipped. Vindicated: only 23
  revert-titled PRs exist in the corpus, so the metric was demoted to context-only anyway.
- **`weekend_activity_share`** — dropped on principle. Surfacing weekend work in a ranking tool
  invites exactly the misuse the caveats panel warns against.
- **Bootstrap confidence intervals** — 33 metrics × 50 people × 1,000 resamples is not viable.
  Analytic propagation answers the same question.

## The ratio worth noting

**46% of the time went to correctness infrastructure** — checkpointing, byte-stability, the
retry taxonomy, identity review, tests — against 28% on the features a demo would show.

That ratio paid for itself inside the same session. The concurrency bug killed a run at 78%
completion, and resume made recovery a 15-second restart with zero refetch instead of a 9-minute
re-download. Checkpointing was built **before** the first long fetch, which is the only reason
it was there when the first long fetch broke.
