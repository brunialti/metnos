"""An authenticated restored predecessor converges without erasing its backup."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_dominant_topology as topology
from install import birth_authority_provisioner as provisioner
import test_executor_birth_dominant_topology_replacement as replacement
import test_systemd_manager_cutover_compatibility as manager


_OLD, _NEW, _NAME = replacement._OLD, replacement._NEW, replacement._NAME
_install, _backup, _snapshot = replacement._install, replacement._backup, replacement._snapshot


def _restored(root: Path):
    _install(root, {_NAME: _OLD})
    _backup(root).write_bytes(_OLD)
    _backup(root).chmod(0o644)


def test_restored_predecessor_keeps_historical_backup_and_replays(tmp_path):
    _restored(tmp_path)
    historical = _snapshot(tmp_path)[_backup(tmp_path).name]
    first = _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    after = _snapshot(tmp_path)
    second = _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    assert not first[0].repeated and second[0].repeated
    assert (tmp_path / _NAME).read_bytes() == _NEW
    assert _snapshot(tmp_path)[_backup(tmp_path).name] == historical
    assert _snapshot(tmp_path) == after
    assert len(after) == 2


@pytest.mark.parametrize("stage", (
    "dominant_fragment_restored_before", "dominant_fragment_restored_removed",
    "dominant_fragment_staged", "dominant_fragment_published",
))
def test_restored_predecessor_resumes_every_new_and_existing_boundary(tmp_path, stage):
    _restored(tmp_path)
    historical = _snapshot(tmp_path)[_backup(tmp_path).name]

    def interrupt(observed):
        if observed == stage:
            raise RuntimeError("interrupted restored transition")

    with pytest.raises(RuntimeError, match="interrupted restored transition"):
        _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD},
                 _crash_seam=interrupt)
    _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    assert (tmp_path / _NAME).read_bytes() == _NEW
    assert _snapshot(tmp_path)[_backup(tmp_path).name] == historical
    assert len(list(tmp_path.iterdir())) == 2


def test_crash_after_duplicate_unlink_before_directory_sync_resumes(tmp_path, monkeypatch):
    _restored(tmp_path)
    historical = _snapshot(tmp_path)[_backup(tmp_path).name]
    original = os.unlink

    def interrupt(path, *, dir_fd=None):
        original(path, dir_fd=dir_fd)
        if path == _NAME and dir_fd is not None:
            raise RuntimeError("after duplicate unlink")

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", interrupt)
        with pytest.raises(RuntimeError, match="after duplicate unlink"):
            _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    assert not (tmp_path / _NAME).exists()
    _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    assert _snapshot(tmp_path)[_backup(tmp_path).name] == historical
    assert (tmp_path / _NAME).read_bytes() == _NEW


@pytest.mark.parametrize("case", ("foreign_backup", "foreign_current", "staging", "backup_mode", "backup_hardlink"))
def test_foreign_or_unsafe_restoration_denies_the_whole_set(tmp_path, case):
    _restored(tmp_path)
    name = "z.service"
    _install(tmp_path, {name: _OLD})
    backup = _backup(tmp_path, name)
    backup.write_bytes(_OLD)
    backup.chmod(0o644)
    if case == "foreign_backup":
        backup.write_bytes(b"foreign")
    elif case == "foreign_current":
        (tmp_path / name).write_bytes(b"foreign")
    elif case == "staging":
        (tmp_path / f".{name}.installing").write_bytes(_NEW)
    elif case == "backup_mode":
        backup.chmod(0o600)
    else:
        os.link(backup, tmp_path / "foreign-link")
    before = _snapshot(tmp_path)
    with pytest.raises(topology.DominantTopologyError):
        _install(tmp_path, {_NAME: _NEW, name: _NEW},
                 previous_fragments={_NAME: _OLD, name: _OLD})
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("change", ("current", "backup", "staging"))
def test_revalidation_before_unlink_refuses_changed_restoration(tmp_path, change):
    _restored(tmp_path)

    def drift(stage):
        if stage == "dominant_fragment_restored_before":
            target = {"current": tmp_path / _NAME, "backup": _backup(tmp_path),
                      "staging": tmp_path / f".{_NAME}.installing"}[change]
            target.write_bytes(b"foreign")

    with pytest.raises(topology.DominantTopologyError):
        _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD},
                 _crash_seam=drift)
    assert (tmp_path / _NAME).exists()
    assert _backup(tmp_path).exists()
    assert (tmp_path / _NAME).read_bytes() != _NEW


class ReachedStartupGate(Exception):
    pass


@pytest.mark.parametrize("case", ("previous", "restored", "foreign_backup"))
def test_real_cutover_reads_whole_topology_before_startup_or_quiescence(tmp_path, monkeypatch, case):
    import executor_birth_distribution_manifest as manifest
    import executor_birth_ownership_coordinator as coordinator
    import executor_birth_service_catalog as catalog
    import install.executor_birth_source_receiver as receiver
    import install.executor_birth_startup_gate as gate

    call, events = manager._cutover_entry(
        monkeypatch, (0, b"Version=255.4-1ubuntu8.18\n", b""),
    )
    distribution = manifest.verify_current_installation_distribution_v1(b"", b"")
    distribution.release_sequence = 2
    descriptor = manifest.capture_current_deployment_descriptor_v1(distribution)[1]
    descriptor.system_unit_root = str(tmp_path)
    previous = SimpleNamespace(encoded=b"previous", signature=b"p" * 64)
    artifacts = object()
    previous_catalog = SimpleNamespace(unit_fragments=((_NAME, _OLD),))
    candidate_catalog = SimpleNamespace(unit_fragments=((_NAME, _NEW),))
    _install(tmp_path, {_NAME: _OLD})
    if case != "previous":
        _backup(tmp_path).write_bytes(_OLD if case == "restored" else b"foreign")
        _backup(tmp_path).chmod(0o644)
    before = _snapshot(tmp_path)

    def capture(candidate):
        assert candidate is distribution
        events.append("candidate-topology")
        return candidate_catalog

    def directory(path, *, owner):
        assert path == tmp_path and owner == (0, 0)
        # This CPU fixture is owned by its test process; the native topology
        # core still verifies its exact real uid/gid, mode and all unit files.
        return path

    def startup(session):
        assert session == "deployment-session"
        events.append("startup-gate")
        raise ReachedStartupGate()

    monkeypatch.setattr(receiver, "_load_received_source_with_product_session_v1",
                        lambda *_args: SimpleNamespace(source_id="sha256:" + "a" * 64))
    monkeypatch.setattr(coordinator, "_reserve_transition_edge_locked_v2", lambda *_a, **_k: None)
    monkeypatch.setattr(coordinator, "_completed_transition_locked_v2", lambda *_a: None)
    monkeypatch.setattr(provisioner, "_resume_required_transition_v2", lambda *_a: None)
    monkeypatch.setattr(provisioner, "_prepare_transition_receipt_material_locked_v2",
                        lambda *_a: SimpleNamespace(descriptor=descriptor,
                                                    previous_context=SimpleNamespace(distribution=previous)))
    monkeypatch.setattr(manifest, "authenticate_distribution_record_v1",
                        lambda encoded, _signature: distribution if encoded == b"distribution" else previous)
    monkeypatch.setattr(manifest, "capture_previous_release_artifacts_v1",
                        lambda current, old: artifacts if (current is distribution and old is previous)
                        else pytest.fail("previous authority binding changed"))
    monkeypatch.setattr(catalog, "load_previous_service_catalog_v1",
                        lambda value: previous_catalog if value is artifacts
                        else pytest.fail("previous catalog was not authenticated"))
    monkeypatch.setattr(catalog, "capture_current_service_catalog_v1", capture)
    monkeypatch.setattr(provisioner, "_require_transition_directory_v2", directory)
    monkeypatch.setattr(gate, "install_startup_gate_v1", startup)
    expected = topology.DominantTopologyError if case == "foreign_backup" else ReachedStartupGate
    with pytest.raises(expected):
        call()
    assert events.count("candidate-topology") == 1
    assert ("startup-gate" in events) is (case != "foreign_backup")
    assert events[-1] == "deployment-unlock"
    assert _snapshot(tmp_path) == before
