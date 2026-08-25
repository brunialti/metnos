from __future__ import annotations

import json
import builtins
import importlib.util
import multiprocessing
import os
import stat
from pathlib import Path

import pytest

import audit_jsonl
from audit_jsonl import (
    AuditPathError,
    append_bounded_jsonl,
    append_jsonl,
    append_unique_jsonl,
)


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_module_import_does_not_require_posix_fcntl(monkeypatch):
    import audit_jsonl

    original_import = builtins.__import__

    def without_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ModuleNotFoundError("simulated Windows")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_fcntl)
    spec = importlib.util.spec_from_file_location(
        "audit_jsonl_without_fcntl", Path(audit_jsonl.__file__),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.append_unique_jsonl)


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
    context = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
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


def test_unique_append_deduplicates_a_stable_event_id(tmp_path):
    path = tmp_path / "authorizations.jsonl"
    event = {"event_id": "sha256:stable", "action": "retire"}

    append_unique_jsonl(path, event)
    append_unique_jsonl(path, event)

    assert _rows(path) == [event]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_unique_append_rejects_same_event_id_with_different_payload(tmp_path):
    path = tmp_path / "authorizations.jsonl"
    original = {"event_id": "sha256:stable", "action": "retire"}
    append_unique_jsonl(path, original)

    with pytest.raises(ValueError, match="event_id collision"):
        append_unique_jsonl(
            path,
            {"event_id": "sha256:stable", "action": "reactivate"},
        )

    assert _rows(path) == [original]


def test_unique_append_fails_closed_on_malformed_history(tmp_path):
    path = tmp_path / "authorizations.jsonl"
    path.write_text("not-json\n")

    with pytest.raises(ValueError, match="malformed audit row"):
        append_unique_jsonl(path, {"event_id": "sha256:new"})


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:  # pragma: no cover - Windows privilege dependent
        pytest.skip(f"symlink unavailable: {exc}")


@pytest.mark.parametrize("kind", ("plain", "unique", "bounded"))
def test_audit_writer_rejects_redirected_audit_file(tmp_path, kind):
    target = tmp_path / "unrelated.jsonl"
    target.write_text('{"untouched":true}\n', encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    _symlink_or_skip(audit, target)

    with pytest.raises(AuditPathError, match="audit_path_invalid"):
        if kind == "plain":
            append_jsonl(audit, {"event": "forbidden"})
        elif kind == "unique":
            append_unique_jsonl(audit, {"event_id": "forbidden"})
        else:
            append_bounded_jsonl(
                audit,
                {"event": "forbidden"},
                max_bytes=100,
                backup_count=2,
            )

    assert target.read_text(encoding="utf-8") == '{"untouched":true}\n'


@pytest.mark.parametrize("kind", ("unique", "bounded"))
def test_audit_writer_rejects_redirected_lock_file(tmp_path, kind):
    audit = tmp_path / "audit.jsonl"
    suffix = "unique" if kind == "unique" else "rotation"
    lock = tmp_path / f".audit.jsonl.{suffix}.lock"
    target = tmp_path / "unrelated.lock"
    target.write_bytes(b"untouched")
    _symlink_or_skip(lock, target)

    with pytest.raises(AuditPathError, match="audit_path_invalid"):
        if kind == "unique":
            append_unique_jsonl(audit, {"event_id": "forbidden"})
        else:
            append_bounded_jsonl(
                audit,
                {"event": "forbidden"},
                max_bytes=100,
                backup_count=2,
            )

    assert not audit.exists()
    assert target.read_bytes() == b"untouched"


def test_audit_writer_rejects_redirected_parent(tmp_path):
    target = tmp_path / "unrelated-directory"
    target.mkdir()
    redirected_parent = tmp_path / "redirected"
    _symlink_or_skip(redirected_parent, target, directory=True)

    with pytest.raises(AuditPathError, match="audit_path_invalid"):
        append_unique_jsonl(
            redirected_parent / "audit.jsonl",
            {"event_id": "forbidden"},
        )

    assert list(target.iterdir()) == []


def test_rotation_rejects_redirected_backup_without_mutating_history(tmp_path):
    audit = tmp_path / "audit.jsonl"
    append_bounded_jsonl(
        audit,
        {"index": 1, "payload": "x" * 20},
        max_bytes=100,
        backup_count=2,
    )
    original = audit.read_bytes()
    target = tmp_path / "unrelated-backup.jsonl"
    target.write_bytes(b"untouched")
    _symlink_or_skip(tmp_path / "audit.jsonl.1", target)

    with pytest.raises(AuditPathError, match="audit_path_invalid"):
        append_bounded_jsonl(
            audit,
            {"index": 2, "payload": "x" * 100},
            max_bytes=100,
            backup_count=2,
        )

    assert audit.read_bytes() == original
    assert target.read_bytes() == b"untouched"


def test_rotation_retry_classifier_excludes_generic_access_denied():
    sharing = OSError("sharing")
    sharing.winerror = 32
    denied = PermissionError("denied")
    denied.winerror = 5

    assert audit_jsonl._confirmed_windows_sharing_violation(sharing) is True
    assert audit_jsonl._confirmed_windows_sharing_violation(denied) is False
