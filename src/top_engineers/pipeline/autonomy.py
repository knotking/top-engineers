"""Parse PostHog's self-declared autonomy field.

CHECK FOR STRUCTURED SELF-DECLARATION BEFORE BUILDING ANY INFERENCE. The PR template carries
``**Autonomy:** Fully autonomous | Human-driven (agent-assisted)`` and it is measured at ~86%
body coverage. No heuristic came close to this signal.

The trap: an author who never edited the template leaves BOTH options in the body. A naive
first-match parser reads that as human_driven. It must be `unknown`, so "both present" is
tested before either option individually -- order matters here.
"""

from __future__ import annotations

import re

FULLY_AUTONOMOUS = "fully_autonomous"
HUMAN_DRIVEN = "human_driven"
UNKNOWN = "unknown"

_AUTONOMY_LINE = re.compile(r"autonomy\s*[:\-]?\s*(.+)", re.IGNORECASE)
_FULLY = re.compile(r"fully\s+autonomous", re.IGNORECASE)
_HUMAN = re.compile(r"human[-\s]?(driven|directed)", re.IGNORECASE)
# The unedited template keeps both options joined by a separator: "|", "or", "-or-".
_TEMPLATE_SEP = re.compile(r"\s*(\||\bor\b|/)\s*", re.IGNORECASE)


def parse_autonomy(body: str | None) -> str:
    if not body:
        return UNKNOWN

    match = _AUTONOMY_LINE.search(body)
    if not match:
        return UNKNOWN

    # Only the remainder of the declaring line; the next line is a different template field.
    tail = match.group(1).split("\n", 1)[0].strip()
    if not tail:
        return UNKNOWN

    has_fully = bool(_FULLY.search(tail))
    has_human = bool(_HUMAN.search(tail))

    # Both options present => the template was never filled in. Not a classification.
    if has_fully and has_human:
        return UNKNOWN
    # A truncated tail still counts as unedited if a separator hints at the dropped option
    # (e.g. "Human-driven (agent-assisted) - or - Fully au" cut off by body truncation).
    if (has_fully or has_human) and _looks_truncated_template(tail):
        return UNKNOWN

    if has_fully:
        return FULLY_AUTONOMOUS
    if has_human:
        return HUMAN_DRIVEN
    return UNKNOWN


def _looks_truncated_template(tail: str) -> bool:
    """True when a separator is followed by a dangling fragment of the other option."""
    parts = [p for p in _TEMPLATE_SEP.split(tail) if p and not _TEMPLATE_SEP.fullmatch(p)]
    if len(parts) < 2:
        return False
    # Splitting on the separator can leave dangling punctuation ("- Fully au"), which would
    # defeat the prefix comparison below.
    fragment = parts[-1].strip().strip("-*_. \t").lower()
    if not fragment:
        return False
    # "fully au", "human-dri" -- a prefix of the opposing option that never completed.
    for option in ("fully autonomous", "human-driven", "human driven"):
        if len(fragment) >= 3 and option.startswith(fragment[: len(option)]) and fragment != option:
            return True
    return False


def coverage(bodies: list[str | None]) -> float:
    if not bodies:
        return 0.0
    declared = sum(1 for b in bodies if parse_autonomy(b) != UNKNOWN)
    return declared / len(bodies)
