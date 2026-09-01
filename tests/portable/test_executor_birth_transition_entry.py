"""Focused proofs for the closed-release transition process handoff."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from install import executor_birth_transition as transition


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


def test_source_process_invokes_only_the_verified_release_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    entry = release / "install" / "executor_birth_transition.py"
    entry.parent.mkdir(parents=True)
    entry.write_bytes(b"# verified entry\n")
    distribution = SimpleNamespace(
        installation_root=release.as_posix(),
        files=(SimpleNamespace(path="install/executor_birth_transition.py"),),
        encoded=b"distribution",
        signature=b"s" * 64,
    )
    observed = {}

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
        service_environment={"HOME": "/srv/metnos", "USER": "metnos"},
    )

    assert result["state"] == "PREFLIGHT_VERIFIED"
    assert observed["command"] == [
        transition.sys.executable, "-I", entry.as_posix(), "complete",
        "--source-id", D("2"), "--service-user", "metnos",
    ]
    assert transition._decode_handoff_frame_v1(observed["input"])[0] == D("2")
    assert observed["env"]["METNOS_INSTALL_ROOT"] == release.as_posix()
    assert observed["env"]["HOME"] == "/srv/metnos"


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
        lambda candidate, source_id, *, service_state_root: result
        if (
            candidate is distribution
            and source_id == D("6")
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


def test_service_environment_binds_every_root_to_the_account_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All service roots follow the selected account on every platform."""
    account = SimpleNamespace(pw_dir="/srv/metnos")
    monkeypatch.setattr(
        transition, "pwd",
        SimpleNamespace(getpwnam=lambda name: account if name == "metnos" else None),
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
