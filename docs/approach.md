# Approach

How this system was designed, what it refuses to measure, and why almost every decision in it
exists because a simpler alternative was tried and found wanting.

This document is long because the interesting parts of this problem are not the parts that look
hard. Fetching data from GitHub is not hard. Computing a weighted average is not hard. What is
hard is building something whose numbers survive contact with the person they describe — and
almost everything in this codebase is there to serve that single requirement.

---

## 1. The problem

The request was to rank the top engineers in a large, fast-moving open-source repository
(`PostHog/posthog`) over a 90-day window, based on their fix and bug work.

That sentence contains a trap, and the trap is the word "top." Every naive reading of it
produces a system that measures throughput. Count the pull requests. Count the lines. Measure
the cycle time. Rank descending. The result is defensible-looking, trivially computable, and
wrong in a way that is specific and newly urgent.

It is wrong because in this repository, in this window, **one account authored 1,285 pull
requests in 90 days** — roughly fourteen per day, sustained, for three months. No human writes
fourteen pull requests a day for three months. And indeed this human did not: 1,160 of those
1,285 PRs, **90.3%**, carry a self-declaration in the PR body reading `Autonomy: Fully
autonomous`. They were dispatched to an agent fleet, not authored.

A volume leaderboard crowns that account, and it does so *correctly by its own logic*. The
account really did cause 1,285 PRs to exist. The logic is not broken; the question is.

This is not a hypothetical concern about where software engineering might be heading. It is the
measured composition of a production repository today: **23.2% of in-scope pull requests were
fully autonomous, 62.3% were human-driven with agent assistance, and only 14.5% carried no
declaration at all.** Fewer than one in seven pull requests in this corpus is unambiguously
hand-written in the pre-agent sense. Any measurement system that treats PR count as a proxy for
contribution is now measuring, in large part, how many agents someone has running.

So the design constraint that shapes everything downstream is this:

> Measure **how** work landed, never **how much** of it there was.

That constraint sounds like a preference. It is closer to a load-bearing wall. It determines
which metrics are admissible, how the cohort is selected, what the scoring pipeline has to
correct for, and — most consequentially — what the user interface must confess.

### 1.1 What "how it landed" means concretely

If volume is off the table, what is left? The answer is the set of facts that describe a change's
passage through review and into production, normalized by the number of changes:

- Did it pass CI the first time, or did it go red and get fixed up?
- How many rounds of requested changes did it take before it merged?
- Did it carry tests?
- How large and how broad was it — did this person take on risk, or only safe work?
- Was it later reverted?

And symmetrically, for review work:

- Were approvals accompanied by any substantive comment, or were they rubber stamps?
- Did comments land — did the threads this person opened get resolved?
- How quickly did review arrive?
- How many *distinct* authors did this person review for?

Each of these is a **rate or a ratio**, not a count. Each is invariant to running more agents.
A person who dispatches a hundred agent PRs and a person who writes five by hand are compared on
the same axis: of the work attributable to you, what fraction landed cleanly?

That invariance is the entire point, and it is why the metric registry contains not a single
scored count.

### 1.2 What this system deliberately cannot see

An honest accounting of scope belongs at the front, not buried in a caveats appendix.

This system sees pull requests. It therefore cannot see design work that happened in a document,
mentoring that happened in a call, incident response that happened at 3am in a terminal,
architectural judgment that prevented work from being necessary, or the quiet labor of keeping a
team unblocked. These are frequently the most valuable things a senior engineer does, and they
leave no trace in the GitHub API.

Furthermore, the scope here is narrower still: **only fix and bug pull requests**. Feature work,
refactors, documentation, and chores are all filtered out by the conventional-commit prefix
filter. This was a deliberate scoping choice in the brief, and it has a real consequence:
somebody who spent the quarter shipping a major feature cleanly appears in this data barely at
all, and somebody who spent it grinding through a bug backlog appears prominently.

The system is therefore not a performance review, cannot be used as one, and says so — in the
interface, permanently, not in a footnote. Section 16 explains why that confession is placed
where it is.

---

## 2. Why volume metrics fail, precisely

It is worth being exact about the failure mode, because "volume metrics are bad" is a slogan and
slogans do not survive a meeting with someone whose ranking just dropped.

### 2.1 The substitution problem

A volume metric is a proxy. PR count stands in for "amount of useful work done." Proxies hold
only as long as the cost structure that made them correlate remains stable. PR count correlated
with effort because writing a pull request took a human some hours.

Agent dispatch severs that correlation. The marginal cost of the 15th PR in a day approaches the
cost of describing it. The proxy does not degrade gracefully — it inverts, because the people
most able to dispatch volume are precisely the people whose individual craft the metric was
trying to detect.

### 2.2 Goodhart, accelerated

The classical objection to volume metrics is Goodhart's law: once a measure becomes a target, it
stops being a good measure. The usual timescale for this is quarters — people gradually learn
what is counted and drift toward it.

Agents compress that timescale to days. A person who learns that PR count is measured does not
need to change their habits over months; they need to change a dispatch configuration. The gap
between "this metric is announced" and "this metric is saturated" is now short enough that the
metric is arguably never valid after publication.

This is a strong argument for publishing metrics that are *rates*, because a rate cannot be
saturated by volume. You cannot improve your CI first-pass rate by dispatching more agents; if
anything, dispatching more agents at constant quality leaves it exactly where it was, and
dispatching more agents at lower quality makes it worse.

### 2.3 Cycle time is a volume metric in disguise

A subtle case worth flagging: median cycle time — creation to merge — looks like a quality
measure and is not. It rewards small, fast, low-risk changes and penalizes the large integration
work that is often the most valuable thing in a quarter. Worse, it is trivially improved by
splitting work into more, smaller PRs, which is to say, by increasing volume.

Cycle time is computed here and displayed as context. It carries zero weight. The same is true
of PR count, review count, median PR size, and commits per PR. They are shown because they are
genuinely informative when a human is interpreting a person's profile; they are unscored because
they are not defensible as a ranking axis.

### 2.4 The one place volume is legitimately used

There is exactly one place volume enters this system: **cohort selection.**

To rank fifty people you must first pick fifty people, and the selection rule here is
participation — authored plus reviewed PR count in the window. This is unapologetically a
volume metric.

It is legitimate in that role for a specific reason: selection and scoring answer different
questions. Selection asks "for whom do we have enough data to say anything?" Volume is an
excellent answer to that question, because a person with three PRs in the window genuinely cannot
be characterized. Scoring asks "how well did this land?", and volume is a terrible answer to
*that*.

