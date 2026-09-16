# top-engineers — Implementation Plan

**Requirement:** Rank the top 10 engineers in `PostHog/posthog` over a 90-day window on *how* their
fix/bug work landed (not how much), from GitHub GraphQL data stored in DuckDB, served as a
sub-10-second drill-down UI on Cloud Run.

- **Window:** 2026-06-18 → 2026-09-16 (90 days, UTC throughout)
- **Status:** PLAN — no implementation code written yet
- **Baseline verified:** 2026-09-16 against the live API (see §1)

---

## 1. Verified baseline — measured, not assumed

Every number below was measured against the live API during planning. **Four brief assumptions
did not survive contact** and the plan is built on the measured values, not the briefed ones.

| Claim | Brief said | Measured today | Verdict |
|---|---|---|---|
| Merged PRs in window | 11,929 | **15,119** | Repo busier than last run |
| Bot exclusion effect | 11,929 → 7,329 (−38%) | 15,119 → 13,768 (**−8.9%**) | ⚠️ **REVISED** |
| `-author:app/trunk-io` | key exclusion | **0 PRs** | ⚠️ **DEAD — delete it** |
| `in:title bugfix` | 0 | **0** | ✅ confirmed dead |
| `in:title hotfix` | 0 | **0** | ✅ confirmed dead |
| `in:title fix` | keep | **5,915** (−61%) | ✅ the real volume lever |
| `in:title revert` | keep | **40** | ✅ keep (revert detection) |
| `label:bug` | 35, useless | **25** | ✅ confirmed useless |
| Autonomy template | ~76% declared | **86% of bodies** | ✅ confirmed, stronger |
| Points per PR (tuned pages) | 0.58 | **0.560** | ✅ confirmed |
| Median files/PR | 2 | **2.0** | ✅ confirmed |
| GraphQL RTT | 2.33s | **4.4s** | ⚠️ **more latency-bound** |

### 1.1 The four revisions that change the plan

**(a) Bot exclusion is worth 8.9%, not 38%.** The brief's headline "−38% of everything
downstream, compounding" does not hold on this window. The real bot authors today are:

| Author | PRs in window | Note |
|---|---|---|
| `app/posthog` | 1,078 | the big one |
| `app/posthog-js-upgrader` | 165 | **missing from the brief's list** |
| `app/scheduled-actions-posthog` | 108 | **missing from the brief's list** |
| `app/trunk-io` | **0** | **dead — delete, per the brief's own rule** |

Bot accounts require the `app/` prefix in search: `author:posthog-js-upgrader` returns **0**
while `author:app/posthog-js-upgrader` returns **165**. Getting this wrong silently disables the
exclusion. Keep the exclusion (it is free), but **do not budget for it as the primary speed lever** —
`in:title fix` is, at −61%.

**(b) The real cohort is ~5,955 PRs, not 7,329.** `fix` (5,915) + `revert` (40), pre-dedup. Phase 2
hydrates only the subset the top 10 touched; the brief's 3,531 estimate is plausible but is an
*output of Phase 1*, not an input. Do not hard-code it.

**(c) Latency dominates even harder than briefed.** Measured RTT 4.31–4.58s (mean 4.42s) on a
hydration-shaped 25-alias query, against 0.8s pacing. **Latency is ~85% of wall-clock.** Concurrency
is unambiguously the top speed lever; pacing is cheap insurance, not the bottleneck.

**(d) Budget is not the binding constraint at this cohort size.** 5,955 PRs × 0.560 pts = **3,335 of
5,000 points — 0.67 of a single hourly window.** The brief's multi-window refill-wait nightmare does
not arise *because* page sizes are already tuned. Untuned (`files(100) commits(100) …`) at the
briefed 1.56 pts/PR = 9,290 points = **2 windows and a 45-minute wait.** The tuning is what keeps
this a one-window job — implement it from the start, not as an optimisation pass.

### 1.2 Projected wall-clock (238 hydration requests @ 25 PRs/batch, 4.4s RTT)

| Mode | Wall-clock |
|---|---|
| Sequential | ~17.5 min |
| Concurrency 3 | ~5.8 min |
| Concurrency 5 | ~3.5 min |
| Concurrency 8 | ~2.2 min |

