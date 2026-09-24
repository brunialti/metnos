"""Administrative cutover: which stores, and when the marker is written."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def service_account(tmp_path, monkeypatch):
    """Portable policy tests must not depend on an installed service account."""
    snapshot = account(str(tmp_path / "service-home"))
    monkeypatch.setattr(cutover, "resolve_posix_account_snapshot_v1",
                        lambda name: snapshot)
    return snapshot


@pytest.fixture(autouse=True)
def service_control(monkeypatch):
    """Keep Linux-only service adapters outside the portable policy under test."""
    monkeypatch.setitem(sys.modules, "stack_reconcile", SimpleNamespace(
        Systemctl=lambda **kwargs: pytest.fail("service observation not configured")))
    monkeypatch.setitem(sys.modules, "services_registry", SimpleNamespace(
        owned_service_units_v1=lambda: pytest.fail("service catalog not configured")))


# --- which stores the cutover reads ------------------------------------------

def test_the_selected_stores_mirror_the_service_defaults(service_account):
    home = Path(service_account.record.home)
    resolved = cutover.selected_sources_v1({}, service_account)
    assert list(resolved) == [
        ("statistics",
         home / ".local/state/metnos/executor_stats.db",
         "executor_stats"),
        ("promotions",
         home / ".local/share/metnos/promoter.sqlite",
         "proposal_promote"),
    ]


def test_a_service_override_wins_over_the_default_path(tmp_path, service_account):
    resolved = cutover.selected_sources_v1({
        "METNOS_EXECUTOR_STATS_DB": str(tmp_path / "elsewhere/stats.db"),
        "METNOS_USER_DATA": str(tmp_path / "data"),
    }, service_account)
    paths = {kind: path for kind, path, _table in resolved}
    assert paths["statistics"] == tmp_path / "elsewhere/stats.db"
    assert paths["promotions"] == tmp_path / "data/promoter.sqlite"


def test_a_service_home_override_moves_both_stores(tmp_path, service_account):
    home = tmp_path / "override-home"
    resolved = cutover.selected_sources_v1({"HOME": str(home)}, service_account)
    assert all(path.is_relative_to(home) for _kind, path, _t in resolved)


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
        b"METNOS_USER_CONFIG=/srv/config\0"
        b"ANTHROPIC_API_KEY=secret\0PATH=/usr/bin\0")
    monkeypatch.setattr(cutover, "_PROC_ROOT_V1", proc)
    owner = os.stat(proc / "4242").st_uid
    observed = cutover._service_environment(
        4242, PosixAccountSnapshotV1(
            PosixAccountRecordV1("metnos", owner, 985, "/srv/home", "/usr/sbin/nologin"),
            (985,)))
    assert set(observed) == {"HOME", "METNOS_PROMOTER_DB", "METNOS_USER_CONFIG"}


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


# --- the two commands --------------------------------------------------------

@pytest.mark.parametrize("command", ["plan", "apply"])
def test_neither_command_runs_without_administrative_privilege(monkeypatch, command):
    monkeypatch.setattr(cutover.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(cutover, "_managed_authority_platform_supported_v1", lambda: True)
    entry = cutover.apply_cutover_v1 if command == "apply" else cutover.plan_cutover_v1
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        entry()
    assert raised.value.code == "cutover_root_required"


def test_neither_command_runs_on_an_unsupported_platform(monkeypatch):
    monkeypatch.setattr(cutover, "_managed_authority_platform_supported_v1", lambda: False)
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.plan_cutover_v1()
    assert raised.value.code == "cutover_platform_unsupported"


OBSERVED = {
    "migration_id": MIGRATION,
    "environment": {"HOME": "/srv/home"},
    "sources": [{"kind": "statistics", "path": "/srv/home/stats.db",
                 "legacy_table": "executor_stats", "source_id": "sha256:" + "3" * 64,
                 "content_id": "sha256:" + "4" * 64, "rows": 2,
                 "plan_id": "sha256:" + "5" * 64, "pending": 0, "restrictions": 1}],
    "selectable": {"demo": ["user:demo/manifest.toml", "sha256:" + "6" * 64]},
}


def _as_root(monkeypatch):
    monkeypatch.setattr(cutover, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(cutover.os, "geteuid", lambda: 0)


@native
def test_planning_records_the_decision_and_changes_nothing(marker_root, monkeypatch):
    _as_root(monkeypatch)
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: dict(OBSERVED))
    document = cutover.plan_cutover_v1()
    assert document["purpose"] == cutover.HANDOFF_PURPOSE_V1
    assert document["migration_id"] == MIGRATION
    assert (marker_root / cutover.MARKER_BASENAME_V1).exists() is False
    assert cutover.read_handoff_v1() == document


@native
def test_replanning_replaces_the_previous_plan(marker_root, monkeypatch):
    _as_root(monkeypatch)
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: dict(OBSERVED))
    cutover.plan_cutover_v1()
    revised = dict(OBSERVED, migration_id="sha256:" + "7" * 64)
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: dict(revised))
    assert cutover.plan_cutover_v1()["migration_id"] == revised["migration_id"]
    assert cutover.read_handoff_v1()["migration_id"] == revised["migration_id"]


@native
@pytest.mark.parametrize("damage", ["purpose", "schema_version", "service_user",
                                    "extra", "missing", "sources"])
def test_a_document_that_is_not_exactly_a_plan_is_refused(marker_root, monkeypatch, damage):
    _as_root(monkeypatch)
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: dict(OBSERVED))
    document = cutover.plan_cutover_v1()
    if damage == "extra":
        document["unexpected"] = 1
    elif damage == "missing":
        document.pop("selectable")
    elif damage == "sources":
        document["sources"] = []
    elif damage == "schema_version":
        document["schema_version"] = 2
    else:
        document[damage] = "something else"
    path = marker_root / cutover.HANDOFF_BASENAME_V1
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")))
    path.chmod(0o644)
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.read_handoff_v1()
    assert raised.value.code == "cutover_handoff_invalid"


@native
def test_applying_without_a_plan_is_refused(marker_root, monkeypatch):
    _as_root(monkeypatch)
    with pytest.raises((cutover.LifecycleCutoverError, files.OwnershipAuthorityError)):
        cutover.apply_cutover_v1()


# --- the writers must actually be stopped -----------------------------------

def _units(monkeypatch, units):
    monkeypatch.setattr("services_registry.owned_service_units_v1", lambda: units)


def _systemctl(monkeypatch, states):
    class _Fake:
        def __init__(self, *_a, **_k):
            pass

        def show(self, unit, scope):
            return dict(states[(scope, unit)])

    monkeypatch.setattr("stack_reconcile.Systemctl", _Fake)


IDLE = {"LoadState": "loaded", "ActiveState": "inactive", "MainPID": "0"}
RUNNING = {"LoadState": "loaded", "ActiveState": "active", "MainPID": "4242"}


def test_the_running_system_worker_blocks_the_migration(monkeypatch):
    """The defect this exists for: the retired user unit answers for nobody."""
    units = (("system", "metnos-durable-worker.service"),
             ("system", "metnos-http.service"))
    _units(monkeypatch, units)
    _systemctl(monkeypatch, {units[0]: RUNNING, units[1]: IDLE})
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._prove_productive_services_stopped_v1("metnos")
    assert raised.value.code == "cutover_writer_running"
    assert "metnos-durable-worker.service" in raised.value.detail


def test_a_stopped_stack_is_reported_unit_by_unit(monkeypatch):
    units = (("system", "metnos-durable-worker.service"),
             ("system", "metnos-telegram-daemon.service"))
    _units(monkeypatch, units)
    _systemctl(monkeypatch, {unit: IDLE for unit in units})
    observed = cutover._prove_productive_services_stopped_v1("metnos")
    assert [item["unit"] for item in observed] == [unit for _scope, unit in units]


def test_a_masked_unit_counts_as_idle_but_a_live_one_does_not(monkeypatch):
    units = (("user", "metnos-http.service"), ("system", "metnos-http.service"))
    _units(monkeypatch, units)
    _systemctl(monkeypatch, {
        units[0]: {"LoadState": "masked", "ActiveState": "inactive", "MainPID": "0"},
        units[1]: RUNNING,
    })
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._prove_productive_services_stopped_v1("metnos")
    assert raised.value.code == "cutover_writer_running"


@pytest.mark.parametrize("state,expected", [
    ({"LoadState": "error", "ActiveState": "inactive", "MainPID": "0"},
     "cutover_topology_unknown"),
    ({"LoadState": "loaded", "ActiveState": "inactive", "MainPID": "0",
      "ManagerError": "no bus"}, "cutover_topology_unknown"),
    ({"LoadState": "loaded", "ActiveState": "inactive", "MainPID": "77"},
     "cutover_writer_running"),
])
def test_an_unreadable_or_busy_unit_is_never_read_as_idle(monkeypatch, state, expected):
    units = (("system", "metnos-http.service"),)
    _units(monkeypatch, units)
    _systemctl(monkeypatch, {units[0]: state})
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._prove_productive_services_stopped_v1("metnos")
    assert raised.value.code == expected


def test_an_unreadable_catalog_refuses_rather_than_guessing(monkeypatch):
    def unreadable():
        raise ValueError("installed ownership window is not verified")

    monkeypatch.setattr("services_registry.owned_service_units_v1", unreadable)
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._prove_productive_services_stopped_v1("metnos")
    assert raised.value.code == "cutover_topology_unknown"


def test_an_empty_topology_is_not_a_quiescent_one(monkeypatch):
    _units(monkeypatch, ())
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover._prove_productive_services_stopped_v1("metnos")
    assert raised.value.detail == "no declared unit"


@pytest.fixture
def planned(marker_root, monkeypatch):
    _as_root(monkeypatch)
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: dict(OBSERVED))
    cutover.plan_cutover_v1()
    barrier = []

    class _Barrier:
        def __enter__(self):
            barrier.append("held")
            return self

        def __exit__(self, *_exc):
            barrier.append("released")
            return False

    import contract_cutover_guard
    monkeypatch.setattr(
        contract_cutover_guard, "_contract_cutover_guard_for_service_user_v1",
        lambda user, **kw: _Barrier())
    monkeypatch.setattr(cutover, "_prove_productive_services_stopped_v1",
                        lambda user: ({"scope": "system", "unit": "metnos-http.service",
                                       "active_state": "inactive"},))
    return barrier


@native
def test_the_migration_runs_inside_the_quiescent_barrier(planned, monkeypatch):
    order = []
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: (
        order.append("migrated") or {"applied": {
            "migration_id": MIGRATION, "preserved": 2, "resolved": 2,
            "restricted": 1, "pending": 0}}))
    monkeypatch.setattr(cutover, "_installation_id", lambda: INSTALLATION)
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda *a: order.append("marker") or Path("/marker"))
    cutover.apply_cutover_v1()
    assert planned == ["held", "released"]
    assert order == ["migrated", "marker"]


@native
def test_an_open_disposition_blocks_the_marker(planned, monkeypatch):
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: {"applied": {
        "migration_id": MIGRATION, "preserved": 2, "resolved": 2,
        "restricted": 1, "pending": 2}})
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda *a: pytest.fail("marker written with open cases"))
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.apply_cutover_v1()
    assert raised.value.code == "cutover_pending_disposition"
    assert raised.value.detail == "2"
    assert planned == ["held", "released"]


@native
def test_a_different_decision_than_the_reviewed_one_blocks_the_marker(planned, monkeypatch):
    monkeypatch.setattr(cutover, "_in_service_child", lambda *args: {"applied": {
        "migration_id": "sha256:" + "f" * 64, "preserved": 2, "resolved": 2,
        "restricted": 1, "pending": 0}})
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda *a: pytest.fail("marker written for another decision"))
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.apply_cutover_v1()
    assert raised.value.code == "cutover_plan_stale"


@native
def test_a_failed_child_never_reaches_the_marker(planned, monkeypatch):
    def failing(*_args):
        raise cutover.LifecycleCutoverError("cutover_epoch_store_absent", "/x")

    monkeypatch.setattr(cutover, "_in_service_child", failing)
    monkeypatch.setattr(cutover, "_install_marker",
                        lambda *a: pytest.fail("marker written after a failure"))
    with pytest.raises(cutover.LifecycleCutoverError) as raised:
        cutover.apply_cutover_v1()
    assert raised.value.code == "cutover_epoch_store_absent"
    assert planned == ["held", "released"]


# --- retiring the copied stores ----------------------------------------------

def test_a_copied_store_is_made_unwritable_and_reported(tmp_path):
    path = tmp_path / "executor_stats.db"
    path.write_bytes(b"")
    retired = cutover._retire_sources((("statistics", path, "executor_stats"),))
    observed_mode = stat.S_IMODE(path.stat().st_mode)
    assert observed_mode & 0o222 == 0
    # Windows chmod removes writes but cannot enforce owner-only POSIX access.
    assert retired == [{"kind": "statistics", "read_only": observed_mode == 0o400,
                        "mode": oct(observed_mode)}]
    if sys.platform.startswith("linux"):
        assert observed_mode == 0o400


def test_a_store_that_cannot_be_retired_says_so(tmp_path):
    missing = tmp_path / "absent.db"
    retired = cutover._retire_sources((("statistics", missing, "executor_stats"),))
    assert retired[0]["read_only"] is False and retired[0]["error"]


def test_the_command_line_accepts_only_its_two_stages():
    assert cutover.main([]) == 64
    assert cutover.main(["apply", "--force"]) == 64


# --- locating the service's store from a root-run tool -----------------------

@pytest.mark.parametrize("environment,expected", [
    ({"METNOS_USER_STATE": "state"}, "state/birth/executor_epochs.sqlite"),
    ({"HOME": "home"}, "home/.local/state/metnos/birth/executor_epochs.sqlite"),
    ({"METNOS_USER_STATE": "state", "HOME": "ignored"},
     "state/birth/executor_epochs.sqlite"),
])
def test_the_epoch_store_is_located_by_the_recorded_overrides(tmp_path, environment, expected):
    absolute = {key: str(tmp_path / value) for key, value in environment.items()}
    assert cutover.service_epoch_db_v1(absolute) == tmp_path / expected


def test_without_an_override_the_service_account_decides_not_the_caller(service_account):
    """Root has its own state directory, and it is never the answer."""
    assert cutover.service_epoch_db_v1({}) == (
        Path(service_account.record.home) / ".local/state/metnos/birth/executor_epochs.sqlite")


@native
@pytest.mark.parametrize("operation", ["plan", "apply", "qualify"])
def test_service_child_loads_fresh_configuration(operation, tmp_path, monkeypatch,
                                                service_account):
    """The real interpreter must not retain the parent's imported root paths."""
    import config
    import subprocess

    service_home = tmp_path / "observed-home"
    state = tmp_path / "observed-state"
    environment = {"HOME": str(service_home), "METNOS_USER_STATE": str(state)}
    monkeypatch.setenv("HOME", str(tmp_path / "caller-home"))
    monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path / "caller-data"))
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-cross")
    monkeypatch.setattr(config, "PATH_USER_STATE", tmp_path / "already-imported")
    monkeypatch.setattr(cutover, "_service_main_pid", lambda: 4242)
    monkeypatch.setattr(cutover, "_service_environment", lambda *args: environment)
    run = subprocess.run

    def run_as_test_account(command, **kwargs):
        # Exercise the actual fresh interpreter without requiring root in CI.
        assert kwargs.pop("user") == service_account.record.uid
        assert kwargs.pop("group") == service_account.record.gid
        assert kwargs.pop("extra_groups") == service_account.supplementary_gids
        # Isolate catalog/storage observations; configuration imports are real.
        command = list(command)
        command[-1] = command[-1].replace(
            "raise SystemExit(_service_worker())",
            "import config, json, os; "
            "print(json.dumps({'state': str(config.PATH_USER_STATE), "
            "'data': str(config.PATH_USER_DATA), "
            "'secret': os.environ.get('UNRELATED_SECRET')}))")
        return run(command, **kwargs)

    monkeypatch.setattr(cutover.subprocess, "run", run_as_test_account)
    handoff = {"environment": environment, "sources": [], "migration_id": MIGRATION}
    report = cutover._in_service_child(operation, handoff, qualification={})
    assert report == {"state": str(state),
                      "data": str(service_home / ".local/share/metnos"),
                      "secret": None}
    assert config.PATH_USER_STATE == tmp_path / "already-imported"
