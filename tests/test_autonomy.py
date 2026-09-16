"""Autonomy parsing. The structured self-declaration no heuristic matched."""

from __future__ import annotations

import pytest

from top_engineers.pipeline.autonomy import (
    FULLY_AUTONOMOUS,
    HUMAN_DRIVEN,
    UNKNOWN,
    coverage,
    parse_autonomy,
)


@pytest.mark.parametrize("body,expected", [
    ("Autonomy: Fully autonomous", FULLY_AUTONOMOUS),
    ("**Autonomy:** Fully autonomous", FULLY_AUTONOMOUS),
    ("Autonomy: Human-driven (agent-assisted)", HUMAN_DRIVEN),
    ("Autonomy: Human-driven (agent-assisted).", HUMAN_DRIVEN),
    ("Autonomy: Human-directed agent work.", HUMAN_DRIVEN),
    ("no autonomy field at all", UNKNOWN),
    ("", UNKNOWN),
    (None, UNKNOWN),
    ("Autonomy:", UNKNOWN),
])
def test_basic_parsing(body, expected):
    assert parse_autonomy(body) == expected


@pytest.mark.parametrize("body", [
    "Autonomy: Fully autonomous | Human-driven (agent-assisted)",
    "**Autonomy:** Human-driven (agent-assisted) - or - Fully autonomous",
    "Autonomy: Human-driven (agent-assisted) - or - Fully au",
    "Autonomy: Fully autonomous / Human-dri",
    "Autonomy: Fully autonomous or Human-driven (agent-assisted)",
])
def test_unedited_template_is_unknown(body):
    """BOTH options present means the author never filled it in.

    A naive first-match parser reads this as human_driven and silently misattributes the PR.
    """
    assert parse_autonomy(body) == UNKNOWN


def test_only_the_declaring_line_is_read():
    """The next template field must not bleed into the autonomy value."""
    body = "Autonomy: Fully autonomous\nRisk: Human-driven rollback plan"
    assert parse_autonomy(body) == FULLY_AUTONOMOUS


def test_coverage_helper():
    bodies = ["Autonomy: Fully autonomous", "Autonomy: Human-driven (agent-assisted)", "nothing"]
    assert coverage(bodies) == pytest.approx(2 / 3)
    assert coverage([]) == 0.0
