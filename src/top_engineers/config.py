"""Runtime configuration.

DB_PATH must never be derived from ``__file__.parents[2]``. Once pip-installed that
resolves to site-packages' grandparent, the container starts fine, and the app serves an
empty leaderboard forever. Env overrides are the contract; the Dockerfile sets them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

REPO_OWNER = "PostHog"
REPO_NAME = "posthog"

# Bot authors measured in-window on 2026-09-16. Bot accounts REQUIRE the ``app/`` prefix in
# search: ``author:posthog-js-upgrader`` returns 0 while ``author:app/posthog-js-upgrader``
# returns 165. Getting this wrong silently disables the exclusion.
# ``app/trunk-io`` measured 0 and is deliberately absent -- delete what earns nothing.
BOT_AUTHORS = (
    "app/posthog",
    "app/posthog-js-upgrader",
    "app/scheduled-actions-posthog",
)

# ``in:title bugfix`` and ``in:title hotfix`` both measured 0 and are deliberately absent.
TITLE_VARIANTS = ("fix", "revert")

# Tuned for the MEDIAN, not the max: measured 0.560 points/PR against 1.56 untuned. This is
# what keeps the whole hydration inside a single 5,000-point window.
PAGE_FILES = 30
PAGE_COMMITS = 30
PAGE_REVIEWS = 20
PAGE_REVIEW_THREADS = 20
PAGE_COMMENTS = 30  # was 10: measured 12 of 14 overflow drains were comments

HYDRATE_BATCH = 25  # one GraphQL alias per PR; there is no bulk pullRequest(numbers:)
SKIM_PAGE = 100
CHECKPOINT_FLUSH_EVERY = 250
BATCH_REQUEUE_LIMIT = 2  # re-queue a failed batch rather than silently dropping it
SEARCH_RESULT_CAP = 1000  # GitHub caps search SILENTLY -- no error, no warning

MIN_INTERVAL_S = 0.8
CONCURRENCY_START = 3
CONCURRENCY_MAX = 8
# One throttling episode halves once. Must exceed worst-case RTT so that every request
# already in flight when the burst lands is attributed to the same episode.
THROTTLE_COOLDOWN_S = 15.0

REVERT_CLONE_TIMEBOX_S = 600  # revert_rate is 6.45% and renormalises away; do not over-invest

# Scoring
SHRINK_PRIOR_K = 5
GATE_MIN_MERGED_PRS = 5
GATE_MIN_REVIEWS = 10
WINSOR_LO, WINSOR_HI = 0.05, 0.95
REVERT_CENSOR_DAYS = 14
RATIO_BAND = (0.5, 2.0)

# Percentiles over 10 people are coarse buckets and the shrinkage prior gets noisy, so
# normalise against a wider pool and display only the head of it.
NORMALISE_POOL_SIZE = 50
DISPLAY_SIZE = 10


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Config:
    data_dir: Path = field(default_factory=lambda: _env_path("TP_DATA_DIR", Path.cwd() / "data"))
    window_days: int = int(os.environ.get("TP_WINDOW_DAYS", "90"))
    until: date = field(
        default_factory=lambda: date.fromisoformat(os.environ["TP_UNTIL"])
        if os.environ.get("TP_UNTIL")
        else date.today()
    )
    token: str | None = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN"))

    @property
    def since(self) -> date:
        return self.until - timedelta(days=self.window_days)

    @property
    def db_path(self) -> Path:
        return _env_path("TP_DB_PATH", self.data_dir / "top_engineers.duckdb")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def checkpoint_dir(self) -> Path:
        return self.data_dir / "checkpoint"

    @property
    def actors_path(self) -> Path:
        return _env_path("TP_ACTORS_PATH", Path.cwd() / "actors.yaml")

    def ensure_dirs(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    return Config()