### 1.3 Two traps found while verifying

**Unedited autonomy templates.** Body text sampling turned up
`'Human-driven (agent-assisted) - or - Fully au…'` — both template options left in, i.e. the author
never filled it out. A naive first-match parser classifies this as `human_driven`. It **must** map to
`unknown`. Detect "both options present" explicitly before matching either.

**`in:title fix` is already precise here.** 100/100 sampled titles matched
`^(fix|bugfix|hotfix|revert)(\(scope\))?!?:` — PostHog enforces conventional-commit titles, so there
is no "prefix"/"fixture" over-match to clean up. Keep the client-side regex anyway, but as a *logged
assertion* (alert if precision drops below ~95%), not as a filter that silently discards rows.

**Nested-page overflow is real but rare.** 1 of 25 sampled PRs overflowed `reviews(20)`; files,
commits, reviewThreads, comments had zero overflow. Overflow drain is required for correctness
(silent truncation is biased toward the busiest, most-reviewed PRs — exactly the signal the reviewer
metrics depend on) but costs ~4% extra requests.

---

## 2. Architecture

```
GitHub GraphQL ──▶ fetcher ──▶ raw/*.jsonl.gz (byte-stable, committed)
                      │              │
                      ├─ checkpoint/*.jsonl (append-only scratch, gitignored)
                      ▼              ▼
                 DuckDB: raw ──▶ derived ──▶ serving
                                               │
                                          FastAPI (zero compute) ──▶ UI ──▶ Cloud Run
```

**Two-file invariant.** Byte-stability (sorted, written at end) and crash-safety (unsorted, appended
as you go) are irreconcilable in one file. Scratch checkpoint = append-only, tolerant of torn lines.
Canonical `.jsonl.gz` = sorted, deterministic, committed. Checkpoint deleted only after canonical
files are written and hash-verified.

**Serving layer does zero computation.** `<10s` is structural, not tuned.

### 2.1 Repo layout

```
top-engineers/
├─ docs/plan/readme.md              ← this file
├─ pyproject.toml                   (uv; py3.12)
├─ Dockerfile
├─ actors.yaml                      ← HUMAN-REVIEWED identity artifact
├─ data/
│  ├─ raw/{prs,reviews,cohort}.jsonl.gz   (committed, byte-stable)
│  └─ top_engineers.duckdb                (gitignored; baked into image)
├─ src/top_engineers/
│  ├─ config.py          TP_DATA_DIR / TP_DB_PATH env overrides
│  ├─ gh/
│  │  ├─ client.py       guarded .json(), retry, error taxonomy
│  │  ├─ limiter.py      shared token bucket, adaptive concurrency
│  │  ├─ queries.py      skim + hydrate GraphQL, shared fragment
│  │  ├─ paginate.py     date-slice recursion, overflow drain
│  │  └─ checkpoint.py   append-only writer, resume, BaseException flush
│  ├─ pipeline/
│  │  ├─ skim.py         Phase 1 → cohort
│  │  ├─ hydrate.py      Phase 2 → full PRs
│  │  ├─ autonomy.py     template parser
│  │  └─ reverts.py      blobless clone (OPTIONAL, see §5.5)
│  ├─ store/
│  │  ├─ schema.sql      raw / derived / serving DDL
│  │  ├─ load.py         named-column inserts, idempotent migrations
│  │  └─ load_from_raw.py  ← restore path (the gap never closed last time)
│  ├─ metrics/           33 computed, 10 scored
│  ├─ scoring/           gate→winsor→shrink→percentile→weight→uncertainty
│  └─ web/               FastAPI + templates
└─ tests/
```

---

## 3. Data model

**raw** (as fetched, no interpretation): `raw_pr`, `raw_review`, `raw_review_thread`,
`raw_commit`, `raw_file`, `raw_comment`.
`raw_pr.updated_at` is stored **from day one** — retrofitting it means a full re-fetch, and it is the
key to the 10–50× incremental re-run (§5.6).

**derived** (computed, one row per fact): `pr_autonomy`, `pr_ci_first_pass`, `pr_review_rounds`,
`pr_risk_weight`, `pr_test_habit`, `review_quality`, `person_metric` (long: person × metric × raw →
winsorized → shrunk → percentile → weight → contribution).