The risk is that the two get conflated by a reader, who sees a leaderboard and assumes the people
on it were chosen for being good rather than for being measurable. The interface therefore states
explicitly, in the permanent caveats panel: **"People entered this cohort by participating in the
most fix/bug PRs, then were judged on non-volume metrics. High participation is a selection
criterion here, never a score."**

---

## 3. Planning as measurement, not as speculation

The project began with a planning phase that produced no code. About ten minutes of a
sixty-five-minute build — roughly 15% — went to querying the live GitHub API to check assertions
before anything was built on them.

This was the highest-leverage time in the project, and it is worth explaining why, because the
instinct under time pressure is to skip it.

### 3.1 The brief was expert and still wrong in four places

The specification for this build was unusually good. It was written by someone who had built a
similar system before and lost 5,000 PRs and ninety minutes to a crash at 65% completion. It
carried scar tissue: guard every `.json()` call, build checkpointing before the fetch that needs
it, treat the three rate limits as three separate problems, make committed data byte-stable,
never let a substring heuristic classify a human.

Every one of those warnings was correct and every one is implemented here. And yet four of the
brief's concrete claims did not survive ten minutes of verification:

| Claim | Measured |
|---|---|
| Excluding bot authors removes 38% of PRs | **8.9%** |
| Bot list: `app/trunk-io`, `app/posthog` | `app/trunk-io` had **0 PRs**; two live bots were missing entirely |
| Median GraphQL round-trip ≈ 2.33s | **4.4s** |
| ~7,329 PRs in scope | **5,945** |

None of these were errors of competence. They were **measurements with a shelf life**. They were
true when taken, against a repository that has since changed its bot fleet, its traffic, and its
merge volume. The brief's own guidance — instrument, measure, delete what earns nothing — was
exactly right; the specific numbers had simply aged.

The lesson generalizes uncomfortably: *the more authoritative a specification sounds, the more
tempting it is to build on its numbers without checking them.* A vague brief invites
verification. A precise one, full of measured figures and hard-won warnings, invites trust.

### 3.2 The assumption that would have been invisible

Three of the four failures above were quantitative — they would have changed projections and
been noticed eventually when the projections missed.

The fourth was different in kind, and it is the reason planning-by-measurement earned its 15%.

The brief specified fetching CI status via `checkSuites(first:5){nodes{conclusion}}`. This is a
reasonable-looking query. It is also, on this repository, **completely empty** — it returns five
stale suites in `QUEUED` status, every one with `conclusion: null`.

Consider the failure mode. The query does not error. The response is a valid 200 with a
well-formed body. The field is present. It simply contains `null`, forever, for every commit.
Downstream, `ci_first_pass_rate` — a *scored* metric carrying 6.45% of the total weight —
computes cleanly to `None` for every person in the cohort, gets dropped by the
missing-metric renormalization logic (which is working correctly!), and its weight silently
redistributes to the other metrics.

The system would have produced a complete, plausible, internally consistent leaderboard in which
one of the nine scored metrics had never once been evaluated. Nothing in the logs, nothing in
the tests, nothing in the output would have indicated this. It would have been found, if ever, by
someone asking why nobody's CI numbers ever appeared in the drill-down.

Caught during planning, it cost about four minutes: probe the field, discover the nulls, test
`statusCheckRollup` instead, confirm it returns real `SUCCESS`/`FAILURE` states. Caught after
publication, it invalidates every Builder score in the system and every decision anyone made
while reading them.

### 3.3 The unexpected performance dividend

The fix to that bug produced the single largest performance win in the project, which was not
anticipated.

`statusCheckRollup` returns real CI state, but it is expensive — it aggregates check runs and
commit statuses across every check registered on a commit. Requesting it across all thirty
commits in the page cost **3.22 seconds per 25-PR batch**. Requesting it on the *first commit
alone* cost **0.88 seconds**, at identical point cost.

And the first commit is all that is needed, because of how the metric is defined. `ci_first_pass_rate`
asks whether the **first pushed commit** passed — a PR that went red and was subsequently fixed up
did not pass first time. The other twenty-nine rollups were pure waste, purchased at 3.7× the
latency.

Combined with a second discovery from the same measurement pass — that comments, not reviews,
were the dominant cause of nested-page overflow, and raising that page from 10 to 30 eliminated
most of the follow-up requests — hydration throughput went from **1.15 seconds per PR to 0.30
seconds per PR**.

The generalizable observation: a correctness investigation and a performance investigation
frequently turn out to be the same investigation. Looking closely enough at a field to discover
it is empty also reveals what it costs.

### 3.4 What planning is for

Planning here was not an attempt to foresee the design. Most of the design was obvious from the
brief. Planning was an attempt to **find the assumptions whose failure would be silent**, because
those are the only ones that genuinely cannot be recovered from later.

A wrong latency estimate announces itself — the run takes longer than projected. A wrong PR count
announces itself. An empty field that nulls out a metric announces nothing at all, ever.

The heuristic that falls out: before building on an external API's behavior, ask not "is this
likely to be right?" but **"if this were wrong, how would I find out?"** Any assumption whose
answer is "I wouldn't" gets verified before it gets built on, regardless of how confident the
source seemed.

---

## 4. The shape of the system

```
GitHub GraphQL ──▶ fetcher ──▶ data/raw/*.jsonl.gz   (byte-stable, committed)
                      │              │
                      ├─ checkpoint/*.jsonl          (append-only scratch, gitignored)
                      ▼              ▼
                 DuckDB:  raw ──▶ derived ──▶ serving
                                                │
                                           FastAPI (zero compute) ──▶ UI ──▶ Cloud Run
```

Four stages, each with a single responsibility and a clean boundary:

1. **Fetch** — talk to GitHub, survive its failure modes, write durable raw data.
2. **Store** — three layers, raw through derived to serving, each strictly downstream.
3. **Score** — six deterministic steps, every intermediate persisted.
4. **Serve** — read denormalized rows, compute nothing.

The boundaries are not decorative. The fetch layer knows nothing about metrics; the scoring
layer never issues a network call; the serving layer contains no arithmetic. This means a
scoring change never requires a re-fetch, and a UI change never requires a re-score — which,
during a sixty-five minute build with a thirteen-minute download in the middle of it, was the
difference between iterating freely and being held hostage by the network.

