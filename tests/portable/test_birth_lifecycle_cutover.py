"""Administrative cutover: which stores, and when the marker is written."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

import executor_birth_activation_mode as mode
import executor_birth_authority_files as files
import install.birth_lifecycle_migration as cutover
from executor_birth_account_identity import (
    PosixAccountRecordV1, PosixAccountSnapshotV1,
)


native = pytest.mark.skipif(not sys.platform.startswith("linux"),
                            reason="the managed cutover is Linux-only")
INSTALLATION = "sha256:" + "1" * 64
MIGRATION = "sha256:" + "2" * 64


def account(home="/var/lib/metnos-service"):
    return PosixAccountSnapshotV1(
        PosixAccountRecordV1("metnos", 995, 985, home, "/usr/sbin/nologin"),
        (985,),
    )


# --- which stores the cutover reads ------------------------------------------

def test_the_selected_stores_mirror_the_service_defaults():
    resolved = cutover.selected_sources_v1({}, account())
    assert [(kind, str(path), table) for kind, path, table in resolved] == [
        ("statistics",
         "/var/lib/metnos-service/.local/state/metnos/executor_stats.db",
         "executor_stats"),
        ("promotions",
         "/var/lib/metnos-service/.local/share/metnos/promoter.sqlite",
         "proposal_promote"),
    ]


def test_a_service_override_wins_over_the_default_path():
    resolved = cutover.selected_sources_v1({
        "METNOS_EXECUTOR_STATS_DB": "/srv/elsewhere/stats.db",
        "METNOS_USER_DATA": "/srv/data",
    }, account())
    paths = {kind: str(path) for kind, path, _table in resolved}
    assert paths["statistics"] == "/srv/elsewhere/stats.db"
    assert paths["promotions"] == "/srv/data/promoter.sqlite"


def test_a_service_home_override_moves_both_stores():
    resolved = cutover.selected_sources_v1({"HOME": "/srv/home"}, account())
    assert all(str(path).startswith("/srv/home/") for _kind, path, _t in resolved)


def test_a_relative_override_is_refused_rather_than_resolved():
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.selected_sources_v1(
            {"METNOS_PROMOTER_DB": "relative/promoter.sqlite"}, account())
    assert raised.value.code == "cutover_source_invalid"


def test_only_the_path_overrides_are_read_from_the_service(tmp_path, monkeypatch):
    """A cutover must not inherit arbitrary environment from a live process."""
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "environ").write_bytes(
        b"HOME=/srv/home\0METNOS_PROMOTER_DB=/srv/p.sqlite\0"
        b"ANTHROPIC_API_KEY=secret\0PATH=/usr/bin\0")
    monkeypatch.setattr(cutover, "_PROC_ROOT_V1", proc)
    owner = os.stat(proc / "4242").st_uid
    observed = cutover._service_environment(
        4242, PosixAccountSnapshotV1(
            PosixAccountRecordV1("metnos", owner, 985, "/srv/home", "/usr/sbin/nologin"),
            (985,)))
    assert set(observed) == {"HOME", "METNOS_PROMOTER_DB"}


def test_a_process_owned_by_another_account_is_refused(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "environ").write_bytes(b"HOME=/srv/home\0")
    monkeypatch.setattr(cutover, "_PROC_ROOT_V1", proc)
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._service_environment(4242, account())
    assert raised.value.detail == "owner mismatch"


def test_an_absent_service_process_is_refused(monkeypatch):
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._service_environment(1, account())
    assert raised.value.detail == "main pid"


# --- when the marker is written ----------------------------------------------

@pytest.fixture
def marker_root(tmp_path, monkeypatch):
    directory = tmp_path / "certification-v1"
    monkeypatch.setattr(cutover, "DEFAULT_OWNERSHIP_ROOT_V1", tmp_path)
    monkeypatch.setattr(cutover, "ACTIVATION_DIRECTORY_V1", directory)
    monkeypatch.setattr(cutover, "_root_owned_chain", lambda path: None)
    monkeypatch.setattr(cutover, "_directory_metadata",
                        lambda path, **kw: files._directory_metadata(path, root_owned=False))
    monkeypatch.setattr(cutover, "_read_regular",
                        lambda path, **kw: files._read_regular(path, **{**kw, "root_owned": False}))
    monkeypatch.setattr(cutover, "_sync_directory", lambda path: None)

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(cutover, "_provisioning_lock", lambda *a, **k: _Lock())
    tmp_path.chmod(0o755)
    return directory


@native
def test_the_marker_is_readable_by_the_runtime_that_will_trust_it(marker_root, monkeypatch):
    path = cutover._install_marker(INSTALLATION, MIGRATION)
    assert path.parent == marker_root
    monkeypatch.setattr(mode, "ACTIVATION_DIRECTORY", marker_root)
    monkeypatch.setattr(mode, "_root_owned_chain", lambda location: None)
    monkeypatch.setattr(mode, "_directory_metadata",
                        lambda location, **kw: files._directory_metadata(location, root_owned=False))
    monkeypatch.setattr(mode, "_read_regular",
                        lambda location, **kw: files._read_regular(location, **{**kw, "root_owned": False}))
    def uncertified():
        raise mode.LifecycleError("f5_activation_invalid", "no certificate installed")

    monkeypatch.setattr(mode, "load_f5_activation", uncertified)
    state = mode.read_birth_activation_state()
    # This is the state immediately after a cutover: the epoch store owns
    # lifecycle, and the qualification the certificate would carry is absent.
    assert state.owner is mode.BirthStateOwner.EPOCH
    assert state.migration_id == MIGRATION
    assert state.certificate is None
    assert state.certificate_refusal == "f5_activation_invalid"


@native
def test_writing_the_same_marker_twice_is_accepted(marker_root):
    first = cutover._install_marker(INSTALLATION, MIGRATION)
    assert cutover._install_marker(INSTALLATION, MIGRATION) == first


@native
@pytest.mark.parametrize("field", ["installation_id", "migration_id"])
def test_a_second_cutover_never_replaces_the_first_marker(marker_root, field):
    cutover._install_marker(INSTALLATION, MIGRATION)
    changed = {"installation_id": INSTALLATION, "migration_id": MIGRATION}
    changed[field] = "sha256:" + "9" * 64
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._install_marker(**changed)
    assert raised.value.code == "cutover_marker_present"


@native
def test_a_damaged_marker_is_not_silently_rewritten(marker_root):
    path = cutover._install_marker(INSTALLATION, MIGRATION)
    path.chmod(0o666)
    with pytest.raises((cutover.LifecycleCutoverError, files.OwnershipAuthorityError)):
        cutover._install_marker(INSTALLATION, MIGRATION)


# --- the two stages ----------------------------------------------------------

def test_the_cutover_refuses_without_administrative_privilege(monkeypatch):
    monkeypatch.setattr(cutover.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cutover, "_managed_authority_platform_supported_v1", lambda: True)
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.run_cutover_v1(apply=True)
    assert raised.value.code == "cutover_root_required"


def test_the_cutover_refuses_on_an_unsupported_platform(monkeypatch):
    monkeypatch.setattr(cutover, "_managed_authority_platform_supported_v1", lambda: False)
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.run_cutover_v1(apply=True)
    assert raised.value.code == "cutover_platform_unsupported"


def _child_report(monkeypatch, report, *, failed=False):
    """Replace only the privileged child, keeping the parent's own checks."""
    monkeypatch.setattr(cutover, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(cutover.os, "geteuid", lambda: 0)

    def child():
        if failed:
            raise cutover.LifecycleCutoverError(
                report.get("error", "cutover_child_failed"), report.get("detail", ""))
        return dict(report)

    monkeypatch.setattr(cutover, "_run_child_migration", child)


def test_an_open_disposition_blocks_the_marker(monkeypatch):
    _child_report(monkeypatch, {"applied": {
        "migration_id": MIGRATION, "preserved": 3, "resolved": 3,
        "restricted": 1, "pending": 2}})
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda *a, **k: pytest.fail("marker written with open cases"))
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.run_cutover_v1(apply=True)
    assert raised.value.code == "cutover_pending_disposition"
    assert raised.value.detail == "2"


