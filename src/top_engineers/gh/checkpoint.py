"""Append-only scratch checkpoint with resume.

A fetcher without resume is a prototype. The previous build listed "resume-after-interrupt"
as an acceptance test, shipped without it, and lost 5,000 PRs and ~90 minutes at 65%.

Ordering is load-bearing: RAW JSON IS WRITTEN FIRST, THEN the database. Append-only cannot
half-fail. Reverse the order and you get store rows with no raw record behind them --
corruption you will not notice for weeks.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterator

from ..config import CHECKPOINT_FLUSH_EVERY
from ..rawio import encode_record, iter_jsonl


def load_seen(path: Path, key: str = "number") -> set[Any]:
    """Ids already durably checkpointed. Tolerates a torn final line from an abrupt kill."""
    return {rec[key] for rec in iter_jsonl(path) if key in rec}


def load_records(path: Path, key: str = "number") -> dict[Any, dict[str, Any]]:
    """Deduped records from the scratch file, last write winning."""
    out: dict[Any, dict[str, Any]] = {}
    for rec in iter_jsonl(path):
        if key in rec:
            out[rec[key]] = rec
    return out


class Checkpoint:
    """Buffered append-only writer.

    Use as a context manager. ``__exit__`` flushes on BaseException -- NOT Exception --
    because Ctrl-C raises KeyboardInterrupt, which is exactly the interrupt that matters.
    """

    def __init__(
        self,
        path: Path,
        key: str = "number",
        flush_every: int = CHECKPOINT_FLUSH_EVERY,
        on_flush: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self.path = path
        self.key = key
        self.flush_every = flush_every
        self.on_flush = on_flush
        self._buf: list[dict[str, Any]] = []
        self._fh = None
        self.seen: set[Any] = set()
        self.written = 0

    def __enter__(self) -> "Checkpoint":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seen = load_seen(self.path, self.key)
        self._fh = self.path.open("a", encoding="utf-8")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # Flush before propagating. Losing the buffer on the way out defeats the point.
        try:
            self.flush()
        finally:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
        return False  # never swallow

    def has(self, record_id: Any) -> bool:
        return record_id in self.seen

    def add(self, record: dict[str, Any]) -> bool:
        """Buffer a record. Returns False if it was already checkpointed."""
        rid = record.get(self.key)
        if rid is not None and rid in self.seen:
            return False
        self._buf.append(record)
        if rid is not None:
            self.seen.add(rid)
        if len(self._buf) >= self.flush_every:
            self.flush()
        return True

    def extend(self, records: list[dict[str, Any]]) -> int:
        return sum(1 for r in records if self.add(r))

    def flush(self) -> None:
        """Write raw first, then hand the batch to the store."""
        if not self._buf:
            return
        if self._fh is None:
            raise RuntimeError("Checkpoint used outside its context manager")
        batch, self._buf = self._buf, []
        for rec in batch:
            self._fh.write(encode_record(rec) + "\n")
        self._fh.flush()
        self.written += len(batch)
        # Only now is it safe to load the store: the raw record already exists on disk.
        if self.on_flush is not None:
            self.on_flush(batch)


def finalize(scratch: Path) -> None:
    """Drop the scratch file. Call ONLY after canonical sorted files are written."""
    scratch.unlink(missing_ok=True)


def iter_checkpoint(path: Path) -> Iterator[dict[str, Any]]:
    yield from iter_jsonl(path)