### 4.1 Two phases of fetching

The fetch itself splits into a cheap sweep and an expensive hydration.

**Phase 1, the skim,** sweeps every merged fix/revert PR in the window and ranks people by
participation. It fetches the bare minimum: PR number, author, timestamps, and a *count* of
reviews with the first ten reviewers. This is enough to rank participation and nothing more.

**Phase 2, the hydration,** deep-fetches only the PRs that the selected cohort authored or
reviewed: full file lists, commits, reviews, review threads, comments, bodies.

The rule that governs what belongs in each is a single question, applied to every candidate
field:

> **Does this change *who* I fetch next?**

If yes, it belongs in the skim. If no, it belongs in hydration. `reviews(first:10){totalCount}`
passes: the count determines participation rank. `reviews(first:50){nodes{bodyText}}` fails —
review bodies are needed for rubber-stamp detection, but that is a *scoring* concern, and
fetching them during discovery would mean fetching them for all 5,945 PRs instead of the 5,136
that matter.

That discipline roughly halves the sweep's request count, because a lighter payload permits page
size 100 instead of a smaller page.

### 4.2 Why the cohort is 50 people and the display is 10

There is a tension here worth making explicit.

Percentiles over a small pool are coarse. With ten people, every percentile is a multiple of
0.1, and the shrinkage prior — which pulls small-sample values toward the cohort mean — becomes
noisy, because the "cohort mean" is itself estimated from ten observations.

The obvious fix is a larger pool, and the obvious objection is cost: surely hydrating fifty
people's PRs costs five times as much as hydrating ten.

It does not, and the measurement is instructive:

| Pool | PRs to hydrate |
|---|---|
| Top 10 | 3,531 |
| Top 20 | 4,782 |
| Top 50 | **5,136** |
| Top 100 | 7,732 |

Going from ten people to fifty costs **45% more PRs, not 400% more.** The reason is structural:
the top few people *are* the volume, and review fan-out spreads across nearly everyone. Person
#47 reviewed PRs that person #3 authored, and those PRs are already in the set.

So the design normalizes percentiles against a **50-person pool** and displays the top ten. The
statistics are computed on a base wide enough to be meaningful; the presentation stays legible.
This is one of the clearest instances in the project of a measurement overturning an intuition
about cost — and the corollary, stated in the architecture notes, is that **cohort size is not
the speed lever. Concurrency is.**

---

## 5. Surviving GitHub

More of this codebase is devoted to surviving the GitHub API than to any other single concern.
That allocation is not paranoia; it is proportionate to what was observed. During the production
run, **77 transient errors** occurred across roughly 600 requests — a failure rate above 12%.

### 5.1 Three rate limits, three responses

The most important structural insight in the fetch layer, inherited from the brief and fully
confirmed in practice, is that GitHub enforces three distinct limits that *look* similar and
require *opposite* responses.

| Limit | How it announces itself | Correct response |
|---|---|---|
| **Primary points** (5,000/hr) | `rateLimit.remaining` in the response body | Sleep until `resetAt` |
| **Secondary / burst** | HTTP 403, no budget information at all | Reduce concurrency; waiting does not help |
| **Node ceiling** (~500k/query) | Query-level error | Cap page sizes |

Conflating the first two is the expensive mistake. If you treat a secondary 403 as a budget
problem and sleep, you wait pointlessly — the secondary limiter is watching request *rate*, not
consumption, and a sleeping client that then resumes at full concurrency trips it again
immediately. If you treat primary exhaustion as a burst problem and merely slow down, you grind
through the remainder of the hour issuing requests that all fail with hard `RATE_LIMITED`
errors.

And the specific trap in primary exhaustion: **sleeping a fixed interval is worse than useless.**
A flat sixty-second sleep wakes into the same empty budget, spends its first request confirming
that, sleeps again, and converts the remainder of the window into a tight loop of failures. The
only correct behavior is to read `resetAt` and sleep until it.

### 5.2 Instrument your own counter

The brief warned that `gh api rate_limit` had disagreed with observed consumption three separate
times. The implementation therefore does not trust the reported budget as a predictor. It
accumulates its own counter from the `rateLimit { cost }` field returned with every query, and
that counter is what gets reported.

The final production numbers came from that self-instrumentation: **~1,550 points of a 5,000-point
window, 0.32 points per PR.** The brief's target was 0.58; the improvement came from the
`statusCheckRollup` change described in §3.3, which reduced cost as well as latency.

This mattered more than the arithmetic suggests. At the untuned 1.56 points per PR the brief
originally measured, the same job costs roughly 8,000 points — which is not "60% slower," it is
**two hourly windows and a 45-minute refill wait in the middle.** Page-size tuning is what kept
this a single-window job. Crossing a budget boundary is a step function, not a gradient.

### 5.3 Guard every deserialization

> GitHub serves error pages with **HTTP 200**.

This was the brief's most emphatic warning, and it understated the problem. Two distinct
variants appeared in production:

**HTML with a 200 status.** An error page, served with a success code, which `json.loads` rejects.

**Truncated JSON with a 200 status.** This was not anticipated. Four times during the run, the
response body began with entirely valid JSON and simply stopped:

```
non-JSON 200 ('{"data":{"repository":{"p0":{"number":74999,"title":"fix(data-warehouse): stop sources pag
```

A connection cut mid-stream. The status code is 200, the headers are correct, the first two
hundred bytes parse fine, and the payload is incomplete. Any code that assumes a 200 implies a
parseable body loses the entire request — and, if it is not retrying, loses the run.

Every deserialization in the client is wrapped, and a non-parseable 200 is classified as
**transient** and retried. The retry taxonomy overall:

| Class | Treatment |
|---|---|
| 5xx | Transient — retry with exponential backoff |
| Connection error | Transient |
| Non-JSON 200 (HTML or truncated) | Transient |
| `RATE_LIMITED` *inside* a 200 body | Transient — sleep to `resetAt` first |
| 403 / 429 | Transient — halve concurrency, do not sleep on a number you don't have |
| GraphQL query error | **Fatal** — retrying a malformed query only burns budget |

That last row matters. A system that retries everything indefinitely converts a typo in a query
into an hour of wasted quota.

### 5.4 One shared token bucket, and the sleep goes outside the lock

Pacing is deceptively easy to get wrong in a concurrent client, and there are two independent
traps.

