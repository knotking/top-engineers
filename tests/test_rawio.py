"""Byte-stability: an unchanged upstream must produce ZERO git diff."""

from __future__ import annotations

import gzip
import hashlib

from top_engineers.rawio import (
    encode_jsonl_gz,
    encode_record,
    read_jsonl_gz,
    verify_byte_stability,
    write_jsonl_gz,
)


def test_encoding_is_order_independent():
    recs = [{"number": 2, "b": 1, "a": 2}, {"number": 1, "z": "x"}]
    assert encode_jsonl_gz(recs) == encode_jsonl_gz(list(reversed(recs)))


def test_record_keys_are_sorted_and_compact():
    assert encode_record({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_gzip_mtime_is_zeroed():
    """gzip embeds the current time in its header by default -- the one people miss."""
    payload = encode_jsonl_gz([{"number": 1}])
    # bytes 4..8 of a gzip header are MTIME
    assert payload[4:8] == b"\x00\x00\x00\x00"


def test_repeated_encode_is_identical():
    recs = [{"number": i, "v": f"x{i}"} for i in range(50)]
    a = hashlib.sha256(encode_jsonl_gz(recs)).hexdigest()
    b = hashlib.sha256(encode_jsonl_gz(recs)).hexdigest()
    assert a == b


def test_hash_round_trip_assertion(tmp_path):
    path = tmp_path / "prs.jsonl.gz"
    recs = [{"number": i, "title": f"fix: {i}"} for i in range(20)]
    write_jsonl_gz(path, recs)
    assert verify_byte_stability(path)
    assert read_jsonl_gz(path) == sorted(recs, key=lambda r: r["number"])


def test_rewrite_produces_identical_bytes(tmp_path):
    """The actual git-diff property."""
    path = tmp_path / "prs.jsonl.gz"
    recs = [{"number": i} for i in range(10)]
    write_jsonl_gz(path, recs)
    before = path.read_bytes()
    write_jsonl_gz(path, list(reversed(recs)))
    assert path.read_bytes() == before
