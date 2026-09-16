"""Identity resolution. Get this wrong and 40% of the data is misattributed.

``actors.yaml`` is a HUMAN-REVIEWED artifact, not a helper function. Two facts drive the
design:

  * The ``[bot]`` suffix and GitHub's ``Bot`` account type MISS the real cases. ``stamphog``
    is a USER account posting automated approvals detailed enough to top every
    reviewer-quality metric.
  * Substring nets ("bot", "ci", "hog") produced 10 flags, ALL 10 false positives -- real
    employees. So the net is a prompt for human review, NEVER a classifier.

Accordingly: `is_human` consults only the reviewed file plus hard structural signals.
Candidate generation writes a SEPARATE file that a human promotes by hand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

log = logging.getLogger(__name__)

# Used ONLY to build a human review queue. Never consulted by is_human().
REVIEW_HINTS = ("bot", "ci", "hog", "auto", "deploy", "release", "sync", "stamp")


@dataclass
class Actors:
    bots: set[str] = field(default_factory=set)
    humans: set[str] = field(default_factory=set)
    aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Actors":
        if not path.exists():
            log.warning(
                "%s missing: falling back to structural bot signals only. Automation on USER "
                "accounts (e.g. stamphog) will be scored as human until reviewed.",
                path,
            )
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls(
            bots={str(b).lower() for b in (data.get("bots") or [])},
            humans={str(h).lower() for h in (data.get("humans") or [])},
            aliases={str(k).lower(): str(v) for k, v in (data.get("aliases") or {}).items()},
        )

    def canonical(self, login: str) -> str:
        return self.aliases.get(login.lower(), login)

    def is_bot(self, login: str | None, account_type: str | None = None) -> bool:
        if not login:
            return True
        key = login.lower()
        # The reviewed file always wins, in both directions.
        if key in self.bots:
            return True
        if key in self.humans:
            return False
        # Structural signals only -- no substring guessing.
        if account_type == "Bot":
            return True
        return key.endswith("[bot]")

    def is_human(self, login: str | None, account_type: str | None = None) -> bool:
        return not self.is_bot(login, account_type)


def build_candidates(
    logins: Iterable[tuple[str, str | None, int]],
    actors: Actors,
) -> list[dict[str, Any]]:
    """Generate a REVIEW QUEUE, not a classification.

    ``logins`` is (login, account_type, activity_count). Output is suggestions with evidence
    for a human to promote into actors.yaml.
    """
    out: list[dict[str, Any]] = []
    for login, account_type, count in logins:
        key = login.lower()
        if key in actors.bots or key in actors.humans:
            continue  # already reviewed
        reasons = []
        if account_type == "Bot":
            reasons.append("github_account_type=Bot")
        if key.endswith("[bot]"):
            reasons.append("login_suffix=[bot]")
        hints = [h for h in REVIEW_HINTS if h in key]
        if hints:
            reasons.append(f"name_hint={','.join(hints)} (UNRELIABLE: 10/10 false positives)")
        if not reasons:
            continue
        out.append({
            "login": login,
            "account_type": account_type,
            "activity": count,
            "reasons": reasons,
            "profile": f"https://github.com/{login}",
            "structural": account_type == "Bot" or key.endswith("[bot]"),
        })
    # Structural signals first; name hints are noise and sort last.
    out.sort(key=lambda r: (not r["structural"], -r["activity"]))
    return out


def write_candidates(path: Path, candidates: list[dict[str, Any]]) -> None:
    header = (
        "# GENERATED -- a review queue, not a classification.\n"
        "# Promote entries by hand into actors.yaml. Name hints below produced 10/10 false\n"
        "# positives on real employees; treat them as a prompt, never as evidence.\n"
        "# Automation on USER accounts (stamphog-class) will NOT appear here at all -- that\n"
        "# is precisely what human review is for.\n"
    )
    path.write_text(header + yaml.safe_dump({"candidates": candidates}, sort_keys=False))