The first: **the bucket must be shared.** If each of N workers independently sleeps 0.8 seconds
between its own requests, the *aggregate* request rate is N requests per 0.8 seconds. The
pacing looks correct in each worker and is wrong by a factor of N in aggregate, which walks
straight back into the secondary limiter that the pacing existed to avoid.

The second is subtler: **the sleep must happen outside the lock.** The natural implementation
takes a lock, checks the next permitted time, sleeps until then, and releases. That is correct
and it also completely serializes the client — while one worker sleeps holding the lock, every
other worker blocks on acquisition, so the effective concurrency is 1. The bug is invisible
except as unexplained slowness.

The implementation reserves a slot under the lock, releases the lock, *then* sleeps:

```python
async with self._lock:
    now = loop.time()
    wait = max(0.0, self._next_at - now)
    self._next_at = max(now, self._next_at) + self.min_interval   # reserve
await asyncio.sleep(wait)                                          # sleep unlocked
```

Both properties have direct tests: one asserts the observed inter-request gap across four
concurrent workers respects the shared minimum; the other asserts that four workers contending
on a primed limiter finish in well under the serialized time.

### 5.5 Adaptive concurrency, and the cascade that broke it

Concurrency starts at 3, increments by one after a window of clean requests, halves on a 403,
and caps at 8. Start conservative, grow into the available headroom, retreat hard on the first
sign of burst throttling.

This worked exactly as designed, and then failed in a way that no test anticipated. From the
production log:

```
11:59:46  throttled; concurrency halved -> 4
11:59:47  throttled; concurrency halved -> 2
11:59:48  throttled; concurrency halved -> 1
```

Three halvings in two seconds, from 8 to 1.

The cause: when GitHub's secondary limiter fires, it does not fire at one request. It fires at
the *burst*, and every request currently in flight comes back 403. With eight workers airborne,
eight of them called `on_throttle()`, and each halved the limit independently.

The consequence was worse than the collapse itself. With a clean-window of twenty, climbing back
from 1 to 8 requires roughly 140 consecutive clean requests. The run did not fail — it **crawled**,
and crawling is a far more dangerous failure than crashing, because nothing errors, nothing
alerts, and the only symptom is that progress has quietly stopped. It sat at 4,000 of 5,136 PRs
while looking, in every log line, entirely healthy.

The fix is a cooldown. One throttling **episode** halves **once**:

```python
now = time.monotonic()
if now - self._last_throttle < self.throttle_cooldown:
    return          # same burst; the other workers are reporting the same event
self._last_throttle = now
```

The cooldown is 15 seconds, chosen to exceed worst-case observed round-trip time, so that every
request already in flight when the burst landed is attributed to the same episode.

After the fix, the resumed run climbed to concurrency 7 with **zero halvings** and completed
without incident.

The generalizable lesson: **a feedback controller must distinguish between N reports of one
event and N separate events.** Any reactive system — backoff, circuit breaking, autoscaling —
that takes a corrective action per *report* rather than per *episode* will over-correct by
exactly the concurrency factor, and the more concurrent it is, the worse it over-corrects.

---

## 6. Checkpointing, which was built first

The brief was unambiguous: *a fetcher without resume is a prototype.* The previous build had
listed resume as an acceptance test, shipped without it, and lost 5,000 PRs and ninety minutes
at 65% completion.

So checkpointing was built **before the first long fetch existed**, with its acceptance test —
`kill -9` mid-run, resume, assert zero duplicates and zero loss — passing before there was
anything to check-point. This felt, at the time, like process discipline for its own sake. It
was vindicated within the hour.

### 6.1 The ordering invariant

The single most important property is an ordering constraint that sounds trivial and is not:

> **Raw JSON is written first. The database is loaded second.**

Append-only writing cannot half-fail: either a line is in the file or it is not. So if raw is
written first and the process dies, the worst outcome is a raw record with no corresponding
database row — which the next run repairs, because it reloads from raw.

Reverse the order and the failure is unrecoverable in a way that does not announce itself: you
get **database rows with no raw record behind them.** The database looks correct. Queries return
data. But the committed raw — the thing that is supposed to be the source of truth, the thing
that can rebuild everything — is now silently missing records, and nobody discovers this until
someone runs a restore weeks later and gets a smaller dataset than they started with.

The implementation enforces the ordering in one place: the checkpoint's flush writes to disk,
*then* invokes the store callback.

### 6.2 `BaseException`, not `Exception`

The context manager's `__exit__` flushes its buffer before propagating. The detail that matters:
it must catch **`BaseException`**, not `Exception`.

`KeyboardInterrupt` — Ctrl-C — does not inherit from `Exception`. It inherits from
`BaseException`. A handler written as `except Exception` looks like it covers interruption and
covers everything *except* the most common way a long-running job actually gets interrupted: a
human deciding to stop it.

Python's context manager protocol calls `__exit__` for `BaseException` too, so implementing the
flush there gets this right as a side effect. There is an explicit test that raises
`KeyboardInterrupt` inside the block and asserts the buffer reached disk.

### 6.3 Torn lines are normal, not exceptional

A process killed with `SIGKILL` mid-write leaves a partial final line:

```json
{"number":99,"tor
```

The naive reader raises `JSONDecodeError` and the resume fails — which converts a recoverable
situation into an unrecoverable one at precisely the worst moment.

The reader treats a torn final line as **the expected result of an abrupt kill**, not an error:

```python
try:
    yield json.loads(line)
except json.JSONDecodeError:
    continue
```

This is deliberately permissive, and the permissiveness is bounded by the fact that only the
final line can plausibly be torn — earlier lines were followed by successful writes.

### 6.4 The restart that proved it

When the concurrency cascade stalled the run at 4,000 of 5,136 PRs, the recovery was:

```
resuming hydrate: 4000 done, 1136 remaining
```

Zero refetch. Zero duplicates. A 9-minute re-download reduced to a 15-second restart, and — not
incidentally — roughly 1,200 rate-limit points not spent a second time.

Building it first was the reason it was there when it was needed. Had it been deferred to
"after the pipeline works," it would have been written *during* the outage, under pressure,
against a half-complete dataset.

### 6.5 Failing loudly instead of quietly

One gap in the first implementation is worth recording, because it was of exactly the kind this
project was built to avoid.

The batch worker caught exceptions and continued:

```python
except Exception:
    log.exception("batch failed (%d PRs), continuing", len(batch))
    continue
```

