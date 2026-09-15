from __future__ import annotations

import json
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from durable_workloads.inventory import InventoryLimits
from durable_workloads.models import ExecutionContext, SourceResolution
from durable_workloads.source_authority import SourceAuthority, SourceAuthorityError


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _context(owner: str, workload: str) -> ExecutionContext:
    return ExecutionContext(
        owner_user_id=owner,
        workload_id=workload,
        revision_id="revision-test",
        stage_id="stage-test",
        unit_key="unit-test",
        attempt_id="attempt-test",
        priority="normal",
        resource_claims=(),
        deadline_at=None,
    )


def _limits() -> InventoryLimits:
    return InventoryLimits(max_sources=10, max_total_bytes=1024 * 1024, max_depth=4)


def test_local_authority_is_private_owner_scoped_and_rehashes(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    source_path = root / "question.png"
    source_path.write_bytes(b"sealed-image")
    database = tmp_path / "private" / "source-authority.sqlite3"

    with SourceAuthority.open(database, clock=lambda: NOW) as authority:
        inventory = authority.seal_and_register(
            [root],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )
        source = inventory["sources"][0]
        assert str(source_path) not in json.dumps(inventory)

        resolution = authority.resolve(source, _context("owner-a", "workload-a"))
        resolved_path = Path(resolution.value)
        assert resolved_path != source_path
        assert resolved_path.suffix == source_path.suffix
        assert resolved_path.read_bytes() == b"sealed-image"
        assert stat.S_IMODE(resolved_path.stat().st_mode) == 0o400
        assert resolution.source_id == source["source_id"]
        assert resolution.authority == "local-source-registry-v1"

        with pytest.raises(SourceAuthorityError, match="unavailable"):
            authority.resolve(source, _context("owner-b", "workload-a"))
        with pytest.raises(SourceAuthorityError, match="unavailable"):
            authority.resolve(source, _context("owner-a", "workload-b"))

        source_path.write_bytes(b"changed-image")
        assert resolved_path.read_bytes() == b"sealed-image"
        with pytest.raises(SourceAuthorityError):
            authority.resolve(source, _context("owner-a", "workload-a"))

    assert not resolved_path.exists()
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_local_authority_keeps_at_most_one_snapshot_per_lane(tmp_path):
    path = tmp_path / "source.png"
    path.write_bytes(b"stable")
    database = tmp_path / "private" / "authority.sqlite3"
    with SourceAuthority.open(database, clock=lambda: NOW) as authority:
        source = authority.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )["sources"][0]
        first = Path(authority.resolve(
            source, _context("owner-a", "workload-a"),
        ).value)
        second = Path(authority.resolve(
            source, _context("owner-a", "workload-a"),
        ).value)
        assert first == second
        assert second.read_bytes() == b"stable"
        assert len(authority._snapshot_files) == 1


@pytest.mark.parametrize("recursive,expected", [(False, 1), (True, 2)])
def test_selection_precedes_source_limits_and_authority_grants(tmp_path, recursive, expected):
    root = tmp_path / "input"
    root.mkdir()
    (root / "a.png").write_bytes(b"first")
    (root / "ignored.txt").write_bytes(b"not selected")
    (root / "nested").mkdir()
    (root / "nested" / "b.PNG").write_bytes(b"second")
    with SourceAuthority.open(tmp_path / "authority.sqlite3", clock=lambda: NOW) as authority:
        inventory = authority.seal_and_register(
            [root], owner_user_id="owner", workload_id="workload", device_id="server",
            limits=InventoryLimits(max_sources=expected, max_total_bytes=11, max_depth=4),
            valid_until=NOW + timedelta(days=1), recursive=recursive,
            accept=lambda path: path.suffix.casefold() == ".png",
        )
        assert len(inventory["sources"]) == expected
        assert authority._connection.execute("SELECT count(*) FROM source_grants").fetchone()[0] == expected
        for item in inventory["sources"]:
            resolved = authority.resolve(item, _context("owner", "workload"))
            assert Path(resolved.value).suffix.casefold() == ".png"


