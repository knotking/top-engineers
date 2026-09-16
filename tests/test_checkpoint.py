"""Checkpoint + resume. The acceptance test IS the deliverable here, not a checkbox."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

import pytest

from top_engineers.gh.checkpoint import Checkpoint, load_seen
from top_engineers.rawio import iter_jsonl


def test_flush_every_n(tmp_path):
    p = tmp_path / "c.jsonl"
    with Checkpoint(p, flush_every=3) as c:
        for i in range(3):
            c.add({"number": i})
        assert p.read_text().count("\n") == 3, "should flush at the threshold"


def test_torn_final_line_is_not_fatal(tmp_path):
    p = tmp_path / "c.jsonl"
    with Checkpoint(p, flush_every=1) as c:
        for i in range(4):
            c.add({"number": i})
    p.write_text(p.read_text() + '{"number":99,"tor')
    assert load_seen(p) == {0, 1, 2, 3}
    assert len(list(iter_jsonl(p))) == 4


def test_resume_dedupes_on_pr_number(tmp_path):
    p = tmp_path / "c.jsonl"
    with Checkpoint(p, flush_every=2) as c:
        for i in range(5):
            c.add({"number": i})
    with Checkpoint(p) as c:
        assert c.add({"number": 3}) is False
        assert c.add({"number": 42}) is True


def test_keyboard_interrupt_flushes_buffer(tmp_path):
    """BaseException, not Exception -- Ctrl-C is the interrupt that matters."""
    p = tmp_path / "c.jsonl"
    with pytest.raises(KeyboardInterrupt):
        with Checkpoint(p, flush_every=250) as c:
            for i in range(10):
                c.add({"number": i})
            raise KeyboardInterrupt
    assert load_seen(p) == set(range(10))


def test_raw_written_before_store_load(tmp_path):
    """Ordering invariant: a store row must never exist without raw behind it."""
    p = tmp_path / "c.jsonl"
    observed = []

    def on_flush(batch):
        # At this moment the raw file must ALREADY contain these records.
        observed.append(load_seen(p))

    with Checkpoint(p, flush_every=2, on_flush=on_flush) as c:
        for i in range(4):
            c.add({"number": i})
    assert observed and observed[0] == {0, 1}


def test_kill_9_midrun_then_resume(tmp_path):
    """Hard kill mid-fetch, resume, assert zero duplicates and zero loss.

    This is the test the previous build listed and shipped without, then lost 5,000 PRs and
    ~90 minutes at 65% for want of.
    """
    src = str(pathlib.Path(__file__).resolve().parents[1] / "src")
    target = tmp_path / "c.jsonl"
    script = tmp_path / "run.py"
    script.write_text(
        "import sys, time, pathlib\n"
        f"sys.path.insert(0, {src!r})\n"
        "from top_engineers.gh.checkpoint import Checkpoint\n"
        f"with Checkpoint(pathlib.Path({str(target)!r}), flush_every=5) as c:\n"
        "    for i in range(1000):\n"
        "        c.add({'number': i})\n"
        "        time.sleep(0.01)\n"
    )

    proc = subprocess.Popen([sys.executable, str(script)])
    time.sleep(1.5)
    proc.kill()          # SIGKILL: no handler, no flush, possibly a torn line
    proc.wait()

    partial = load_seen(target)
    assert partial, "should have flushed something before the kill"
    assert len(partial) < 1000, "should not have finished"

    with Checkpoint(target, flush_every=5) as c:
        for i in range(1000):
            c.add({"number": i})

    after = [r["number"] for r in iter_jsonl(target)]
    assert len(after) == len(set(after)), "resume must not duplicate"
    assert set(after) == set(range(1000)), "resume must not lose"
