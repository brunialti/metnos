"""Catalog admission must distinguish invocation statistics from lifecycle."""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import executor_aging
import loader
import pytest

from tests.runtime.infra.test_loader_contract_store import (
    _activate_published_store,
    _point_loader_at,
    _source,
)


@pytest.fixture
def catalog_source(tmp_path, monkeypatch):
    source_root, ref, trusted = _source(tmp_path)
    state, _generation = _activate_published_store(tmp_path, ref, trusted)
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    monkeypatch.setattr(executor_aging, "DB_PATH", tmp_path / "statistics.sqlite")
    executor_aging.register("read_files")
    yield source_root
    loader.invalidate_catalog_cache()


def _load(source_root):
    return loader.load_catalog(
        executors_dir=source_root, verify=True,
        include_synth=False, include_verb_unique=False, lang="en",
    )


def test_invocation_during_authentication_does_not_destabilize_catalog(
    catalog_source, monkeypatch,
):
    original = loader._load_store_into_catalog
    loads = []

    def authenticate_then_record_invocation(*args, **kwargs):
        original(*args, **kwargs)
        # An ordinary HTTP/Telegram/other LRE invocation completes during
        # authentication. Its accounting is not a contract/visibility change.
        executor_aging.touch("read_files", ok=True)
        loads.append(True)

    monkeypatch.setattr(
        loader, "_load_store_into_catalog", authenticate_then_record_invocation,
    )
    assert _load(catalog_source).get("read_files") is not None
    assert loads == [True]


def test_invocation_preserves_authenticated_cache(catalog_source):
    first = _load(catalog_source)
    executor_aging.touch("read_files", ok=True)
    assert _load(catalog_source) is first


def test_parallel_catalog_readers_survive_other_lane_accounting(
    catalog_source, monkeypatch,
):
    original = loader._load_store_into_catalog
    rendezvous = threading.Barrier(4, timeout=5)

    def authenticate_with_concurrent_accounting(*args, **kwargs):
        original(*args, **kwargs)
        if rendezvous.wait() == 0:
            executor_aging.touch("read_files", ok=True)
        rendezvous.wait()

    monkeypatch.setattr(
        loader, "_load_store_into_catalog", authenticate_with_concurrent_accounting,
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_load, catalog_source) for _ in range(4)]
        catalogs = [future.result(timeout=10) for future in futures]
    assert all(catalog.get("read_files") is not None for catalog in catalogs)


def test_unreadable_lifecycle_does_not_reuse_cached_catalog(catalog_source):
    first = _load(catalog_source)
    assert first.get("read_files") is not None
    executor_aging.DB_PATH.write_bytes(b"not a statistics database")
    with pytest.raises(sqlite3.DatabaseError):
        _load(catalog_source)


@pytest.mark.parametrize("audit_only", [False, True])
def test_verified_lifecycle_snapshot_needs_no_fallible_third_read(
    catalog_source, monkeypatch, audit_only,
):
    reads = []

    def lifecycle(*, read_only=False):
        reads.append(read_only)
        if len(reads) > 2:
            raise sqlite3.OperationalError("database is locked")
        return {"read_files": "archived"}

    monkeypatch.setattr(executor_aging, "lifecycle_override_map", lifecycle)
    if audit_only:
        monkeypatch.setattr(
            executor_aging, "register",
            lambda *_args, **_kwargs: pytest.fail("audit registered an executor"),
        )
        catalog = loader._load_catalog_for_cutover_audit_v1(
            catalog_trusted_owner=None,
            trusted_publics=tuple(loader.list_trusted_publics()),
            _executors_dir=catalog_source, _include_synth=False, _lang="en",
        )
    else:
        catalog = _load(catalog_source)
    assert catalog.get("read_files") is None
    assert reads == [True, True]


def test_lifecycle_applied_is_the_snapshot_bound_to_cache_signature(
    catalog_source, monkeypatch,
):
    state = {"read_files": "archived"}
    original_affinity = loader._check_affinity_overlap

    def change_after_authentication(*args, **kwargs):
        original_affinity(*args, **kwargs)
        # A lifecycle writer changes state after the stable before/after
        # snapshot, but before the catalog applies that visibility snapshot.
        state.clear()

    monkeypatch.setattr(
        executor_aging, "lifecycle_override_map",
        lambda *, read_only=False: dict(state),
    )
    monkeypatch.setattr(
        loader, "_check_affinity_overlap", change_after_authentication,
    )
    first = _load(catalog_source)
    assert first.get("read_files") is None
    second = _load(catalog_source)
    assert second is not first
    assert second.get("read_files") is not None


@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_lifecycle_change_invalidates_cache_with_unchanged_database_mtime(
    catalog_source, journal_mode,
):
    import os

    connection = sqlite3.connect(executor_aging.DB_PATH)
    try:
        connection.execute(f"PRAGMA journal_mode={journal_mode}")
        first = _load(catalog_source)
        assert first.get("read_files") is not None
        before = executor_aging.DB_PATH.stat()
        connection.execute(
            "UPDATE executor_stats SET archived_at='2026-01-01' WHERE name=?",
            ("read_files",),
        )
        connection.commit()
        os.utime(
            executor_aging.DB_PATH,
            ns=(before.st_atime_ns, before.st_mtime_ns),
        )
        second = _load(catalog_source)
        assert second is not first
        assert second.get("read_files") is None
    finally:
        connection.close()


@pytest.mark.parametrize("field", ["archived_at", "deprecated_at"])
def test_removing_lifecycle_override_invalidates_cache_under_wal(
    catalog_source, field,
):
    connection = sqlite3.connect(executor_aging.DB_PATH)
    try:
        connection.execute("PRAGMA journal_mode=wal")
        connection.execute(
            f"UPDATE executor_stats SET {field}='2026-01-01' WHERE name=?",
            ("read_files",),
        )
        connection.commit()
        first = _load(catalog_source)
        if field == "archived_at":
            assert first.get("read_files") is None
        else:
            assert first.get("read_files").lifecycle == "deprecated"
        connection.execute(
            f"UPDATE executor_stats SET {field}=NULL WHERE name=?",
            ("read_files",),
        )
        connection.commit()
        second = _load(catalog_source)
        assert second is not first
        assert second.get("read_files").lifecycle == "active"
    finally:
        connection.close()