This drops 25 PRs and writes a log line. On a healthy run it never fires. On an unhealthy run it
fires repeatedly, and the operator gets a dataset that is quietly short by some multiple of 25,
with no indication in the output.

It was replaced with a re-queue and an explicit accounting: a failed batch goes back on the
queue up to a limit, and if it still cannot land, its PR numbers are recorded in the run's
statistics and logged at ERROR as `DATA LOSS`. Giving up is acceptable. **Giving up silently is
not** — the operator must be told exactly which records are missing rather than being left to
infer it from a short count.

The same principle governs the search pagination (§7.2): a date range that cannot be split
further and still exceeds the result cap is a genuine, unrecoverable loss, and it is logged as
such rather than silently truncated.

---

## 7. Two problems with silent failure modes

### 7.1 Byte-stability, and why it needs a second file

Raw data is committed to the repository. That is only useful if an unchanged upstream produces
an unchanged file — otherwise every re-run generates a multi-megabyte diff, the diffs become
noise, and nobody reads them.

Deterministic output requires three things, and the third is the one people miss:

```python
json.dumps(sort_keys=True, separators=(",", ":"))   # stable field order
records sorted by PR number                          # stable record order
gzip.GzipFile(mtime=0)                               # stable header
```

**gzip embeds the current timestamp in its header by default.** Two files with byte-identical
contents, compressed one second apart, differ in bytes 4 through 8. Without `mtime=0`, every
single re-run produces a diff regardless of whether anything changed.

The deeper structural point is that byte-stability and crash-safety are **irreconcilable in one
file**:

- Byte-stability requires everything sorted, which means writing at the *end*.
- Crash-safety requires appending as you go, which means writing *unsorted*.

You cannot have both in one artifact. So there are two:

| File | Property | Fate |
|---|---|---|
| `checkpoint/*.jsonl` | Append-only, unsorted, crash-safe | Gitignored; deleted only after the canonical file exists |
| `raw/*.jsonl.gz` | Sorted, deterministic, diffable | Committed |

The ordering of the final steps matters as much as the ordering in §6.1: write the canonical
file, verify it, *then* delete the checkpoint. Delete first and a crash in between loses
everything.

Verification is a round-trip assertion rather than a stored hash: `top-engineers verify` reads
the file, re-encodes it through the same encoder, and compares SHA-256 against the bytes on
disk. This tests the **encoder**, not just the file — a stored checksum would pass even if the
encoder had become non-deterministic. Confirmed on the real 19MB artifact.

### 7.2 The search cap that does not announce itself

GitHub's search API returns at most 1,000 results. It does not error at 1,001. It does not warn.
It returns 1,000 results and a `pageInfo` that terminates, and the response is indistinguishable
from a query that genuinely had 1,000 matches.

With 5,945 PRs in the window, a naive single query would silently return 1,000 and lose 83% of
the corpus with no indication whatsoever.

The handling is recursive date-slicing driven by a cheap count query: ask for `issueCount`
first; if it exceeds the cap, split the range in half and recurse. This is **self-correcting** —
there is no need to guess a slice width in advance, and it adapts automatically to bursty
periods.

The base case is the important part. When a range narrows to a **single day** that still exceeds
1,000 results, it cannot be split further. That is real, unrecoverable data loss, and it is
logged at ERROR level with the exact day and count, and recorded in the run's statistics:

```python
log.error("DATA LOSS: %s on %s has %d results, above the %d cap and unsplittable; "
          "%d PRs will be missing", ...)
```

The production run hit zero such days. But the alternative — silently returning 1,000 and moving
on — is precisely the class of failure this system is built to refuse.

### 7.3 Nested pagination, and why truncation is biased

A related trap operates one level down. Each PR's files, commits, reviews, threads, and comments
are themselves paginated connections. Page sizes are tuned for the **median, not the maximum** —
the median PR changes 2 files — which is what keeps cost at 0.32 points per PR instead of 1.56.

Tuning for the median necessarily means some PRs overflow their pages. Every connection is
therefore drained when `hasNextPage` is true.

The reason this cannot be skipped is that **truncation is not random, it is biased.** The PRs
that overflow are the largest, most-reviewed, most-discussed ones. Dropping the overflow does
not add noise; it systematically deflates exactly the busiest PRs — which are precisely the
inputs to the reviewer-quality metrics. A reviewer who does thorough work on contentious changes
would have that work truncated preferentially.

Measured overflow was about 4% of PRs, dominated by comments. Draining costs roughly 4% more
requests and removes a systematic bias, which is an obvious trade.

---

## 8. Identity, where 40% of the data is at stake

The brief's warning was blunt: get identity wrong and 40% of the data is misattributed. The
measured bot-authored share in the window was substantial, and a single misclassification at the
top of the participation ranking distorts the entire cohort.

### 8.1 The obvious signals are insufficient

Two structural signals exist: GitHub's `Bot` account type, and the `[bot]` login suffix. Both
are used. Neither is sufficient.

The counter-example named in the brief is `stamphog`: a **`User` account** that posts automated
approvals. Structurally it is a human. Behaviorally it is a bot. And because its automated
approvals are detailed enough to escape rubber-stamp detection, leaving it unclassified would
put an automation at the top of every reviewer-quality metric in the system.

There is no API field that resolves this. Only a human who knows the team can.

### 8.2 The heuristic that must not be a classifier

The tempting fix is substring matching — flag logins containing "bot", "ci", "auto", "hog".

The brief records what happened when that was tried: **10 flags, 10 false positives.** Every
single one a real employee. The base rate of these substrings in ordinary human usernames is
simply too high, and in a repository named `posthog` the string "hog" is catastrophically
uninformative.

The resolution is to keep the heuristic but **strip it of authority**. It generates a *review
queue*, never a classification:

- `actors.yaml` — human-reviewed, hand-edited, the only file `is_bot()` consults.
- `actors.candidates.yaml` — generated, a queue of suggestions with evidence links, explicitly
  labeled as unreliable.

`is_bot()` consults the reviewed file plus the two structural signals, and **never** performs
substring matching. The candidate generator sorts structural signals above name hints and
annotates every hint with `(UNRELIABLE: 10/10 false positives)` so that a future reader does not
rediscover this the expensive way.

`stamphog` is deliberately *not* in the shipped `actors.yaml`, with a comment explaining why: it
needs a human to confirm its approvals are automated before it is excluded. Shipping a guess
would defeat the purpose of the file.

### 8.3 Structured self-declaration beats every heuristic