def test_named_source_boundaries_are_ordered_and_registration_rolls_back(
    tmp_path,
):
    source_path = tmp_path / "source.txt"
    source_path.write_bytes(b"stable source")
    events = []
    injected_at = [""]

    def checkpoint(name):
        events.append(name)
        if name == injected_at[0]:
            raise RuntimeError(f"injected at {name}")

    with SourceAuthority.open(
        tmp_path / "private" / "authority.sqlite3",
        clock=lambda: NOW,
        checkpoint=checkpoint,
    ) as authority:
        inventory = authority.seal_and_register(
            [source_path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )
        authority.resolve(
            inventory["sources"][0],
            _context("owner-a", "workload-a"),
        )
        required = (
            "source_authority_transaction_before_begin",
            "source_authority_transaction_after_begin",
            "inventory_before_discovery",
            "inventory_root_before_lstat",
            "inventory_root_after_lstat",
            "inventory_spool_before_discovery_commit",
            "inventory_spool_after_discovery_commit",
            "inventory_after_discovery",
            "inventory_source_before_open",
            "inventory_source_after_open",
            "inventory_source_before_initial_stat",
            "inventory_source_after_initial_stat",
            "inventory_source_before_read",
            "inventory_source_after_read",
            "inventory_source_before_final_stat",
            "inventory_source_after_final_stat",
            "inventory_spool_before_batch_commit",
            "inventory_spool_after_batch_commit",
            "inventory_before_directory_revalidation",
            "inventory_after_directory_revalidation",
            "inventory_spool_before_finalize_commit",
            "inventory_spool_after_finalize_commit",
            "source_authority_transaction_before_commit",
            "source_authority_transaction_after_commit",
            "source_snapshot_before_temp_create",
            "source_snapshot_after_temp_create",
            "source_snapshot_before_copy",
            "source_snapshot_after_copy",
            "source_snapshot_before_fsync",
            "source_snapshot_after_fsync",
            "source_snapshot_before_verification",
            "source_snapshot_after_verification",
        )
        positions = [events.index(name) for name in required]
        assert positions == sorted(positions)

        injected_at[0] = "inventory_source_after_read"
        with pytest.raises(RuntimeError, match="after_read"):
            authority.seal_and_register(
                [source_path],
                owner_user_id="owner-a",
                workload_id="workload-b",
                device_id="server",
                limits=_limits(),
                valid_until=NOW + timedelta(days=1),
            )
        assert authority._connection.in_transaction is False
        assert authority._connection.execute(
            "SELECT COUNT(*) FROM source_grants WHERE workload_id='workload-b'"
        ).fetchone()[0] == 0

        injected_at[0] = "source_authority_transaction_before_commit"
        with pytest.raises(RuntimeError, match="before_commit"):
            authority.seal_and_register(
                [source_path],
                owner_user_id="owner-a",
                workload_id="workload-c",
                device_id="server",
                limits=_limits(),
                valid_until=NOW + timedelta(days=1),
            )
        assert authority._connection.in_transaction is False
        assert authority._connection.execute(
            "SELECT COUNT(*) FROM source_grants WHERE workload_id='workload-c'"
        ).fetchone()[0] == 0

        injected_at[0] = "source_authority_transaction_after_commit"
        with pytest.raises(RuntimeError, match="after_commit"):
            authority.seal_and_register(
                [source_path],
                owner_user_id="owner-a",
                workload_id="workload-d",
                device_id="server",
                limits=_limits(),
                valid_until=NOW + timedelta(days=1),
            )
        assert authority._connection.in_transaction is False
        assert authority._connection.execute(
            "SELECT COUNT(*) FROM source_grants WHERE workload_id='workload-d'"
        ).fetchone()[0] == 1

        injected_at[0] = ""
        replay = authority.seal_and_register(
            [source_path],
            owner_user_id="owner-a",
            workload_id="workload-d",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )
        try:
            assert len(replay["sources"]) == 1
        finally:
            close = getattr(replay, "close", None)
            if callable(close):
                close()
        assert authority._connection.execute(
            "SELECT COUNT(*) FROM source_grants WHERE workload_id='workload-d'"
        ).fetchone()[0] == 1


def test_snapshot_interruption_removes_partial_file_and_allows_retry(
    tmp_path, monkeypatch,
):
    import durable_workloads.source_authority as authority_module

    path = tmp_path / "source.txt"
    path.write_bytes(b"stable")
    with SourceAuthority.open(
        tmp_path / "private" / "authority.sqlite3", clock=lambda: NOW,
    ) as authority:
        source = authority.seal_and_register(
            [path], owner_user_id="owner-a", workload_id="workload-a",
            device_id="server", limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )["sources"][0]

        class InjectedInterrupt(BaseException):
            pass

        def interrupted(*_args, **_kwargs):
            raise InjectedInterrupt()

        original = authority_module._stable_file_digest
        monkeypatch.setattr(authority_module, "_stable_file_digest", interrupted)
        with pytest.raises(InjectedInterrupt):
            authority.resolve(source, _context("owner-a", "workload-a"))
        assert authority._snapshot_files == []
        session = authority._snapshot_session
        assert session is not None
        assert list(session.iterdir()) == [session / "lease.lock"]

        monkeypatch.setattr(authority_module, "_stable_file_digest", original)
        assert Path(authority.resolve(
            source, _context("owner-a", "workload-a"),
        ).value).read_bytes() == b"stable"


def test_snapshot_cleanup_removes_crash_residue_but_not_a_live_lane(tmp_path):
    path = tmp_path / "source.png"
    path.write_bytes(b"stable")
    database = tmp_path / "private" / "authority.sqlite3"
    first = SourceAuthority.open(database, clock=lambda: NOW)
    second = SourceAuthority.open(database, clock=lambda: NOW)
    try:
        source = first.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )["sources"][0]
        live_snapshot = Path(first.resolve(
            source, _context("owner-a", "workload-a"),
        ).value)
        stale = database.parent / "source_snapshots" / ".session-deadbeef"
        stale.mkdir(mode=0o700)
        (stale / "lease.lock").write_bytes(b"")
        (stale / "partial.png").write_bytes(b"partial")

        second_snapshot = Path(second.resolve(
            source, _context("owner-a", "workload-a"),
        ).value)
        assert live_snapshot.exists()
        assert second_snapshot.exists()
        assert not stale.exists()
    finally:
        second.close()
        first.close()


