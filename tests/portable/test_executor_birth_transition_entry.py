"""Focused proofs for the closed-release transition process handoff."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from executor_birth_account_identity import PosixAccountRecordV1
from install import executor_birth_transition as transition


LINUX_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason="the productive transition entry switches a Linux installation",
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def test_handoff_frame_is_exact_bounded_and_round_trips() -> None:
    encoded = b'{"release_sequence":1}'
    signature = b"s" * 64
    frame = transition._handoff_frame_v1(
        source_id=D("1"), encoded=encoded, signature=signature,
    )

    assert transition._decode_handoff_frame_v1(frame) == (
        D("1"), encoded, signature,
    )
    with pytest.raises(
        transition.TransitionEntryError,
        match="birth_ownership_distribution_invalid",
    ):
        transition._decode_handoff_frame_v1(frame + b"\n")
    with pytest.raises(
        transition.TransitionEntryError,
        match="birth_ownership_distribution_invalid",
    ):
        transition._handoff_frame_v1(
            source_id=D("1"), encoded=encoded, signature=b"short",
        )


def test_handoff_bound_covers_the_distribution_payload_abi() -> None:
    from executor_birth_distribution_manifest import MAX_PAYLOAD_BYTES

    encoded = b"x" * MAX_PAYLOAD_BYTES
    frame = transition._handoff_frame_v1(
        source_id=D("1"), encoded=encoded, signature=b"s" * 64,
    )

    assert len(frame) <= transition._MAX_FRAME_BYTES_V1
    assert transition._decode_handoff_frame_v1(frame) == (
        D("1"), encoded, b"s" * 64,
    )


def test_source_process_invokes_only_the_verified_release_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import executor_birth_distribution_manifest as manifest
    import executor_birth_service_catalog as catalog

    release = tmp_path / "release"
    entry = release / "install" / "executor_birth_transition.py"
    entry.parent.mkdir(parents=True)
    entry.write_bytes(b"# verified entry\n")
    managed_python = tmp_path / "python-env" / "bin" / "python"
    managed_python.parent.mkdir(parents=True)
    managed_python.write_bytes(b"#!/bin/sh\n")
    distribution = SimpleNamespace(
        installation_root="/forged/source-context-root",
        files=(),
        encoded=b"distribution",
        signature=b"s" * 64,
    )
    record = SimpleNamespace(
        installation_root=release.as_posix(),
        files=(SimpleNamespace(path="install/executor_birth_transition.py"),),
    )
    observed = {}
    monkeypatch.setattr(
        manifest, "authenticate_distribution_record_v1",
        lambda encoded, signature: record
        if encoded == distribution.encoded and signature == distribution.signature
        else pytest.fail("wrong distribution material"),
    )
    monkeypatch.setattr(
        catalog, "load_service_catalog_v1",
        lambda candidate: SimpleNamespace(catalog=SimpleNamespace(entries=(
            SimpleNamespace(
                execution_kind="python_module",
                target_executable=managed_python.as_posix(),
            ),
            SimpleNamespace(
                execution_kind="python_module",
                target_executable=managed_python.as_posix(),
            ),
            SimpleNamespace(execution_kind="none", target_executable=None),
        ))) if candidate is record else pytest.fail("wrong record"),
    )
    monkeypatch.setattr(
        catalog, "capture_current_service_catalog_v1",
        lambda *_args: pytest.fail("source context cannot claim to be current"),
    )

    def run(command, **kwargs):
        observed.update({"command": command, **kwargs})
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({
                "state": "PREFLIGHT_VERIFIED",
                "target_unit": "metnos.target",
            }).encode("ascii"),
            stderr=b"",
        )

    monkeypatch.setattr(transition.subprocess, "run", run)
    result = transition._invoke_closed_release_v1(
        distribution=distribution,
        source_id=D("2"),
        service_user="metnos",
        legacy_service_user="legacy-metnos",
        legacy_installation_root="/opt/metnos",
        service_environment={"HOME": "/srv/metnos", "USER": "metnos"},
    )

    assert result["state"] == "PREFLIGHT_VERIFIED"
    assert observed["command"] == [
        managed_python.as_posix(), "-I", "-B", entry.as_posix(), "complete",
        "--source-id", D("2"), "--service-user", "metnos",
        "--legacy-service-user", "legacy-metnos",
        "--legacy-installation-root", "/opt/metnos",
    ]
    assert transition._decode_handoff_frame_v1(observed["input"])[0] == D("2")
    assert observed["env"]["METNOS_INSTALL_ROOT"] == release.as_posix()
    assert observed["env"]["HOME"] == "/srv/metnos"


def test_closed_release_timeout_covers_convergence_and_activation() -> None:
    from install import birth_authority_provisioner as provisioner

    assert transition._CLOSED_RELEASE_TIMEOUT_SECONDS_V1 > (
        provisioner._CONTRACT_CONVERGENCE_TIMEOUT_SECONDS_V2
        + 3 * transition._ACTIVATION_TIMEOUT_SECONDS_V1
        + 600
    )


@LINUX_ONLY
def test_closed_process_binds_distribution_source_user_and_final_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import executor_birth_distribution_manifest as manifest
    from install import birth_authority_provisioner as provisioner

    distribution = SimpleNamespace(
        identity=SimpleNamespace(closed_build_id=D("3")),
    )
    descriptor = SimpleNamespace(
        service_user="metnos", service_home="/srv/metnos",
    )
    result = SimpleNamespace(
        state=SimpleNamespace(value="PREFLIGHT_VERIFIED"),
        cutover_id=D("4"), request_id=D("5"),
    )
    monkeypatch.setattr(
        manifest, "verify_current_installation_distribution_v1",
        lambda encoded, signature: distribution
        if encoded == b"distribution" and signature == b"s" * 64
        else pytest.fail("unexpected distribution"),
    )
    monkeypatch.setattr(
        manifest, "capture_current_deployment_descriptor_v1",
        lambda candidate: (candidate, descriptor),
    )
    monkeypatch.setattr(
        provisioner, "complete_transition_cutover_v2",
        lambda candidate, source_id, *, service_state_root,
        legacy_service_user, legacy_installation_root: result
        if (
            candidate is distribution
            and source_id == D("6")
            and legacy_service_user == "legacy-metnos"
            and legacy_installation_root == "/opt/metnos"
            and Path(service_state_root)
            == Path("/srv/metnos/.local/state/metnos")
        )
        else pytest.fail("transition binding changed"),
    )
    monkeypatch.setattr(
        transition, "_activate_signed_topology_v1",
        lambda candidate, bound_descriptor: {
            "target_unit": "metnos.target",
            "readiness_unit": "metnos-stack-ready.service",
        } if candidate is distribution and bound_descriptor is descriptor
        else pytest.fail("activation binding changed"),
    )
    frame = transition._handoff_frame_v1(
        source_id=D("6"), encoded=b"distribution", signature=b"s" * 64,
    )

    completed = transition._complete_closed_v1(
        expected_source_id=D("6"),
        expected_service_user="metnos",
        expected_legacy_service_user="legacy-metnos",
        expected_legacy_installation_root="/opt/metnos",
        expected_service_state_root="/srv/metnos/.local/state/metnos",
        frame=frame,
    )
    assert completed == {
        "target_unit": "metnos.target",
        "readiness_unit": "metnos-stack-ready.service",
        "closed_build_id": D("3"),
        "cutover_id": D("4"),
        "request_id": D("5"),
        "state": "PREFLIGHT_VERIFIED",
    }


def test_closed_process_rejects_a_state_root_outside_the_signed_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import executor_birth_distribution_manifest as manifest
    from install import birth_authority_provisioner as provisioner

    distribution = SimpleNamespace(identity=SimpleNamespace(closed_build_id=D("7")))
    descriptor = SimpleNamespace(
        service_user="metnos", service_home="/srv/metnos",
    )
    monkeypatch.setattr(
        manifest, "verify_current_installation_distribution_v1",
        lambda *_args: distribution,
    )
    monkeypatch.setattr(
        manifest, "capture_current_deployment_descriptor_v1",
        lambda candidate: (candidate, descriptor),
    )
    monkeypatch.setattr(
        provisioner, "complete_transition_cutover_v2",
        lambda *_args, **_kwargs: pytest.fail(
            "identity mismatch must be rejected before cutover",
        ),
    )
    frame = transition._handoff_frame_v1(
        source_id=D("8"), encoded=b"distribution", signature=b"s" * 64,
    )

    with pytest.raises(
        transition.TransitionEntryError,
        match="birth_ownership_request_conflict",
    ):
        transition._complete_closed_v1(
            expected_source_id=D("8"),
            expected_service_user="metnos",
            expected_legacy_service_user="legacy-metnos",
            expected_legacy_installation_root="/opt/metnos",
            expected_service_state_root="/root/.local/state/metnos",
            frame=frame,
        )


def test_activation_uses_only_target_and_readiness_from_signed_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import executor_birth_service_catalog as catalog

    loaded = SimpleNamespace(catalog=SimpleNamespace(entries=(
        SimpleNamespace(
            class_name="target", unit_name="metnos.target",
            readiness_owner=False,
        ),
        SimpleNamespace(
            class_name="gated_service",
            unit_name="metnos-stack-ready.service",
            readiness_owner=True,
        ),
    )))
    monkeypatch.setattr(
        catalog, "capture_current_service_catalog_v1",
        lambda distribution: loaded,
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(transition.subprocess, "run", run)
    assert transition._activate_signed_topology_v1(
        object(), SimpleNamespace(systemctl_executable="/usr/bin/systemctl"),
    ) == {
        "target_unit": "metnos.target",
        "readiness_unit": "metnos-stack-ready.service",
    }
    assert [item[0] for item in calls] == [
        ["/usr/bin/systemctl", "start", "--", "metnos.target"],
        [
            "/usr/bin/systemctl", "is-active", "--quiet", "--",
            "metnos.target",
        ],
        [
            "/usr/bin/systemctl", "is-active", "--quiet", "--",
            "metnos-stack-ready.service",
        ],
    ]


@LINUX_ONLY
def test_service_authority_preparation_runs_as_the_selected_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from install import executor_birth_source_receiver as receiver

    account = SimpleNamespace(
        uid=991, gid=992, supplementary_gids=(44, 992),
    )
    observed = {}
    monkeypatch.setattr(
        receiver, "_service_account_snapshot_v1",
        lambda name: account if name == "metnos" else None,
    )

    def run(command, **kwargs):
        observed.update({"command": command, **kwargs})
        return subprocess.CompletedProcess(
            command, 0, stdout=b'{"prepared":true}\n', stderr=b"",
        )

    monkeypatch.setattr(transition.subprocess, "run", run)
    environment = {
        "HOME": "/srv/metnos", "LOGNAME": "metnos", "USER": "metnos",
    }
    transition._prepare_service_authorities_v1("metnos", environment)

    assert observed["user"] == 991
    assert observed["group"] == 992
    assert observed["extra_groups"] == (44, 992)
    assert observed["cwd"] == "/"
    assert observed["umask"] == 0o077
    assert observed["env"]["HOME"] == "/srv/metnos"
    assert observed["command"][-3:] == ["prepare", "--service-user", "metnos"]


@LINUX_ONLY
def test_service_environment_binds_every_root_to_the_account_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All service roots follow the selected account on every platform."""
    account = PosixAccountRecordV1(
        name="metnos", uid=991, gid=992,
        home="/srv/metnos", shell="/usr/sbin/nologin",
    )
    monkeypatch.setattr(
        transition._account_identity, "resolve_posix_account_v1",
        lambda name: account if name == "metnos" else None,
    )

    selected, environment = transition._service_environment_v1("metnos")

    assert selected == "metnos"
    assert environment == {
        "HOME": "/srv/metnos",
        "LOGNAME": "metnos",
        "USER": "metnos",
        "METNOS_USER_DATA": "/srv/metnos/.local/share/metnos",
        "METNOS_USER_STATE": "/srv/metnos/.local/state/metnos",
        "METNOS_USER_CONFIG": "/srv/metnos/.config/metnos",
        "METNOS_USER_CACHE": "/srv/metnos/.cache/metnos",
        "METNOS_WORKSPACE": "/srv/metnos/.local/share/metnos/workspace",
    }


@LINUX_ONLY
def test_cli_disables_bytecode_before_deployment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The privileged entry never mutates its reviewed source with pyc files."""
    observed = {}
    monkeypatch.setattr(transition.sys, "dont_write_bytecode", False)
    monkeypatch.setattr(transition, "_require_root_linux_v1", lambda: None)

    def deploy(*args):
        observed["disabled"] = transition.sys.dont_write_bytecode
        return {"state": "PREFLIGHT_VERIFIED"}

    monkeypatch.setattr(transition, "deploy_source_v1", deploy)
    result = transition.main([
        "deploy", "--source", "/reviewed/source",
        "--service-user", "metnos",
        "--legacy-service-user", "legacy-metnos",
        "--legacy-installation-root", "/opt/metnos",
    ])

    assert result == 0
    assert observed == {"disabled": True}
    assert capsys.readouterr().out == '{"state":"PREFLIGHT_VERIFIED"}\n'
