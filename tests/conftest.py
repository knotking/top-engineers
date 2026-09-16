"""Shared test configuration.

Tests must never read the operator's real config: TP_* env vars are cleared so a developer
with a populated data/ directory sees the same results as a clean checkout.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    for var in ("TP_DATA_DIR", "TP_DB_PATH", "TP_ACTORS_PATH", "TP_WINDOW_DAYS", "TP_UNTIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TP_UNTIL", "2026-09-16")