**serving** (denormalised, UI reads only these): `serving_leaderboard`, `serving_person_detail`,
`serving_metric_chain`, `serving_evidence`, `serving_caveats`.

Rules: **named-column inserts only** (positional breaks silently when a migration widens a table);
DuckDB has no `ADD COLUMN IF NOT EXISTS` — attempt each migration, swallow the duplicate-column
error for idempotency; DuckDB is **single-writer**, so stop the server before rebuilding (moot on
Cloud Run — immutable image).

---

## 4. Identity — `actors.yaml`

Gets 40% of attribution wrong if rushed. `actors.yaml` is a **human-reviewed artifact**, not a
generated file.

- `[bot]` suffix and GitHub `Bot` account type **miss the real cases** — `stamphog` is a `User`
  account posting automated approvals detailed enough to top every reviewer-quality metric.
- Substring nets (`bot`, `ci`, `hog`) produced **10 flags, 10 false positives** (real employees).
  Use **only** to generate a review queue, **never** as a classifier.
- Tooling emits `actors.candidates.yaml` (suggestions + evidence links). A human promotes entries
  into `actors.yaml`. The pipeline reads only `actors.yaml`.
- Measured in sample: 4.4% of PRs are `Bot`-typename authored; `app/`-prefixed bots are 8.9% of the
  window. Neither captures `stamphog`-class accounts — that is what human review is for.

### 4.1 Autonomy (the signal no heuristic matched)

PostHog's PR template carries `**Autonomy:** Fully autonomous | Human-driven (agent-assisted)`.
Measured **86% body coverage**. Parse into `pr.autonomy ∈ {fully_autonomous, human_driven, unknown}`.

Parser order (**order matters**):
1. Both options present verbatim → `unknown` (unedited template — see §1.3).
2. Exactly one option present → that value.
3. Free-text variants (`"Human-directed agent work."`) → nearest match, else `unknown`.
4. No `Autonomy` key → `unknown`.

- **EXCLUDE `fully_autonomous` from BUILDER metrics** — dispatched, not authored.
- **KEEP it for REVIEWER metrics** — reviewing an agent's PR is real review work.
- **Report `dispatched_n` per person.** Excluded-from-scoring is not did-not-happen; show it.
  One account measured 91% autonomous — a real engineer running an agent fleet at 15 PRs/day, which
  is exactly why they led every volume metric. That is the finding, not an artifact to hide.

---

## 5. Implementation steps

### 5.1 Step 0 — Scaffold
`pyproject.toml` (uv, py3.12; `httpx`, `duckdb`, `fastapi`, `uvicorn`, `pyyaml`, `jinja2`, `pytest`),
`config.py` with **`TP_DATA_DIR` / `TP_DB_PATH` env overrides**. Never derive `DB_PATH` from
`__file__.parents[2]` — it resolves to site-packages' grandparent once pip-installed, and the
container then starts fine and serves an **empty leaderboard forever**.

### 5.2 Step 1 — Checkpointing FIRST (before any long fetch)
A fetcher without resume is a prototype. The previous build listed resume as an acceptance test,
shipped without it, and lost 5,000 PRs and ~90 minutes at 65%.
- Flush every **250** records; never hold a run in memory.
- **Write raw JSON first, THEN the database.** Append-only cannot half-fail; the reverse order
  yields store rows with no raw record behind them — corruption invisible for weeks.
- Catch **`BaseException`** (not `Exception`) and flush before re-raising — covers Ctrl-C.
- Resume = read checkpoint, dedupe on PR number, skip existing. Tolerate torn final lines from an
  abrupt kill (`json.JSONDecodeError → continue`); never fatal.
- **Acceptance test: `kill -9` mid-fetch, resume, assert zero duplicates and zero loss.** This test
  is the deliverable, not the checkbox.

### 5.3 Step 2 — Client + limiter
Three **distinct** rate limits, three different responses — conflating them wastes hours:

