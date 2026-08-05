from __future__ import annotations

import json
import multiprocessing
import stat

import pytest

from audit_jsonl import append_bounded_jsonl


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _append_batch(path: str, worker: int) -> None:
    for index in range(50):
        append_bounded_jsonl(
            path, {"worker": worker, "index": index},
            max_bytes=1024 * 1024, backup_count=2, fsync=False)


def test_bounded_append_rotates_and_keeps_only_configured_generations(tmp_path):
    path = tmp_path / "audit.jsonl"
    for index in range(20):
        append_bounded_jsonl(
            path, {"index": index, "payload": "x" * 12},
            max_bytes=100, backup_count=2)

    generations = [path, tmp_path / "audit.jsonl.1", tmp_path / "audit.jsonl.2"]
    assert all(item.exists() for item in generations)
    assert not (tmp_path / "audit.jsonl.3").exists()
    assert all(item.stat().st_size <= 100 for item in generations)
    retained = [row["index"] for item in reversed(generations)
                for row in _rows(item)]
    assert retained == sorted(retained)
    assert retained[-1] == 19
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bounded_append_preserves_a_record_larger_than_the_limit(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_bounded_jsonl(
        path, {"payload": "x" * 1000}, max_bytes=20, backup_count=1)
    assert _rows(path) == [{"payload": "x" * 1000}]


def test_bounded_append_rejects_an_unbounded_policy(tmp_path):
    with pytest.raises(ValueError):
        append_bounded_jsonl(
            tmp_path / "audit.jsonl", {"x": 1},
            max_bytes=0, backup_count=1)


def test_bounded_append_serializes_multiple_processes(tmp_path):
    path = tmp_path / "concurrent.jsonl"
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=_append_batch, args=(str(path), number))
               for number in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    rows = _rows(path)
    assert len(rows) == 200
    assert {(row["worker"], row["index"]) for row in rows} == {
        (worker, index) for worker in range(4) for index in range(50)}
