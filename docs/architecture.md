# Architecture

```
GitHub GraphQL ──▶ fetcher ──▶ data/raw/*.jsonl.gz   (byte-stable, committed)
                      │              │
                      ├─ checkpoint/*.jsonl          (append-only scratch, gitignored)
                      ▼              ▼
                 DuckDB:  raw ──▶ derived ──▶ serving
                                                │
                                           FastAPI (zero compute) ──▶ UI ──▶ Cloud Run
```

Every design decision below exists because the naive alternative was measured and failed.

## Two phases, because cohort size is not the speed lever

**Phase 1 (skim)** sweeps cheaply and ranks participation. **Phase 2 (hydrate)** deep-fetches
only PRs the cohort touched.

The skim carries only what changes *who* gets fetched next — `reviews(first:10){totalCount}`,
not `first:50`. Anything that doesn't influence that decision belongs in hydration. The lighter
payload allows page size 100, roughly halving sweep requests.

Narrowing the cohort buys less than it looks like it should: top-10 needs 3,531 PRs hydrated,
top-50 needs 5,136. The top few *are* the volume, and review fan-out spreads across everyone
regardless. Concurrency is the lever, not cohort size — so we normalise percentiles against a
50-person pool and display only the head of it.

## The three rate limits

Three distinct limits, three different responses. Conflating them wastes hours.

| Limit | Signal | Response |
|---|---|---|
| Primary points (5,000/hr) | `rateLimit.remaining` | sleep until **`resetAt`** |
| Secondary / burst | HTTP 403, no budget signal | **proactive pacing** + halve concurrency |
| Per-query node ceiling (~500k) | query error | cap page sizes |

A flat 60s sleep on primary exhaustion wakes into the same empty budget and burns the
remainder of the window into hard `RATE_LIMITED` errors. Secondary limits are *not* cured by
waiting, so they are answered by backing off concurrency instead.

The reported budget is not a reliable predictor — `gh api rate_limit` disagreed with observed
consumption three separate times — so the limiter instruments its own counter from
`rateLimit.cost`.

## Every `.json()` is guarded

GitHub serves HTML error pages with **HTTP 200**, and truncated JSON bodies with HTTP 200. Both
occurred during the production run (4 truncated bodies). One unguarded `.json()` is enough to
destroy a long run at 65% completion.

Retried as transient: 5xx, connection errors, non-JSON 200, and `RATE_LIMITED` *inside* a 200
body. Not retried: malformed queries — retrying those just burns the window.

## Latency, not budget, is the constraint

Measured GraphQL RTT is **4.4s** against 0.8s pacing, so latency is ~85% of wall-clock.

Concurrency converts a latency problem into a budget problem, which is why page sizes are
tuned for the **median, not the max** (`files(30) commits(30) reviews(20) reviewThreads(20)
comments(30)`): **0.32 points/PR against 1.56 untuned**. That is what keeps the whole job
inside a single 5,000-point window instead of spanning three with two 45-minute refill waits.

Two measurements that changed the design:

- **`checkSuites` is empty on this repo.** It returns only stale QUEUED suites with null
  conclusions, silently nulling out a scored metric. `statusCheckRollup` reports real state —
  but requesting it across all 30 commits costs **3.22s/batch versus 0.88s for the first commit
  alone**, at identical point cost. Since `ci_first_pass_rate` is defined on the first pushed
  commit anyway, the rollup is requested on exactly two commits (first and head).
- **Comments were the dominant overflow source** — 12 of 14 drains. Raising that page from 10
  to 30 cut total hydration time ~3.8×.

### Shared pacing, adaptive concurrency

Pacing is **one shared token bucket**. N workers sleeping independently multiply the request
rate by N and walk straight back into the secondary limit. The sleep happens *outside* the
lock — holding it while sleeping serialises every worker and silently undoes the concurrency.

Concurrency starts at 3, +1 per clean window, **halves on a 403**, caps at 8. One throttling
*episode* halves **once**: without a cooldown, every worker in flight when a burst lands reports
it independently and the limit collapses geometrically (observed: 8 → 4 → 2 → 1 in two seconds,
after which recovery is glacial).

## Checkpointing was built before the first long fetch

A fetcher without resume is a prototype. The ordering is load-bearing:

> **Raw JSON is written first, then the database.**

Append-only cannot half-fail. Reverse the order and you get store rows with no raw record
behind them — corruption invisible for weeks.

Buffers flush every 250 records. `__exit__` catches **`BaseException`**, not `Exception`,
because Ctrl-C raises `KeyboardInterrupt` and that is precisely the interrupt that matters.
Resume dedupes on PR number and tolerates a torn final line from `kill -9` as normal, not fatal.

A failed batch is **re-queued**, and if it still will not land its PR numbers are reported.
Giving up is acceptable; giving up silently is not.

## Byte-stability and crash-safety need two files

They are irreconcilable in one. Byte-stability needs everything sorted (write at the end);
crash-safety needs unsorted append (write as you go). So:

- `checkpoint/*.jsonl` — append-only scratch, gitignored, deleted **only after** the canonical
  files exist.
- `raw/*.jsonl.gz` — sorted, deterministic, committed.

Determinism needs three things, and `mtime` is the one people miss because gzip embeds the
current time in its header by default:

```python
json.dumps(sort_keys=True, separators=(",", ":"))   # stable field order
records sorted by PR number                          # stable record order
gzip.GzipFile(mtime=0)                               # stable header
```