| Limit | Signal | Response |
|---|---|---|
| Primary points (5,000/hr) | `rateLimit.remaining` | sleep until **`resetAt`**, never a fixed interval — a flat 60s sleep wakes into the same empty budget and burns the remainder into hard `RATE_LIMITED` errors |
| Secondary/burst | HTTP 403, no budget signal | **proactive pacing**; not cured by waiting |
| Per-query node ceiling (~500k) | query error | cap page sizes (measured 9,000 nodes/batch — ample headroom) |

- **Guard every `.json()`.** GitHub serves HTML error pages with **HTTP 200**. One unguarded call
  destroyed a 90-minute run at 65%.
- Retry as transient: 5xx, connection errors, non-JSON 200, `RATE_LIMITED` **inside a 200 body**.
- Pace at **≥0.8s** shared interval. Instrument an **own counter from `rateLimit.cost`** —
  `gh api rate_limit` disagreed with observed consumption three separate times.
- **One shared token bucket** across workers. N workers sleeping independently multiply the request
  rate by N and walk straight back into the secondary limit.
- **Sleep OUTSIDE the lock.** Holding it while sleeping serialises every worker and silently undoes
  the concurrency.
- Adaptive: start **3**, `+1` on a clean window, **halve on first 403**, cap **8**.

### 5.4 Step 3 — Skim → top 10 cohort
Query: `repo:PostHog/posthog is:pr is:merged merged:<window>` + `in:title fix` / `in:title revert`,
minus the three live bot authors (§1.1a). Drop `bugfix`, `hotfix`, `app/trunk-io` — all measured 0.
Instrument marginal yield per variant and delete what earns nothing.

- **Search caps at 1,000 results SILENTLY** — no error, no warning. Slice by date, read `issueCount`,
  split recursively above the cap. Self-correcting; no need to guess slice width. **Log any single
  day still over the cap** — that is real data loss the operator must know about.
- Slim payload: `reviews(first:10){totalCount}`, not `first:50`. Ask of every discovery field:
  *does this change WHO I fetch next?* If not, it belongs in hydration. Lighter payload allows
  `first:100` and roughly halves sweep requests.
- Rank by participation (authored + reviewed) → **normalise against top 50–100, DISPLAY 10** (§7).
- Keep the "% bot-authored" stat from **one** count query (`per_page=1`, `total_count`). Never
  download rows you intend to discard.

### 5.5 Step 4 — Hydrate
One GraphQL **alias per PR** (`p0:`, `p1:` …) against a shared fragment, **25 per request** — there
is no bulk `pullRequest(numbers: [...])` field.

Page sizes tuned for the **median, not the max** (validated §1): `files(30) commits(30) reviews(20)
reviewThreads(20) comments(10)` → **0.560 pts/PR**. This is what keeps the job inside one rate-limit
window (§1.1d).

**Always drain overflow when `hasNextPage`** (~4% of PRs, reviews only). Verify child-counts-per-PR
are unchanged after any tuning — silent truncation is biased toward the busiest, most-reviewed PRs.

### 5.6 Step 5 — Autonomy backfill
Parse bodies per §4.1; backfill `pr.autonomy`. Assert coverage ≥80% (measured 86%); alert if the
template changes upstream.

### 5.7 Step 6 — Reverts (OPTIONAL, timeboxed)
Blobless shallow clone (`--filter=blob:none --shallow-since`) for revert detection.
**`revert_rate` carries 6.45% weight and renormalises away cleanly if absent.** Clone time is
untested. **Timebox to 10 minutes; on overrun, skip and let renormalisation absorb it.**

### 5.8 Step 7 — Metrics → evidence → score
### 5.9 Step 8 — UI
### 5.10 Step 9 — Deploy

---

## 6. Metrics — 10 scored, all deterministic, no LLM spend

Selection criteria, in order: has data on the window; **separates** people rather than clustering
them; measures **HOW** work landed, not **HOW MUCH**.

| Pillar | Weight | Metrics |
|---|---|---|
| BUILDER | 45% | `ci_first_pass_rate`(↑ 7.53%) `review_rounds_per_pr`(↓ 7.53%) `risk_weighted_contribution`(↑ 13.66%) `test_habit`(↑ 16.28%) — `revert_rate` demoted to context |
| REVIEWER | 40% | `rubber_stamp_rate`(↓) `comment_acceptance_rate`(↑) `review_latency_hours`(↓) `author_breadth`(↑) |
| CROSS-CUTTING | 15% | `review_to_authoring_ratio`(~ band 0.5–2.0) |