def test_planning_reports_without_writing_the_marker(monkeypatch):
    _child_report(monkeypatch, {"applied": {
        "migration_id": MIGRATION, "preserved": 3, "resolved": 3,
        "restricted": 1, "pending": 0}})
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda *a, **k: pytest.fail("marker written while planning"))
    report = cutover.run_cutover_v1(apply=False)
    assert report["marker"] is None
    assert report["applied"]["migration_id"] == MIGRATION


def test_a_failed_child_never_reaches_the_marker(monkeypatch):
    _child_report(monkeypatch, {"error": "cutover_epoch_store_absent", "detail": "/x"},
                  failed=True)
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda *a, **k: pytest.fail("marker written after a failure"))
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.run_cutover_v1(apply=True)
    assert raised.value.code == "cutover_epoch_store_absent"


def test_the_marker_is_the_last_thing_written(monkeypatch):
    _child_report(monkeypatch, {"applied": {
        "migration_id": MIGRATION, "preserved": 3, "resolved": 3,
        "restricted": 1, "pending": 0}})
    order = []
    monkeypatch.setattr(cutover, "_installation_id",
                        lambda: order.append("installation") or INSTALLATION)
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda installation, migration: order.append("marker")
                        or Path("/var/lib/metnos/executor-birth/certification-v1/migration.json"))
    report = cutover.run_cutover_v1(apply=True)
    assert order == ["installation", "marker"]
    assert report["marker"].endswith("migration.json")


def test_the_command_line_accepts_only_its_two_stages():
    assert cutover.main([]) == 64
    assert cutover.main(["apply", "--force"]) == 64