def test_authority_expiry_revocation_and_bounded_pruning(tmp_path):
    current = [NOW]
    path = tmp_path / "source.txt"
    path.write_text("stable", encoding="utf-8")
    with SourceAuthority.open(
        tmp_path / "private" / "authority.sqlite3",
        clock=lambda: current[0],
    ) as authority:
        inventory = authority.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(minutes=5),
        )
        source = inventory["sources"][0]
        assert authority.revoke_workload("owner-a", "workload-a") == 1
        with pytest.raises(SourceAuthorityError, match="unavailable"):
            authority.resolve(source, _context("owner-a", "workload-a"))
        assert authority.prune(limit=1) == 1
        assert authority.prune(limit=1) == 0

        second = authority.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(minutes=5),
        )["sources"][0]
        current[0] = NOW + timedelta(minutes=5)
        with pytest.raises(SourceAuthorityError, match="expired"):
            authority.resolve(second, _context("owner-a", "workload-a"))
        assert authority.prune(limit=1) == 1


def test_resealing_replaces_stale_grants_without_leaving_authority_active(
    tmp_path,
):
    path = tmp_path / "source.txt"
    path.write_text("first version", encoding="utf-8")
    with SourceAuthority.open(
        tmp_path / "private" / "authority.sqlite3",
        clock=lambda: NOW,
    ) as authority:
        first_inventory = authority.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )
        first = first_inventory["sources"][0]

        path.write_text("second version with a new identity", encoding="utf-8")
        second_inventory = authority.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="server",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )
        second = second_inventory["sources"][0]

        assert first["source_id"] != second["source_id"]
        with pytest.raises(SourceAuthorityError, match="unavailable"):
            authority.resolve(first, _context("owner-a", "workload-a"))
        assert Path(authority.resolve(
            second, _context("owner-a", "workload-a"),
        ).value).read_text(encoding="utf-8") == (
            "second version with a new identity"
        )
        assert authority._connection.execute(
            """
            SELECT COUNT(*) FROM source_grants
            WHERE owner_user_id='owner-a' AND workload_id='workload-a'
              AND revoked_at IS NULL
            """
        ).fetchone()[0] == 1
        assert authority.prune(limit=10) == 1