**NO volume metrics** (PR count, cycle time, LOC) — they reward agent throughput, and §4.1 shows
exactly who that flatters.

Definitional traps:
- `ci_first_pass_rate` = **FIRST pushed commit, not head.** A PR that went red and was fixed up did
  not pass first time.
- `revert_rate` / `approval_revert_rate`: PRs merged in the **final 14 days leave the DENOMINATOR
  entirely** — they cannot yet be observed for a full revert window.
- `review_to_authoring_ratio` is **NOT monotonic.** Score distance from a healthy band; scoring it
  higher-is-better ranks someone who only reviews above someone who does both.
- `change_value_mix` is near-useless on a fix-only cohort — no variance by construction. Context-only.

Compute all **33**; **score 10**; show the rest as zero-weight context.

**Watch effective weights.** Equal-split-within-sub-pillar means a lone survivor inherits the whole
sub-pillar share: `risk_weighted_contribution` lands at **11.7%**, the outcome-quality trio at
**6.45%** each. Weight explicitly if that concentration is wrong — decide deliberately, don't inherit
it by accident.

---

## 7. Scoring — six steps, every intermediate persisted

1. **Gate:** ≥5 merged PRs for a builder rank, ≥10 reviews for a reviewer rank. Below the gate,
   **compute and show metrics but withhold the rank.**
2. **Winsorize** at p5/p95 within cohort.
3. **Shrink** rate metrics toward the cohort mean, prior `k=5`: `(x*n + mean*k)/(n+k)`.
   Without this a perfect record over 3 PRs wins. **Non-negotiable.**
4. **Normalise** to percentile within the **eligible** pool; sign-flip lower-is-better; band-score
   the ratio. Percentile over z-score — these distributions are heavily skewed.
5. **Weight:** equal within sub-pillar → sub-pillar → pillar (45/40/15). Missing metrics
   **renormalise over what is present**; never count as zero.
6. **Uncertainty:** standard error per metric from its own denominator, combined through the same
   weights. Overlapping intervals share a **TIE BAND** — do not fake a 1-2-3-4-5 ordering the data
   cannot support. (A true bootstrap is 33 metrics × N people × 1,000 resamples — not viable;
   analytic propagation answers the same question.)

Persist **raw → winsorized → shrunk → percentile → weight → contribution** for every metric, so
drill-down explains a rank arithmetically.

**Small-pool decision (recommended):** normalise percentiles against the **top 50–100** pool and
**display only 10**. Percentiles over 10 people are coarse buckets and the shrinkage prior gets
noisy. If we instead normalise within the 10, the UI must **state plainly** that ranks are
within-a-10-person-pool. Note this costs more hydration — top-50 is 6,544 PRs vs top-10's ~3,531 —
but narrowing 100→20 saves only 38%, because **the top few ARE the volume** and review fan-out
spreads regardless. **Cohort size is not the speed lever; concurrency is.**

---

## 8. UI

- **Top 5 as avatar cards** — `https://github.com/{login}.png?size=160`, no API call or id lookup.
- Then the ranked list.
- **Click → drawer:** full arithmetic chain per metric, evidence PR links, and an **`ⓘ how`** toggle
  revealing a plain-English computation note **including the exclusions**. A number whose denominator
  is a mystery cannot be argued with.
- **Evidence favours PRs explaining a BAD score** — the reverted change, the 3-week PR, the
  4,000-line diff, the approval with no comment. Those are what people will challenge.
- **Hide metrics with no value.** Split **Scored** vs **Context-only**.
- **Caveats panel, always visible:** volume-selected cohort; fix/bug subset only; 14-day revert
  censoring; autonomy exclusion (with `dispatched_n`); small-pool percentiles; and that this is a
  **conversation starter, not a performance verdict.**

---

## 9. Deploy

- **Bake the DuckDB file into the image** — data is static per run; no runtime fetch, no cold-start
  download.
