"""Exact manager qualification and refusal before the cutover's first effect."""
from __future__ import annotations

from contextlib import contextmanager
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_distribution_assembler as assembler
from install import birth_authority_provisioner as provisioner
import test_executor_birth_admin_preflight_systemd_live as live

# Parsing is portable; the cutover entry and the effective-unit observation
# run only under systemd, with POSIX paths, identities and symlinks.
LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="systemd cutover is Linux-only",
)


@pytest.mark.parametrize("version", ("255.4-1ubuntu8.17", "255.4-1ubuntu8.18"))
def test_exact_supported_manager_version(version):
    assert preflight.parse_systemd_manager_version_v1(
        f"Version={version}\n".encode(),
    ) == version


@pytest.mark.parametrize("raw", (
    b"Version=255.4-1ubuntu8.19\n", b"Version=255.4\n",
    b"Version=255.5\n", b"Version=255.4-1ubuntu8.18 extra\n",
    b"Version=255.4-1ubuntu8.18\nVersion=255.4-1ubuntu8.18\n",
    b"Version=255.4-1ubuntu8.18\r\n",
))
def test_unknown_or_malformed_manager_version_stays_denied(raw):
    with pytest.raises(preflight.PreflightError):
        preflight.parse_systemd_manager_version_v1(raw)


class ReachedReceivedSource(Exception):
    """The real entry reached its next port; no source was read."""


def _cutover_entry(monkeypatch, reply, *, executable=None):
    import config
    import contract_cutover_guard
    import executor_birth_authority_gate as authority_gate
    import executor_birth_distribution_manifest as manifest
    import executor_birth_ownership_coordinator as coordinator
    import executor_birth_startup_gate as startup_gate
    import install.executor_birth_source_receiver as source_receiver
    import install.executor_birth_systemd as systemd_installer

    events = []
    descriptor = SimpleNamespace(
        service_user="metnos", service_uid=41, service_gid=42,
        service_home="/srv/metnos",
        python_executable=os.path.realpath(preflight.PYTHON_LINK),
        systemctl_executable=(
            os.path.realpath(preflight.SYSTEMCTL_LINK)
            if executable is None else executable
        ),
    )
    distribution = SimpleNamespace(encoded=b"distribution", signature=b"s" * 64)
    state = Path("/srv/metnos/.local/state/metnos")

    @contextmanager
    def lock():
        events.append("deployment-lock")
        try:
            yield "deployment-session"
        finally:
            events.append("deployment-unlock")

    def version(argv):
        assert argv == preflight.systemctl_show_argv_v1(
            os.path.realpath(preflight.SYSTEMCTL_LINK), None, ("Version",),
        )
        events.append("manager-version")
        return reply

    def received(*_args):
        events.append("received-source")
        raise ReachedReceivedSource()

    def forbidden(*_args, **_kwargs):
        pytest.fail("cutover effect was entered before the manager was accepted")

    monkeypatch.setattr(config, "PATH_USER_STATE", state)
    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: True)
    monkeypatch.setattr(provisioner, "_resolve_legacy_service_identity_v2",
                        lambda _name: SimpleNamespace(name="legacy-metnos"))
    monkeypatch.setattr(manifest, "verify_current_installation_distribution_v1",
                        lambda *_args: distribution)
    monkeypatch.setattr(manifest, "capture_current_deployment_descriptor_v1",
                        lambda candidate: (candidate, descriptor))
    monkeypatch.setattr(coordinator, "_deployment_lock_v1", lock)
    monkeypatch.setattr(preflight, "_run_systemctl_bounded_v1", version)
    monkeypatch.setattr(source_receiver, "_load_received_source_with_product_session_v1", received)
    monkeypatch.setattr(coordinator, "_reserve_transition_edge_locked_v2", forbidden)
    monkeypatch.setattr(startup_gate, "_exclusive_startup_gate_v1", forbidden)
    monkeypatch.setattr(contract_cutover_guard,
                        "_contract_cutover_guard_for_service_user_v1", forbidden)
    monkeypatch.setattr(systemd_installer, "install_group6_administrative_v1", forbidden)

    def call():
        return provisioner.complete_transition_cutover_v2(
            distribution, "sha256:" + "a" * 64, service_state_root=state,
            legacy_service_user="legacy-metnos", legacy_installation_root="/opt/metnos",
        )

    return call, events


@LINUX_ONLY
@pytest.mark.parametrize("reply", (
    (0, b"Version=255.4-1ubuntu8.19\n", b""),
    (0, b"Version=255.5\n", b""),
    (0, b"Version=255.4-1ubuntu8.18\nVersion=255.4-1ubuntu8.18\n", b""),
    (1, b"Version=255.4-1ubuntu8.18\n", b""),
    (0, b"Version=255.4-1ubuntu8.18\n", b"diagnostic"),
))
def test_unsupported_or_unavailable_manager_refuses_before_reserve_and_quiescence(
    monkeypatch, reply,
):
    call, events = _cutover_entry(monkeypatch, reply)
    with pytest.raises(preflight.PreflightError):
        call()
    assert events == ["deployment-lock", "manager-version", "deployment-unlock"]


@LINUX_ONLY
@pytest.mark.parametrize("version", ("255.4-1ubuntu8.17", "255.4-1ubuntu8.18"))
def test_supported_manager_reaches_next_port_without_reserving_or_stopping(
    monkeypatch, version,
):
    call, events = _cutover_entry(monkeypatch, (0, f"Version={version}\n".encode(), b""))
    with pytest.raises(ReachedReceivedSource):
        call()
    assert events == [
        "deployment-lock", "manager-version", "received-source", "deployment-unlock",
    ]