The most important identity signal in this project required no inference at all, and finding it
is the single highest-leverage thing in the brief.

PostHog's pull request template contains a field:

```
**Autonomy:** Fully autonomous | Human-driven (agent-assisted)
```

Authors fill it in. Measured coverage in the corpus: **85%**.

No heuristic — not commit cadence, not diff shape, not time-of-day clustering — comes close to a
declaration the author wrote themselves. The generalizable instruction is worth stating plainly:

> **Check for structured self-declaration before building any inference.**

It is easy to skip. Inference is the interesting engineering problem, and reading a template
field is not. But the template field is right and the inference is a proxy.

### 8.4 The unedited-template trap

Parsing this field has a trap that is easy to miss and quietly corrupts a quarter of the data.

An author who never edits the template leaves **both** options in the body:

```
**Autonomy:** Human-driven (agent-assisted) - or - Fully autonomous
```

A first-match parser scanning for "human-driven" finds it and classifies the PR as human-driven.
The PR is not human-driven. It is **undeclared** — the author skipped the field entirely.

So the parser tests for "both options present" **before** testing for either individually, and
resolves that case to `unknown`. It also handles the truncated variant (`... - or - Fully au`),
where body truncation leaves a dangling fragment of the second option — detected by splitting on
the separator and prefix-matching the remainder.

The measured effect is visible: 86% of bodies *mention* autonomy, but only 82–85% *declare* it.
That gap is the unedited templates, correctly excluded.

### 8.5 Asymmetric handling, and the refusal to hide it

Autonomy is applied **asymmetrically**, and the asymmetry is the substantive judgment in this
entire system:

> Fully autonomous PRs are **excluded from Builder metrics** — dispatched, not authored.
> They are **kept for Reviewer metrics** — reviewing an agent's PR is real review work.

Both halves matter. Excluding dispatched work from authorship credit is the point of the
exercise. But a reviewer who catches a bug in an agent's PR did genuine work, and excluding that
would penalize exactly the vigilance the situation demands.

The third component is a presentational commitment with real weight:

> **`dispatched_n` is reported per person.**

Excluded-from-scoring is **not** did-not-happen. A person who dispatched 1,160 agent PRs did
something substantial, and a system that silently dropped that work would be lying by omission —
and would be immediately, correctly attacked by anyone who knew the underlying facts.

The interface therefore shows authored, reviewed, **and dispatched** side by side, so the reader
sees both the score and the work that the score deliberately excluded, and can disagree with the
choice on the evidence.

---

## 9. Metric selection

Thirty-three metrics are computed. **Nine** are scored. The remaining twenty-four are displayed
as zero-weight context.

That ratio is deliberate. Computing a metric is cheap once the data is local; scoring it is a
claim that it belongs in a ranking. Keeping the unscored ones visible means a reader can see the
volume figures — PR count, cycle time, LOC — in their proper role: informative context that the
ranking explicitly refuses to use.

### 9.1 Three criteria, applied in order

1. **Has data on the window.** A metric that is null for most of the cohort contributes nothing
   and destabilizes renormalization.
2. **Separates people rather than clustering them.** A metric on which everyone scores
   identically adds a constant to every score — pure dilution.
3. **Measures how work landed, not how much.**

The second criterion is the one that is easy to state and hard to apply, because it can only be
evaluated **after** the data exists. It is the criterion that removed a metric post-hoc, and
that story is §9.4.

### 9.2 The scored nine

| Pillar | Weight | Metrics |
|---|---|---|
| **Builder** | 45% | `test_habit` 16.3% · `risk_weighted_contribution` 13.7% · `ci_first_pass_rate` 7.5% · `review_rounds_per_pr` 7.5% |
| **Reviewer** | 40% | `rubber_stamp_rate` · `comment_acceptance_rate` · `review_latency_hours` · `author_breadth` — 10% each |
| **Cross-cutting** | 15% | `review_to_authoring_ratio` |

Every one is a rate, a ratio, or a normalized mean. Not one is a count.

Two Reviewer metrics deserve comment. `rubber_stamp_rate` — the share of a person's *approvals*
that carried no review body and no inline comment — is the closest thing here to a direct
integrity measure, and it is the reason autonomy is retained on the reviewer side: approving an
agent's PR without reading it is precisely the behavior worth surfacing. And `author_breadth`,
the count of *distinct* authors reviewed, rewards spreading context across a team rather than
repeatedly reviewing one collaborator.

### 9.3 Two definitions that are easy to get quietly wrong

**`ci_first_pass_rate` keys off the FIRST pushed commit, not the head commit.**

The distinction is the entire metric. A PR that went red, got fixed up, and merged green did
**not** pass CI first time. Keying off the head commit measures "did this eventually merge
green," which is very nearly constant — production data shows **96% of PRs green at head, but
only ~53% green on the first commit.** The head-commit version has almost no variance; the
first-commit version has plenty. One measures a policy (you cannot merge red); the other
measures a habit.

**`revert_rate` censors the final 14 days by removing them from the DENOMINATOR.**

A PR merged yesterday has not had time to be reverted. Counting it as "not reverted" is not
conservative — it is **wrong**, and wrong in a direction that systematically rewards whoever
merged most recently. The only correct handling is to remove recent merges from the denominator
entirely: they are unobserved, not clean.

### 9.4 Removing a metric after seeing the data

`revert_rate` was specified as a scored metric at 6.45% weight. After the full run, the data
showed:

- **23** revert-titled PRs in the entire 5,136-PR corpus.
- **1 of 50 people** with a non-zero revert rate.

Forty-nine people shared an identical value, therefore an identical percentile, therefore an
identical contribution. The metric added a constant to every score — **zero separation** — while
consuming 6.45% of the weight that could have gone to metrics that discriminate.

It failed criterion 2, and criterion 2 can only be checked against real data.

It was demoted to context-only: still computed, still displayed, no weight. Its 6.45%
renormalized within the Builder pillar (`test_habit` 13.95% → 16.28%,
`risk_weighted_contribution` 11.7% → 13.66%).

It is still *shown* because "this team almost never reverts" is genuinely worth knowing. It is
simply not a ranking axis when only one person differs from zero. `change_value_mix` is retained
on identical reasoning — it has no variance on a fix-only cohort by construction, and is kept so
that the absence of variance is visible rather than assumed.

