"""Byte-stable canonical raw files.

Byte-stability and crash-safety are irreconcilable in one file: the first needs everything
sorted (write at the end), the second needs unsorted append (write as you go). So there are
two files -- an append-only scratch checkpoint (``gh.checkpoint``) and the canonical sorted
``.jsonl.gz`` written here at the end.

An unchanged upstream must produce ZERO git diff. Three things are required for that, and
``mtime=0`` is the one people miss -- gzip embeds the current time in its header by default.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Iterable, Sequence


def encode_record(record: dict[str, Any]) -> str:
    """Deterministic single-line JSON. Sorted keys, no incidental whitespace."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def encode_jsonl_gz(records: Sequence[dict[str, Any]], sort_key: str = "number") -> bytes:
    """Encode records to deterministic gzipped JSONL bytes.

    Sorting by ``sort_key`` makes record ORDER stable; ``sort_keys`` makes each record's
    field order stable; ``mtime=0`` makes the gzip HEADER stable. All three are needed.
    """
    ordered = sorted(records, key=lambda r: _sort_value(r, sort_key))
    body = "".join(encode_record(r) + "\n" for r in ordered).encode("utf-8")
    buf = io.BytesIO()
    # compresslevel is pinned: the default is stable today but is not part of gzip's contract.
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as fh:
        fh.write(body)
    return buf.getvalue()


def _sort_value(record: dict[str, Any], sort_key: str) -> tuple[int, Any]:
    value = record.get(sort_key)
    if value is None:
        return (1, "")
    if isinstance(value, (int, float)):
        return (0, value)
    return (0, str(value))


def write_jsonl_gz(path: Path, records: Sequence[dict[str, Any]], sort_key: str = "number") -> str:
    """Write canonically and return the sha256 of the bytes on disk."""
    payload = encode_jsonl_gz(records, sort_key=sort_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)
    return hashlib.sha256(payload).hexdigest()


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def verify_byte_stability(path: Path, sort_key: str = "number") -> bool:
    """Round-trip assertion: re-encode what is on disk and compare sha256 against it.

    A pass means an unchanged upstream produces zero git diff. This is an assertion about
    the ENCODER, so it must re-encode rather than just re-hash the file.
    """
    if not path.exists():
        return False
    on_disk = hashlib.sha256(path.read_bytes()).hexdigest()
    re_encoded = hashlib.sha256(encode_jsonl_gz(read_jsonl_gz(path), sort_key=sort_key)).hexdigest()
    return on_disk == re_encoded


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Plain (uncompressed) JSONL reader tolerant of a torn final line."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # A torn final line is the NORMAL result of kill -9, not a fatal error.
                continue
