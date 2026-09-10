"""Focused proofs for the closed-release transition process handoff."""
from __future__ import annotations

from contextlib import contextmanager

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


@pytest.mark.parametrize("sequence", [1, 2, 3])
def test_deploy_disables_legacy_units_only_for_the_initial_release(
    monkeypatch, tmp_path, sequence,
):
    from install import birth_ownership_authority_provisioner as authorities
    from install import executor_birth_distribution_release as release
    from install import executor_birth_source_receiver as receiver
    from install import executor_birth_systemd_quiescence as quiescence

    events = []
    candidate = SimpleNamespace(release_sequence=sequence)
    account = object()
    monkeypatch.setattr(transition, "_require_root_linux_v1", lambda: None)
    monkeypatch.setattr(transition, "_validated_legacy_inputs_v1", lambda *_: (
        "legacy", {}, tmp_path,
    ))
    monkeypatch.setattr(transition, "_provisioned_service_environment_v1", lambda *_: (
        "metnos", {},
    ))
    # deploy updates this one environment variable, restored after the proof.
    monkeypatch.setenv("METNOS_INSTALL_ROOT", tmp_path.as_posix())
    monkeypatch.setattr(transition, "_prepare_service_authorities_v1", lambda *_: None)
    monkeypatch.setattr(authorities, "provision_root_ownership_authorities_v1", lambda: None)
    monkeypatch.setattr(receiver, "_receive_source_v1", lambda *_: D("1"))
    monkeypatch.setattr(release, "build_and_install_received_source_v1", lambda *_: candidate)
    monkeypatch.setattr(
        transition._account_identity, "resolve_posix_account_snapshot_v1",
        lambda *_: account if sequence == 1 else pytest.fail("legacy census on update"),
    )
    monkeypatch.setattr(quiescence, "quiesce_legacy_systemd_v1", lambda value: (
        events.append(("legacy", value)),
    ))
    monkeypatch.setattr(transition, "_invoke_closed_release_v1", lambda **kwargs: (
        events.append(("closed", kwargs["distribution"])) or {"state": "PREFLIGHT_VERIFIED"}
    ))

    assert transition.deploy_source_v1("source", "metnos", "legacy", tmp_path) == {
        "state": "PREFLIGHT_VERIFIED",
    }
    assert events == (
        [("legacy", account), ("closed", candidate)] if sequence == 1
        else [("closed", candidate)]
    )


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
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
    held = _hold_test_deployment_lock(monkeypatch, tmp_path)
    import executor_birth_ownership_coordinator as coordinator

    monkeypatch.setattr(
        coordinator, "_completed_transition_locked_v2",
        lambda session, candidate: SimpleNamespace(
            request_id=D("5"), cutover_id=D("4"),
        ) if session is held["session"] and candidate is distribution
        else pytest.fail("final identity binding changed"),
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
        legacy_service_user, legacy_installation_root, deployment_session: result
        if (
            deployment_session is held["session"]
            and candidate is distribution
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


def _lock_root(base: Path) -> Path:
    """A directory the product accepts as a deployment lock root: exactly 0o755."""
    root = base / "ownership"
    root.mkdir(exist_ok=True)
    root.chmod(0o755)
    return root


def _hold_test_deployment_lock(monkeypatch, base: Path) -> dict:
    """Swap the fixed root-owned deployment lock for the same lock on a test root."""
    import executor_birth_ownership_coordinator as coordinator

    root = _lock_root(base)
    held: dict = {}

    @contextmanager
    def lock():
        with coordinator._deployment_lock_for_test_v1(root) as session:
            held["session"] = session
            yield session

    monkeypatch.setattr(coordinator, "_deployment_lock_v1", lock)
    return held


def _deployment_lock_is_free(base: Path) -> bool:
    """Try the deployment lock from another open file description, never waiting."""
    import fcntl
    import executor_birth_ownership_coordinator as coordinator

    fd = os.open(
        _lock_root(base) / coordinator.DEPLOYMENT_LOCK_BASENAME_V1, os.O_RDWR,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def _closed_process(monkeypatch, root: Path, *, final, during_activation=None):
    """The closed entry with completion, activation and selection simulated."""
    import executor_birth_distribution_manifest as manifest
    import executor_birth_ownership_coordinator as coordinator
    from install import birth_authority_provisioner as provisioner

    distribution = SimpleNamespace(identity=SimpleNamespace(closed_build_id=D("3")))
    descriptor = SimpleNamespace(service_user="metnos", service_home="/srv/metnos")
    held = _hold_test_deployment_lock(monkeypatch, root)
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
        lambda *_args, deployment_session, **_kwargs: SimpleNamespace(
            state=SimpleNamespace(value="PREFLIGHT_VERIFIED"),
            cutover_id=D("4"), request_id=D("5"),
        ) if deployment_session is held["session"]
        else pytest.fail("completion did not receive the held session"),
    )

    def activate(*_args):
        if during_activation is not None:
            during_activation()
        return {"target_unit": "metnos.target",
                "readiness_unit": "metnos-stack-ready.service"}

    monkeypatch.setattr(transition, "_activate_signed_topology_v1", activate)
    monkeypatch.setattr(
        coordinator, "_completed_transition_locked_v2",
        lambda session, _candidate: final
        if session is held["session"]
        else pytest.fail("final identity not reread under the held session"),
    )
    frame = transition._handoff_frame_v1(
        source_id=D("6"), encoded=b"distribution", signature=b"s" * 64,
    )
    return lambda: transition._complete_closed_v1(
        expected_source_id=D("6"),
        expected_service_user="metnos",
        expected_legacy_service_user="legacy-metnos",
        expected_legacy_installation_root="/opt/metnos",
        expected_service_state_root="/srv/metnos/.local/state/metnos",
        frame=frame,
    )


@LINUX_ONLY
def test_the_deployment_lock_is_held_through_activation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Review A-05: completion used to take and drop the lock by itself.

    Between completion and activation an administrative N+1 could advance,
    and the entry would still report N as the release it had started. The
    lock is now taken once by the entry and still held while the topology
    starts; a competitor on another file description cannot take it.
    """
    observed = []
    run = _closed_process(
        monkeypatch, tmp_path,
        final=SimpleNamespace(request_id=D("5"), cutover_id=D("4")),
        during_activation=lambda: observed.append(
            _deployment_lock_is_free(tmp_path)),
    )
    assert run()["closed_build_id"] == D("3")
    assert observed == [False]
    assert _deployment_lock_is_free(tmp_path)


@LINUX_ONLY
@pytest.mark.parametrize("final", (
    None,
    SimpleNamespace(request_id=D("9"), cutover_id=D("4")),
    SimpleNamespace(request_id=D("5"), cutover_id=D("9")),
))
def test_a_selection_that_moved_is_not_reported_as_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, final,
) -> None:
    """The reviewer's interleaving: the selection is already N+1 at the end.

    The entry must not return N's identity as the one it started. Reread under
    the same lock, the final transaction for this release is gone or belongs
    to another request, and that is a failure with its own name.
    """
    run = _closed_process(monkeypatch, tmp_path, final=final)
    with pytest.raises(
        transition.TransitionEntryError,
        match="birth_transition_selection_changed",
    ):
        run()


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