- `TP_DATA_DIR` / `TP_DB_PATH` set in the Dockerfile (§5.1).
- **Build locally with docker before `gcloud builds submit`** — catches path bugs for free.
- Cloud Run, project **`code-impact-dev`** (already the active gcloud project), `--min-instances 1`.

---

## 10. Tests

| Area | Test |
|---|---|
| Checkpoint | `kill -9` mid-fetch → resume → **zero duplicates, zero loss** |
| Checkpoint | torn final line → parsed as skip, not fatal |
| Byte-stability | re-encode → **sha256 round-trip matches file on disk** (unchanged upstream ⇒ zero git diff) |
| Byte-stability | `gzip.GzipFile(mtime=0)` + `sort_keys=True, separators=(",",":")` + records sorted by PR number |
| Restore | **`load-from-raw` rebuilds the DB from committed raw** — otherwise raw is an audit trail, not a restore path (the gap never closed last time) |
| Client | HTML-with-HTTP-200 → retried, not crashed |
| Client | `RATE_LIMITED` inside a 200 body → treated as transient |
| Limiter | N workers → observed rate ≤ 1/0.8s (shared bucket, not per-worker) |
| Limiter | sleeping worker does not block others (sleep outside lock) |
| Pagination | >1,000-result slice splits recursively; single day over cap **logs loudly** |
| Pagination | `hasNextPage` drains; child-counts-per-PR unchanged after tuning |
| Autonomy | unedited both-options template → **`unknown`** |
| Scoring | missing metric renormalises, never counts as zero |
| Scoring | 3-PR perfect record does **not** outrank a 40-PR strong record (shrinkage) |
| Scoring | final-14-day merges absent from revert denominator |
| Scoring | overlapping intervals → shared tie band |
| Store | positional insert rejected; named-column required |
| Store | migration re-run is idempotent (duplicate-column swallowed) |

---

## 11. Risks & decisions