`top-engineers verify` re-encodes what is on disk and compares sha256. A pass means an
unchanged upstream produces zero git diff.

`top-engineers restore` rebuilds the database from that raw, sharing `pipeline/records.py`
with the live fetch so the two cannot drift.

## Identity: 40% of the data rides on this

`actors.yaml` is a **human-reviewed artifact**, not a helper function.

The `[bot]` suffix and GitHub's `Bot` account type miss the real cases — `stamphog` is a *User*
account posting automated approvals detailed enough to top every reviewer-quality metric.
Substring nets ("bot", "ci", "hog") produced **10 flags and 10 false positives**, all real
employees. So `is_bot()` consults only the reviewed file plus hard structural signals, and
candidate generation writes a *separate* file for a human to promote.

### Autonomy: structured self-declaration beats every heuristic

PostHog's PR template carries `**Autonomy:** Fully autonomous | Human-driven (agent-assisted)`,
at **85% coverage**. Measured: 23.2% fully autonomous, 62.3% agent-assisted.

The trap: an author who never edits the template leaves **both** options in the body. A
first-match parser reads that as `human_driven`. So "both present" is tested *before* either
option individually, and unedited templates resolve to `unknown`.

Fully-autonomous PRs are **excluded from builder metrics** (dispatched, not authored) and
**kept for reviewer metrics** (reviewing an agent's PR is real work). The excluded count is
reported per person.

## Storage: three layers

**raw** (as fetched) → **derived** (computed) → **serving** (denormalised).

The serving layer lets the UI do **zero computation** — that is what makes sub-10s structural
rather than something to tune. Measured: 18ms local, 130ms cold in-container.

Two DuckDB specifics: inserts are **named-column only** (positional breaks silently the moment
a migration widens a table), and since there is no `ADD COLUMN IF NOT EXISTS`, each migration
is attempted inside its own transaction with the duplicate-column error swallowed — **with an
explicit ROLLBACK**, because DuckDB aborts the surrounding transaction on a failed statement
and the *next* migration would otherwise fail with an opaque `TransactionException`.

## Scoring: six steps, every intermediate persisted

1. **Gate** — 5 merged PRs for a builder rank, 10 reviews for a reviewer rank. Below the gate,
   metrics are computed and shown; only the *rank* is withheld.
2. **Winsorize** at p5/p95 within the eligible pool.
3. **Shrink** rate metrics toward the cohort mean, `(x·n + mean·k)/(n+k)`, k=5. Without this a
   perfect record over 3 PRs wins. Non-negotiable.
4. **Percentile** within the eligible pool; sign-flip lower-is-better; band-score the ratio.
   Percentile over z-score — these distributions are heavily skewed.
5. **Weight** — declared as within-pillar *shares*, absolutes derived, so demoting a metric
   rebalances its pillar exactly. Missing metrics **renormalise**; they never count as zero.
6. **Uncertainty** — analytic propagation from each metric's own denominator. A true bootstrap
   is 33 metrics × N people × 1,000 resamples, which is not viable and answers the same
   question.

**Tie bands** anchor on the band *leader's* lower bound. Tracking a running minimum chains
transitively (A~B, B~C ⇒ one band) and collapses any smooth distribution into a single
meaningless band — observed on real data, where rank 1 `[0.637,0.766]` and rank 50
`[0.051,0.368]` shared a band despite no overlap.

### Metric selection

In order: **has data on the window**; **separates people rather than clustering them**;
**measures how work landed, not how much**.

`revert_rate` was demoted to context-only by that second criterion — 23 revert-titled PRs in
the corpus and 1 of 50 people nonzero, so it added a constant to every score while consuming
6.45% of the weight. `change_value_mix` is near-useless on a fix-only cohort by construction
and is retained purely so its zero variance is visible.

## Deployment

The DuckDB file is **baked into the image** — data is static per run, so there is no runtime
fetch and no cold-start download.

`TP_DATA_DIR` / `TP_DB_PATH` are set in the Dockerfile and are **not optional**. Deriving the
path from `__file__.parents[2]` resolves to `/usr/local/lib/python3.12` once pip-installed —
verified in-container — which contains no data directory. The container then starts fine and
serves an empty leaderboard forever. The health endpoint returns 503 on an empty leaderboard so
that failure is loud — served on **`/_health`**, because Google's frontend intercepts `/healthz`
on Cloud Run and answers it with its own 404 before the container ever sees the request, which
made the guard unreachable in exactly the environment it was written for.

The same class of bug bit twice more. `gcloud builds submit` falls back to `.gitignore` when no
`.gcloudignore` exists, and `.gitignore` excludes `data/*.duckdb`, so the first deploy shipped
without a database. And the local Docker smoke test passed while that deploy was broken, because
`.dockerignore` and `.gitignore` disagree about what ships.

The conclusion generalizes: **a build that verifies a different artifact than it deploys is not
a verification.** `deploy.sh` still builds and smoke-tests locally first, because that catches
path bugs for free, but it now also fails fast if `.gcloudignore` is missing and verifies the
*deployed* URL after rollout.

The database is committed **gzipped** — 19MB against 111MB raw, which exceeds GitHub's 100MB
hard limit — with `mtime=0` for the same determinism as the raw files. The Dockerfile
decompresses it at build time and asserts the leaderboard is non-empty.