An honest caveat about the number itself: revert detection here is **API-only**, matching revert
PRs to their targets by title and body reference within the corpus. A revert landing outside the
90-day window, or outside the fix-only slice, is invisible. So the low count is partly a
detection limit, not purely a low revert rate. The git-clone path that would close that gap was
timeboxed and skipped — a decision §12 revisits.

### 9.5 Weights as shares, not constants

The original implementation declared absolute weights as literal constants summing to 1.0. That
worked until a metric was demoted, at which point the Builder pillar quietly summed to 38.55%
instead of 45% — and the assertion that caught it was one I had written, firing on my own change.

Weights are now declared as **within-pillar shares**, with absolutes derived:

```python
weight = pillar_weight * share / sum(shares in that pillar)
```

Demoting a metric now rebalances its pillar **exactly**, asserted to 1e-12. The general principle:
if a value must satisfy an invariant, derive it from the invariant rather than hand-maintaining
it and hoping a test notices.

The brief anticipated the underlying issue — it warned to *watch effective weights*, because
equal-splitting within a sub-pillar lets a lone survivor inherit an entire share. The fix here
generalizes that warning into a structural guarantee.

---

## 10. Scoring: six steps, every intermediate kept

The scoring pipeline is six deterministic steps, and **every intermediate value is persisted**.
That persistence is not for debugging. It is the mechanism by which a rank becomes arguable.

```
gate → winsorize → shrink → percentile → weight → uncertainty
```

### 10.1 Gate: withhold the rank, not the metrics

A builder rank requires **5 merged PRs**; a reviewer rank requires **10 reviews**. Below those
thresholds, metrics are still computed and still displayed — only the *rank* is withheld.

The distinction is deliberate. Hiding someone's data because they have little of it is
paternalistic and unhelpful; the data is real. Ranking them against people with forty times the
sample is statistically indefensible. Showing the numbers while declining to rank is the honest
position, and it is visible in the UI as an explicit "below builder gate" marker rather than a
blank row.

### 10.2 Winsorize: bound the outliers

Values are clamped to the 5th and 95th percentiles within the eligible pool. A single
20,000-line PR should not dominate `risk_weighted_contribution` for an entire quarter.

Winsorizing rather than dropping keeps the observation — the person did land that PR — while
bounding its leverage.

### 10.3 Shrink: the step that is non-negotiable

Rate metrics are pulled toward the cohort mean in proportion to how little data supports them:

```
shrunk = (x·n + mean·k) / (n + k)      with k = 5
```

Without this, **a perfect record over 3 PRs beats a strong record over 40**, and the leaderboard
becomes a ranking of who had the smallest sample. This is the single most common way a
well-intentioned metrics system produces absurd results.

With k=5, a person with 3 observations is pulled most of the way to the mean; a person with 40 is
barely moved. The regression test asserts exactly this ordering — a 1.00 rate over 3 samples must
not outrank a 0.90 rate over 40 — and it is worth noting that when I first wrote that test I got
the expected arithmetic *wrong*, because I forgot that winsorization runs first and feeds shrinkage
its clamped value. The code was right; my expectation was not. The test now asserts the composed
order explicitly.

Shrinkage applies only to **rates** — bounded 0-1 proportions. Shrinking a duration or a raw count
toward a mean is not meaningful, and the registry marks which metrics qualify.

### 10.4 Percentile, not z-score

Normalization is to percentile rank within the eligible pool, with lower-is-better metrics
sign-flipped and the balance ratio band-scored.

Percentile rather than z-score because **these distributions are heavily skewed**. Review latency
has a long right tail; PR size is roughly log-normal. A z-score on a skewed distribution produces
scores dominated by tail behavior, and the whole purpose of winsorizing was to prevent exactly
that. Percentile is robust to shape by construction.

Ties share a midrank percentile rather than being ordered arbitrarily — three people with
identical values receive identical scores, which is both correct and necessary for the tie-band
logic downstream.

### 10.5 The non-monotonic metric

`review_to_authoring_ratio` is the one metric where "higher is better" is actively wrong.

Someone who reviews 20× more than they author is not the best engineer on the team; they may have
stopped shipping. Someone who authors constantly and never reviews is not ideal either. The
healthy region is a **band** — here 0.5× to 2.0×.

So it is scored by **distance from that band**: 1.0 inside, decaying below (linearly toward zero)
and above (logarithmically, so 4× is penalized but not catastrophically).

The brief's framing is the clearest statement of why this matters: *scoring it higher-is-better
ranks someone who only reviews above someone who does both.* Any metric with a healthy middle —
and there are more of them than people assume — needs this treatment.

### 10.6 Missing metrics renormalize; they never count as zero

If a person has no data for a metric, its weight is **redistributed across the metrics they do
have**. It is never treated as a zero score.

The difference is enormous. Counting a missing metric as zero punishes people for absent data —
for reviewing fewer PRs than the threshold, or working in an area with no CI signal. It
manufactures a penalty out of a gap in observation.

Renormalization asks instead: *given what we can observe about this person, how do they compare?*
That is the only question the data can actually answer.

### 10.7 Uncertainty: analytic, not bootstrapped

Each metric carries a standard error derived from **its own denominator** — binomial for rates,
an inverse-square-root falloff for others — combined through the same weights as the score
itself.

A true bootstrap was considered and rejected: 33 metrics × 50 people × 1,000 resamples is not
viable in a batch job, and it answers the same question. The question being asked is narrow —
*how much of this number is noise from a small denominator?* — and analytic propagation answers
it directly.

### 10.8 Tie bands, and a bug I did not anticipate

Overlapping uncertainty intervals share a **tie band**. The purpose is to refuse to fabricate an
ordering the data cannot support: if #3 and #4 have intervals that overlap substantially, the
difference between them is noise, and presenting them as ranked is a lie of precision.

My first implementation tracked a running **minimum** of `ci_low` across the band. It produced,
on real data, a single band containing all 50 people — including rank 1 at `[0.637, 0.766]` and
rank 50 at `[0.051, 0.368]`, which plainly do not overlap.

The bug is transitive closure. A overlaps B, B overlaps C, so A, B, and C are grouped — even
when A and C are disjoint. On any smooth distribution, consecutive intervals always overlap
slightly, so the chain never breaks and everyone lands in one band. The output was technically
"correct" under the rule I had implemented and completely uninformative.

The fix anchors each band on its **leader's** lower bound: a new band starts when someone's
`ci_high` falls below the current band leader's `ci_low`. On production data that yields **3
bands** — ranks 1–21, 22–42, 43–50 — which is a genuine, defensible statement about what the
data supports.