@LINUX_ONLY
@pytest.mark.parametrize("executable", ("/untrusted/systemctl", "relative-systemctl"))
def test_early_manager_read_cannot_execute_an_unbound_descriptor_path(monkeypatch, executable):
    call, events = _cutover_entry(
        monkeypatch, (0, b"Version=255.4-1ubuntu8.18\n", b""), executable=executable,
    )
    with pytest.raises(preflight.PreflightError, match="systemctl executable"):
        call()
    assert events == ["deployment-lock", "deployment-unlock"]


def _observation_fixture(tmp_path, monkeypatch, *, signed_version, observed_versions):
    live_root = tmp_path / "live"
    unit_root = live_root / "etc/systemd/system"
    wants = unit_root / "default.target.wants"
    wants.mkdir(parents=True)
    materials, fragment = live._target_materials()
    (unit_root / "probe.target").write_bytes(fragment)
    (unit_root / "probe.target").chmod(0o644)
    (wants / "probe.target").symlink_to("../probe.target")
    live._harden_directories(live_root)
    manager = live._target_manager_observation(materials)
    monkeypatch.setattr(preflight, "_run_systemctl_show_v1", lambda _e, unit, _p: (
        {"Version": ("255.4-1ubuntu8.17",)} if unit is None else manager
    ))
    first = preflight._capture_effective_systemd_units_core_v1(
        materials, systemctl_executable="/usr/bin/systemctl",
        live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    )
    record = assembler.build_startup_prerequisite_v1(
        request_id=live.D("1"), closed_build_id=live.D("2"), release_sequence=1,
        deployment_descriptor_id=live.D("3"), predecessor_id=live.D("4"),
        administrative_bundle_hash=materials.administrative_bundle_hash,
        python_binary_hash=live.D("5"), openssl_binary_hash=live.D("6"),
        openssl_tcb_hash=live.D("7"), systemctl_binary_hash=live.D("8"),
        systemd_analyze_binary_hash=live.D("9"), service_catalog_id=live.D("a"),
        service_coverage_hash=live.D("b"), systemd_manager_version=signed_version,
        candidate_units_hash=materials.candidate_units.candidate_units_hash,
        effective_units_hash=first.snapshot.effective_units_hash,
    )
    encoded = assembler.encode_startup_prerequisite_v1(record)
    decoded = preflight._decode_startup_prerequisite_v1(encoded)
    transaction = SimpleNamespace(
        request_id=decoded.request_id, startup_prerequisite_id=decoded.prerequisite_id,
        startup_prerequisite_digest=preflight._startup_prerequisite_digest_v1(encoded),
        required_head_frame_hash=live.D("c"),
    )
    materials = materials._replace(prerequisite=decoded, transaction=transaction)
    ownership_root = tmp_path / "ownership"
    live._install_prerequisite(ownership_root, materials, encoded)
    capture = SimpleNamespace(executables=SimpleNamespace(
        systemctl=SimpleNamespace(resolved=SimpleNamespace(canonical_path="/usr/bin/systemctl")),
    ))
    administrative = preflight._ObservedAdministrativeTcbForTestV1(
        preflight._ObservedAdministrativeTcbV1(materials, capture, ()),
    )
    monkeypatch.setattr(preflight, "_revalidate_captured_administrative_tcb_v1",
                        lambda *_a, **_k: None)
    versions = iter(observed_versions)
    monkeypatch.setattr(preflight, "_run_systemctl_show_v1", lambda _e, unit, _p: (
        {"Version": (next(versions),)} if unit is None else manager
    ))
    common = dict(
        ownership_root=ownership_root, live_root=live_root,
        administrative_links=tuple(tmp_path / "admin" / name
                                   for name in ("python", "openssl", "systemctl", "analyze")),
        administrative_root=tmp_path / "admin",
    )
    return administrative, common, encoded


@LINUX_ONLY
def test_native_double_observation_accepts_exact_signed_18(tmp_path, monkeypatch):
    administrative, common, encoded = _observation_fixture(
        tmp_path, monkeypatch, signed_version="255.4-1ubuntu8.18",
        observed_versions=("255.4-1ubuntu8.18", "255.4-1ubuntu8.18"),
    )
    observed = preflight._observe_effective_systemd_for_test_v1(administrative, **common)
    assert observed.observation.effective_systemd.manager_version == "255.4-1ubuntu8.18"
    assert observed.observation.prerequisite.content == encoded


@LINUX_ONLY
def test_supported_18_does_not_reinterpret_a_prerequisite_signed_for_17(tmp_path, monkeypatch):
    administrative, common, _encoded = _observation_fixture(
        tmp_path, monkeypatch, signed_version="255.4-1ubuntu8.17",
        observed_versions=("255.4-1ubuntu8.18", "255.4-1ubuntu8.18"),
    )
    with pytest.raises(preflight.PreflightError) as failure:
        preflight._observe_effective_systemd_for_test_v1(administrative, **common)
    assert failure.value.detail == (
        "effective systemd signed binding version observed=255.4-1ubuntu8.18 "
        "signed=255.4-1ubuntu8.17"
    )


@LINUX_ONLY
def test_switch_between_two_supported_versions_still_denies_ab_drift(tmp_path, monkeypatch):
    administrative, common, _encoded = _observation_fixture(
        tmp_path, monkeypatch, signed_version="255.4-1ubuntu8.17",
        observed_versions=("255.4-1ubuntu8.17", "255.4-1ubuntu8.18"),
    )
    with pytest.raises(preflight.PreflightError) as failure:
        preflight._observe_effective_systemd_for_test_v1(administrative, **common)
    assert failure.value.detail == "effective systemd A/B mismatch"
