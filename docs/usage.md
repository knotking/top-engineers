# Usage

## Setup

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Auth uses `GITHUB_TOKEN` if set, otherwise falls back to `gh auth token`. A standard
`public_repo` token is enough — everything read is public.

## Configuration

All configuration is environment variables. **`TP_DB_PATH` and `TP_DATA_DIR` are not
optional in a container** — see [architecture.md](architecture.md#deployment).

| Variable | Default | Purpose |
|---|---|---|
| `TP_DATA_DIR` | `./data` | Root for `raw/`, `checkpoint/`, the DuckDB file |
| `TP_DB_PATH` | `$TP_DATA_DIR/top_engineers.duckdb` | DuckDB file location |
| `TP_ACTORS_PATH` | `./actors.yaml` | Human-reviewed identity file |
| `TP_WINDOW_DAYS` | `90` | Window length |
| `TP_UNTIL` | today | Window end (`YYYY-MM-DD`), for reproducible re-runs |
| `GITHUB_TOKEN` | — | Falls back to `gh auth token` |

Tuning constants (page sizes, concurrency, gates, shrinkage prior, pillar weights) live in
`src/top_engineers/config.py` and `metrics/definitions.py`, each with the measurement that
justifies its value.

## Commands

### `top-engineers skim`

Phase 1. Sweeps merged fix/revert PRs in the window, ranks people by participation
(authored + reviewed), and writes the cohort.

- Excludes bots **server-side** — never downloads rows it intends to discard.
- Splits date ranges recursively around GitHub's **silent 1,000-result search cap**. A single
  day still over the cap is logged as `DATA LOSS` at ERROR level, because it cannot be split
  further and you need to know.
- Reports marginal yield per title variant, so a variant that earns nothing is visible and can
  be deleted. (`bugfix` and `hotfix` both measured 0 and are already gone.)

Writes `data/raw/skim.jsonl.gz` and `data/raw/cohort.jsonl.gz`.

### `top-engineers hydrate [--limit N]`

Phase 2. Deep-fetches only the PRs the normalisation pool authored or reviewed.

**Resumable.** Kill it at any point and re-run — it reads the checkpoint, dedupes on PR
number, and skips what exists. Tolerates a torn final line from `kill -9`.

```
resuming hydrate: 4000 done, 1136 remaining
```

Asserts byte-stability of the output before deleting the checkpoint; exits non-zero if the
assertion fails. Writes `data/raw/prs.jsonl.gz`.

### `top-engineers score`

Computes all 33 metrics, scores 9, and builds the serving layer. Warns if autonomy coverage
drops below 80% — that would mean the upstream PR template changed and builder metrics are
quietly degrading.

Prints the leaderboard with tie bands and uncertainty intervals.

### `top-engineers restore`

Rebuilds the entire database from committed raw. Without this, committed raw is an audit
trail rather than a restore path.

```bash
TP_DB_PATH=/tmp/rebuilt.duckdb top-engineers restore
```

### `top-engineers verify`

Asserts every committed raw file is byte-stable: re-encodes what is on disk and compares
sha256. A pass means an unchanged upstream produces **zero git diff**.

### `top-engineers candidates`

Regenerates `actors.candidates.yaml`, a **review queue** for identity classification. Promote
entries into `actors.yaml` by hand after opening the profile.

Name-substring hints are included only as prompts. On this repo they produced **10 flags and
10 false positives**, all real employees. Never promote on a hint alone.

## Serving the UI

```bash
uvicorn top_engineers.web.app:app --host 0.0.0.0 --port 8080
```

The health endpoint returns **503 on an empty leaderboard**, not 200. An empty leaderboard
almost always means a misconfigured `TP_DB_PATH` or a database missing from the image, and that
failure is otherwise invisible — the container starts fine and serves a blank page forever.

| Route | Purpose |
|---|---|
| `/` | Leaderboard + drill-down drawer |
| `/api/leaderboard` | Ranked list |
| `/api/person/{login}` | Full metric chain, evidence, how-notes |
| `/_health` | Liveness **and** non-emptiness |
| `/healthz` | Same, but **intercepted by Google's frontend on Cloud Run** — use `/_health` |

## Deploying

```bash
./deploy.sh          # local build + smoke test, then Cloud Build + Cloud Run
```

Live at **https://top-engineers-693674679836.us-central1.run.app** (project `code-impact-dev`,
region `us-central1`, `--allow-unauthenticated --min-instances 1`).

Three deployment traps, each of which produced a container that started cleanly and served
nothing useful:

**`.gcloudignore` must exist.** Without it `gcloud builds submit` falls back to `.gitignore`,
which excludes `data/*.duckdb` — correctly, it is a build artifact for git — so the database is
stripped out of the source upload. `deploy.sh` now refuses to run if `.gcloudignore` is missing
or mentions the database.

**`/healthz` is intercepted by Google's frontend** and returns its own 404 before the request
reaches the container, which makes the empty-leaderboard guard unreachable in production. Use
`/_health`.

**A passing local smoke test does not imply a working deploy.** `.dockerignore` and `.gitignore`
disagree about what ships, so `deploy.sh` verifies the *deployed* URL after rollout rather than
trusting the local container.

The database ships **gzipped** (19MB vs 111MB, under GitHub's 100MB limit) and the Dockerfile
decompresses it at build time, so the running container still performs no startup work.

## Re-running

DuckDB is **single-writer**: stop the server before rebuilding, or the rebuild blocks on the
read lock. (Irrelevant on Cloud Run — the image is immutable and the data is baked in.)

For a reproducible re-run, pin the window:

```bash
TP_UNTIL=2026-09-16 top-engineers skim
```

`raw_pr.updated_at` is stored on every record, so an incremental mode (`updated:>=<last_run>`,
skipping unchanged PRs) can be added without a full re-fetch.

## Tests

```bash
pytest -q          # 112 tests
```

The suite covers the failure modes that cost real time, not just the happy path: `kill -9`
mid-fetch then resume with zero loss and zero duplicates; HTML-with-HTTP-200; truncated JSON;
`RATE_LIMITED` inside a 200 body; the shared token bucket actually being shared; one throttle
episode halving once; byte-stability round-trips; and the two definitional traps
(`ci_first_pass_rate` on the first commit, 14-day revert censoring leaving the denominator).
