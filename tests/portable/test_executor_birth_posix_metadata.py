"""Contract tests for the shared, read-only POSIX metadata owner."""
from __future__ import annotations

import ast
from dataclasses import replace
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

import executor_birth_posix_metadata as metadata


def _stat(**changes) -> SimpleNamespace:
    values = {
        "st_dev": 11,
        "st_ino": 12,
        "st_mode": 0o100644,
        "st_nlink": 1,
        "st_uid": 1001,
        "st_gid": 1002,
        "st_size": 23,
        "st_mtime_ns": 24,
        "st_ctime_ns": 25,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _snapshot() -> metadata.PosixStatSnapshotV1:
    return metadata.snapshot_stat_v1(_stat())  # type: ignore[arg-type]


def test_stat_field_mapping_is_exact() -> None:
    assert _snapshot() == metadata.PosixStatSnapshotV1(
        device=11,
        inode=12,
        mode=0o100644,
        link_count=1,
        uid=1001,
        gid=1002,
        size=23,
        mtime_ns=24,
        ctime_ns=25,
    )


@pytest.mark.parametrize(
    "field", (
        "device", "inode", "mode", "link_count", "uid", "gid", "size",
        "mtime_ns", "ctime_ns",
    ),
)
def test_complete_snapshot_detects_every_field_change(field: str) -> None:
    original = _snapshot()
    assert replace(original, **{field: getattr(original, field) + 1}) != original


@pytest.mark.parametrize(
    "field", ("device", "inode", "mode", "link_count", "uid", "gid", "size"),
)
def test_stable_metadata_detects_each_non_timestamp_change(field: str) -> None:
    original = _snapshot()
    changed = replace(original, **{field: getattr(original, field) + 1})
    assert not original.same_stable_metadata_as(changed)


@pytest.mark.parametrize("field", ("mtime_ns", "ctime_ns"))
def test_stable_metadata_deliberately_ignores_timestamps(field: str) -> None:
    original = _snapshot()
    changed = replace(original, **{field: getattr(original, field) + 1})
    assert original.same_stable_metadata_as(changed)


def test_object_identity_uses_only_device_and_inode() -> None:
    original = _snapshot()
    assert original.same_object_as(replace(original, mode=original.mode + 1))
    assert not original.same_object_as(replace(original, device=12))
    assert not original.same_object_as(replace(original, inode=13))
    assert not original.same_object_as(original.object_key)


def test_snapshot_fd_matches_real_fstat(tmp_path: Path) -> None:
    target = tmp_path / "metadata.txt"
    target.write_bytes(b"executor-birth")
    descriptor = os.open(target, os.O_RDONLY)
    try:
        observed = metadata.snapshot_fd_v1(descriptor)
        direct = metadata.snapshot_stat_v1(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    assert observed == direct
    assert observed.size == len(b"executor-birth")
    assert observed.object_key == metadata.PosixObjectKeyV1(
        direct.device, direct.inode,
    )


def test_consumers_do_not_import_receiver_identity_privates() -> None:
    repository = Path(__file__).resolve().parents[2]
    forbidden = {"_identity", "_stable_identity"}
    violations = []
    for root_name in ("install", "runtime"):
        for source_path in (repository / root_name).rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module in {
                        "executor_birth_source_receiver",
                        "install.executor_birth_source_receiver",
                    }
                ):
                    imported = forbidden.intersection(alias.name for alias in node.names)
                    if imported:
                        violations.append((source_path.name, sorted(imported)))
    assert violations == []


def test_import_is_pure_and_size_limits_are_enforced(tmp_path: Path) -> None:
    before_environment = dict(os.environ)
    before_entries = tuple(tmp_path.iterdir())
    source_path = Path(metadata.__file__)
    namespace = runpy.run_path(source_path)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_sizes = [
        node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert "snapshot_stat_v1" in namespace
    assert dict(os.environ) == before_environment
    assert tuple(tmp_path.iterdir()) == before_entries
    assert len(source.splitlines()) <= 200
    assert max(function_sizes) <= 30