| # | Risk | Decision |
|---|---|---|
| 1 | Bot list is stale (measured: brief's list missed 2 live bots, included 1 dead one) | Regenerate the bot list **per run** from `Bot`-typename sampling; treat `actors.yaml` as human-reviewed, never generated |
| 2 | Cohort is volume-selected, then judged on non-volume metrics | Stated caveat; unavoidable given a ranking brief |
| 3 | Fix-only subset | `change_value_mix` demoted to context-only; caveat surfaced |
| 4 | Small-pool percentiles | Normalise on top 50–100, display 10 (§7) |
| 5 | Revert clone time unknown | Timebox 10 min, skip on overrun, renormalise (§5.7) |
| 6 | Upstream template change breaks autonomy | Assert ≥80% coverage; alert on drop |
| 7 | Incremental re-runs | Store `updated_at` **now**; `updated:>=<last_run>` + skip unchanged = 10–50×, dwarfing everything else. Retrofitting = full re-fetch |

**Explicitly NOT worth doing** (per brief, and consistent with the latency measurement): compressing
requests, micro-tuning JSON parsing, multiple tokens (against ToS, and we're latency-bound anyway),
concurrency past ~8, optimising the DB load (milliseconds against seconds of network).

---

## 12. Order of work

- [x] 1. Scaffold + `config.py` env overrides (§5.1)
- [x] 2. **Checkpointing + resume — BEFORE any long fetch** (§5.2) — `kill -9` resume test passes
- [x] 3. Client + shared-bucket limiter (§5.3) — caught a real non-JSON 200 and 502/504s in the wild
- [x] 4. Skim → cohort, normalise-pool 50 (§5.4) — 5,945 PRs, 5,136 hydration targets
- [x] 5. Hydrate with concurrency (§5.5) — 0.32 pts/PR, 0 backoffs
- [x] 6. Autonomy backfill (§5.6) — 82–86% coverage, unedited templates → `unknown`
- [ ] 7. Reverts — optional, timeboxed (§5.7) — **NOT BUILT**; API-only title/body detection
      used instead. The blobless clone was skipped, `revert_rate` renormalises per §7 step 5.
- [x] 8. Metrics → evidence → score (§6, §7) — 33 computed, 10 scored
- [x] 9. UI (§8)
- [x] 10. Deploy scaffolding (§9) — Dockerfile + `deploy.sh`; **not deployed** (needs operator)

### Final run — measured 2026-09-16

| | |
|---|---|
| Skim | 5,945 PRs (5,923 `fix` + 22 net-new `revert`) |
| Hydrated | 5,136 PRs · 0.319 pts/PR · **~1,550 of 5,000 points, one window** |
| Backoffs | 0 · zero data loss · 77 transients all absorbed |
| Autonomy | 62.3% agent-assisted, **23.2% fully autonomous**, 14.5% unknown (85% coverage) |
| Committed raw | 19MB, byte-stable, `load-from-raw` rebuilds the DB |
| Tie bands | 3 (ranks 1–21, 22–42, 43–50) |
| Scored metrics | **9**, not 10 — `revert_rate` demoted (see below) |

Two bugs found by running it, both fixed with regression tests:

1. **Concurrency cascade.** A 403 burst was reported independently by every in-flight worker,
   each halving: 8 → 4 → 2 → 1 in two seconds, after which recovery was glacial. One episode
   must halve once; now debounced by a 15s cooldown (> worst-case RTT).
2. **Tie bands chained transitively.** Tracking a running minimum of `ci_low` meant A~B and
   B~C put A and C in one band. On a smooth distribution that collapses everyone into a
   single band — rank 1 `[0.637,0.766]` and rank 50 `[0.051,0.368]` shared a band despite no
   overlap. Now anchored on the band leader: 3 real bands.

### Deviations from the plan, and why

| # | Plan said | Built | Why |
|---|---|---|---|
| 1 | `checkSuites(first:5){conclusion}` | `statusCheckRollup` on first + head commit only | Measured: `checkSuites` returns only stale QUEUED suites with **null conclusions** on this repo, silently nulling out a scored metric. Rollup across all 30 commits cost 3.22s/batch vs 0.88s for the first commit alone — same points, 3.7× the latency. `ci_first_pass_rate` is defined on the first commit anyway. |
| 2 | `comments(10)` | `comments(30)` | 12 of 14 measured overflow drains were comments. Raising the page cut drain traffic and total time ~3.8×. |
| 3 | `ci_failure_rate_any` (context) | `ci_head_pass_rate` (context) | Follows from #1 — per-commit CI state is no longer fetched. The head state is well-defined and contrasts usefully with first-pass. |
| 4 | bot list incl. `app/trunk-io` | `app/posthog`, `app/posthog-js-upgrader`, `app/scheduled-actions-posthog` | `app/trunk-io` measured **0**; two live bots were missing from the brief's list. |
| 5 | 33 metrics (set unspecified) | 33, having dropped 5 candidates | `weekend_activity_share` dropped on principle — surfacing weekend work in a ranking tool invites the misuse the caveats panel warns against. |
| 6 | git-clone revert detection | API-only (title/body references) | Timeboxed and skipped per §5.7. |
| 7 | `revert_rate` scored at 6.45% | **Context-only, 0%** | Measured: 23 revert-titled PRs in the corpus, **1 of 50 people nonzero**. The other 49 shared one percentile, so it added a constant to every score — no separation — while diluting the metrics that do discriminate. Fails §6's own second criterion. Its 6.45% renormalised within Builder: `test_habit` 13.95→16.28%, `risk_weighted_contribution` 11.7→13.66%, the other two 6.45→7.53%. Still computed and displayed as context. |
| 8 | weights as literal constants | within-pillar **shares**, absolutes derived | Hand-tuned absolutes silently stop summing to the pillar weight the moment a metric is demoted. Shares rebalance exactly (asserted to 1e-12). |

---

## 13. Open questions for the operator

1. **Normalisation pool** — top 50–100 (better statistics, ~6,544 PRs to hydrate) or top 10 only
   (faster, ~3,531 PRs, coarse buckets)? Plan currently assumes **50–100, display 10**.
2. **Effective weights** — accept the inherited concentration (`risk_weighted_contribution` at 11.7%,
   outcome-quality trio at 6.45% each), or set sub-pillar weights explicitly?
3. **Revert detection** — attempt the clone, or skip it up front and let the 6.45% renormalise?
4. **Recurrence** — is this a one-off or a recurring job? Only affects whether we wire the
   `updated:>=` incremental path now; `updated_at` gets stored either way.