def test_workload_reconciliation_rotates_and_revokes_terminal_scopes(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("stable", encoding="utf-8")
    inventories = {}
    with SourceAuthority.open(
        tmp_path / "private" / "authority.sqlite3",
        clock=lambda: NOW,
    ) as authority:
        for workload in ("workload-a", "workload-b", "workload-c"):
            inventories[workload] = authority.seal_and_register(
                [path],
                owner_user_id="owner-a",
                workload_id=workload,
                device_id="server",
                limits=_limits(),
                valid_until=NOW + timedelta(days=1),
            )

        checked = []

        def active(_owner, workload):
            checked.append(workload)
            return workload != "workload-c"

        assert authority.reconcile_workloads(active, limit=1) == 0
        assert authority.reconcile_workloads(active, limit=1) == 0
        assert authority.reconcile_workloads(active, limit=1) == 1
        assert checked == ["workload-a", "workload-b", "workload-c"]
        with pytest.raises(SourceAuthorityError, match="unavailable"):
            authority.resolve(
                inventories["workload-c"]["sources"][0],
                _context("owner-a", "workload-c"),
            )
        assert authority.prune(limit=1) == 1


def test_v1_authority_database_migrates_before_use(tmp_path):
    database = tmp_path / "private" / "authority.sqlite3"
    database.parent.mkdir(mode=0o700)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE source_authority_schema (
            singleton INTEGER PRIMARY KEY,
            version INTEGER NOT NULL
        );
        INSERT INTO source_authority_schema VALUES (1, 1);
        CREATE TABLE source_grants (
            owner_user_id TEXT NOT NULL,
            workload_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            locator_bytes BLOB NOT NULL,
            content_digest TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            granted_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            PRIMARY KEY (owner_user_id, workload_id, source_id)
        ) WITHOUT ROWID;
        """
    )
    connection.close()

    with SourceAuthority.open(database, clock=lambda: NOW) as authority:
        columns = {
            str(row[1])
            for row in authority._connection.execute(
                "PRAGMA table_info(source_grants)"
            )
        }
        version = authority._connection.execute(
            "SELECT version FROM source_authority_schema WHERE singleton=1"
        ).fetchone()[0]
    assert {"checked_at", "check_generation"} <= columns
    assert version == 3


def test_remote_locator_requires_and_uses_explicit_device_attestation(tmp_path):
    path = tmp_path / "remote-source.txt"
    path.write_text("remote", encoding="utf-8")
    seen = {}

    def attest(device_id, locator, source, context):
        seen.update({"device_id": device_id, "locator": locator, "owner": context.owner_user_id})
        return SourceResolution(
            value="C:\\authorized\\remote-source.txt",
            source_id=source["source_id"],
            device_id=source["device_id"],
            content_digest=source["content_digest"],
            size_bytes=source["size_bytes"],
            mtime_ns=source["mtime_ns"],
            authority="remote-device-v1",
        )

    with SourceAuthority.open(
        tmp_path / "private" / "remote.sqlite3",
        remote_attestor=attest,
        clock=lambda: NOW,
    ) as authority:
        inventory = authority.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="device-a",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )
        result = authority.resolve(
            inventory["sources"][0],
            _context("owner-a", "workload-a"),
        )
        assert result.value == "C:\\authorized\\remote-source.txt"
        assert seen == {
            "device_id": "device-a",
            "locator": str(path),
            "owner": "owner-a",
        }


def test_remote_attestation_must_match_every_sealed_identity_field(tmp_path):
    path = tmp_path / "remote-source.txt"
    path.write_text("remote", encoding="utf-8")

    def mismatched(_device_id, _locator, source, _context):
        return SourceResolution(
            value="C:\\untrusted\\remote-source.txt",
            source_id=source["source_id"],
            device_id=source["device_id"],
            content_digest="sha256:" + "0" * 64,
            size_bytes=source["size_bytes"],
            mtime_ns=source["mtime_ns"],
            authority="remote-device-v1",
        )

    with SourceAuthority.open(
        tmp_path / "private" / "mismatch.sqlite3",
        remote_attestor=mismatched,
        clock=lambda: NOW,
    ) as authority:
        source = authority.seal_and_register(
            [path],
            owner_user_id="owner-a",
            workload_id="workload-a",
            device_id="device-a",
            limits=_limits(),
            valid_until=NOW + timedelta(days=1),
        )["sources"][0]
        with pytest.raises(SourceAuthorityError, match="does not match"):
            authority.resolve(source, _context("owner-a", "workload-a"))


def test_authority_registration_rolls_back_when_sealing_fails(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    with SourceAuthority.open(
        tmp_path / "private" / "rollback.sqlite3",
        clock=lambda: NOW,
    ) as authority:
        with pytest.raises(Exception):
            authority.seal_and_register(
                [root],
                owner_user_id="owner-a",
                workload_id="workload-a",
                device_id="server",
                limits=InventoryLimits(
                    max_sources=1,
                    max_total_bytes=1024,
                    max_depth=2,
                ),
                valid_until=NOW + timedelta(days=1),
            )
        assert authority._connection.execute(
            "SELECT COUNT(*) FROM source_grants"
        ).fetchone()[0] == 0