The honest reading of that result: this data distinguishes roughly three tiers. It does not
distinguish #7 from #11, and the interface says so rather than implying otherwise.

### 10.9 Persisting the chain

For every person and every metric, the system stores: **raw → winsorized → shrunk → percentile →
weight → contribution → standard error.**

This is what converts the drill-down from a display into an argument:

```
Test habit                      94%   n=18
  raw 0.944 → winsor 0.944 → shrunk 0.909 → pct 0.779 × w 0.163 = 0.1269
```

Someone who disagrees with their rank can see precisely where the number came from and dispute a
specific step — the shrinkage prior, the winsorization bound, the weight — rather than rejecting
the system wholesale. That is the difference between a metric someone can engage with and a
number that feels imposed.

---

## 11. The interface as an argument

The serving layer is denormalized to the point where the UI performs **zero computation**. Every
number, label, explanatory note, and evidence link is materialized during scoring. Measured
render time: **18ms locally, 130ms cold in-container.**

That is an architectural property, not a tuning achievement. Sub-second response is structural
because there is nothing to compute.

But the more interesting design decisions in the UI are about what it *confesses*.

### 11.1 Evidence favours the bad score

Each metric in the drill-down carries linked PRs. The selection rule is deliberately
counter-intuitive: **evidence favours PRs that explain a BAD score.**

The reverted change. The 4,000-line diff. The PR that sat open for three weeks. The approval with
no comment.

The reasoning is adversarial. A person reading their own profile does not need convincing about
the good numbers. They will challenge the bad ones — and the correct response to that challenge
is not a defense of the methodology but **the specific PRs the number came from**. Handing over
the disconfirming evidence unprompted is what makes the number discussable instead of
adversarial.

In production, 7 of 9 evidence items for a typical person are adverse.

### 11.2 The `ⓘ how` note includes the exclusions

Every metric has a plain-English computation note, and every note states what was **left out**:

> *Share of your merged PRs that were later reverted. PRs merged in the final 14 days are
> EXCLUDED FROM THE DENOMINATOR entirely — they cannot yet be observed for a full revert window.
> Fully-autonomous PRs are excluded (dispatched, not authored).*

The principle: **a number whose denominator is a mystery cannot be argued with.** Not "should
not" — *cannot*. Without knowing what was excluded, a reader has no purchase on the value at
all; their only options are to accept it or reject the system. Publishing the denominator is
what makes disagreement possible, and disagreement is the point.

### 11.3 Scored versus context, visibly split

The drawer splits **Scored (9)** from **Context only (24)**, and metrics with no value are hidden
entirely rather than shown as em-dashes.

A reader can see that PR count, cycle time, and LOC were computed and deliberately not used. That
is a stronger statement than omitting them — omission looks like oversight, while visible
exclusion is a position.

### 11.4 Permanent caveats

Nine caveats sit in a panel on the main page, always visible, not behind a disclosure:
volume-selected cohort; fix/bug subset only; 14-day revert censoring; autonomy exclusion with
`dispatched_n` shown; small-pool percentiles; tie bands are not an ordering; ranks withheld below
the gate; balance is a band not a target; and the framing statement — **"this is a conversation
starter, not a performance verdict."**

A caveats section that a reader must click to find is a caveats section designed not to be read.
If these limitations are real enough to state, they are real enough to sit next to the numbers
they qualify.

---

## 12. What broke, and what that teaches

Two genuine bugs reached production. Both were in code the test suite passed. Both were found by
**looking at output**, not by the tests.

That is worth recording without spin. The 111 tests caught expectation errors, regressions, and
every failure mode I thought to imagine. They did not anticipate a throttle-report storm, and
they did not anticipate transitive closure in band assignment — because both bugs were failures
of *specification*, not implementation. The code did exactly what I told it to; what I told it
was subtly wrong.

What the tests did provide was the confidence to fix both in minutes rather than hours, and the
resume path that turned the first bug's damage from a 9-minute re-download into a 15-second
restart.

The pattern common to both: **neither produced an error.** The cascade made the run slow; the
band bug made the output uniform. Systems that fail loudly get fixed. Systems that degrade
quietly ship.

Three habits follow from that, and they are the practical residue of this build:

1. **Look at real output, not just green tests.** Both bugs were visible in thirty seconds of
   reading production results and invisible to a suite that passed.
2. **Ask of every reactive mechanism: what happens when N things report the same event?**
3. **Ask of every grouping rule: does it chain transitively, and do I want it to?**

---

## 13. What was skipped

Three things were deliberately not built, and naming them matters more than the features that
were:

**Git-clone revert detection.** Timeboxed at ten minutes and skipped. Partly vindicated —
`revert_rate` turned out to have almost no variance and was demoted anyway. But the honest
caveat stands: API-only detection undercounts, so part of that near-zero is a detection limit
rather than a genuinely low revert rate. If the metric ever matters, this is the gap to close
first.

**`weekend_activity_share`.** Dropped on principle rather than for cost. Surfacing weekend work
in a ranking tool invites exactly the misuse the caveats panel warns against, regardless of
intent, and a metric whose most likely use is harmful does not belong in the registry at all.

**Bootstrap confidence intervals.** Not viable at this scale, and analytic propagation answers
the same question.

---

## 14. Closing

The system's defining property is that it is **harder to game than a volume leaderboard and more
honest about its own limits than a metrics dashboard usually is.**

Those are related. A system that hides its denominators invites the suspicion that the
denominators are doing something improper. A system that publishes them — along with the
excluded work, the uncertainty intervals, the tie bands, and the disconfirming evidence — can be
argued with, and something that can be argued with can be trusted in a way that something opaque
cannot.

The measured result validates the premise. The person with the most pull requests — **1,285 in
90 days, 90.3% of them dispatched to agents** — ranks **27th**, scored on the 125 PRs actually
authored, with all 1,160 dispatched PRs displayed rather than hidden. On a volume leaderboard
that person is first by a margin no one else approaches.

Neither ranking is a lie. They answer different questions. This system exists because, in a
repository where fewer than one pull request in seven is unambiguously hand-written, the second
question is the one still worth asking — and the first has quietly stopped meaning what everyone
assumes it means.

---

*See also: [architecture.md](architecture.md) for component design, [usage.md](usage.md) for
operation, [time.md](time.md) for the build timeline, and [plan/readme.md](plan/readme.md) for
the original plan with deviations recorded.*
