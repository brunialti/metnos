"""One-shot administrative entry for a verified Executor Birth transition.

The source process can receive and build a release, but it cannot certify its
own code as that release.  This entry therefore transfers the exact signed
distribution record over a bounded pipe and lets the installed release verify
and complete the transition in a fresh process.  Exact repetition rebuilds the
same release and resumes from durable coordinator state.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping

_REPOSITORY = Path(__file__).resolve().parents[1]
_RUNTIME = _REPOSITORY / "runtime"
for _IMPORT_ROOT in (_REPOSITORY, _RUNTIME):
    if str(_IMPORT_ROOT) not in sys.path:
        sys.path.insert(0, str(_IMPORT_ROOT))
import executor_birth_account_identity as _account_identity  # noqa: E402


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ERROR_RE = re.compile(r"birth_[a-z0-9_]{1,96}\Z")
_FRAME_SCHEMA_V1 = "metnos.executor-birth.transition-handoff/1"
# The signed distribution ABI admits payloads through 16 MiB.  Its base64
# representation needs 4/3 of that space; keep a bounded allowance for the
# canonical envelope and the fixed signature/source fields.
_MAX_FRAME_BYTES_V1 = 24 * 1024 * 1024
_ACTIVATION_TIMEOUT_SECONDS_V1 = 300
# The closed release can contain the 20-minute contract convergence and then
# activate/verify the signed target topology.  Keep its parent budget larger
# than those bounded child operations plus the remaining verification work.
_CLOSED_RELEASE_TIMEOUT_SECONDS_V1 = 3000
_HOST_PROVISIONING_ERROR_CODES_V1 = frozenset({
    "birth_ownership_administrative_required",
    "birth_ownership_platform_unsupported",
    "birth_provisioning_host_invalid",
    "birth_provisioning_recovery_required",
})


class TransitionEntryError(RuntimeError):
    """Stable public failure for the one-shot transition entry."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> TransitionEntryError:
    return TransitionEntryError(code)


def _require_root_linux_v1() -> None:
    if not sys.platform.startswith("linux"):
        raise _fail("birth_ownership_platform_unsupported")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise _fail("birth_ownership_administrative_required")


def _service_environment_v1(service_user: object) -> tuple[str, Mapping[str, str]]:
    """Derive the fixed service paths before importing configuration modules."""
    if not _account_identity.is_posix_account_name_v1(service_user):
        raise _fail("birth_ownership_deployment_invalid")
    try:
        account = _account_identity.resolve_posix_account_v1(service_user)
    except _account_identity.PosixAccountResolutionError as exc:
        if exc.kind is _account_identity.PosixAccountFailureKindV1.platform_unsupported:
            raise _fail("birth_ownership_platform_unsupported") from exc
        raise _fail("birth_ownership_deployment_invalid") from exc
    home = Path(account.home)
    if account.name != service_user or not home.is_absolute() or home == Path("/"):
        raise _fail("birth_ownership_deployment_invalid")
    layout = _account_identity.metnos_xdg_layout_v1(account)
    return account.name, layout.environment()


def _provisioned_service_environment_v1(
    service_user: object,
) -> tuple[str, Mapping[str, str]]:
    """Provision first, then reject an identity change during fresh lookup."""
    from install.executor_birth_host_provisioning import (
        HostProvisioningError,
        provision_executor_birth_host_v1,
    )

    try:
        provisioned = provision_executor_birth_host_v1(service_user)
    except HostProvisioningError as exc:
        code = (
            exc.code if type(exc.code) is str
            and exc.code in _HOST_PROVISIONING_ERROR_CODES_V1
            else "birth_ownership_deployment_invalid"
        )
        raise _fail(code) from exc
    try:
        snapshot = provisioned.account
        if type(snapshot) is not _account_identity.PosixAccountSnapshotV1:
            raise TypeError("provisioned account snapshot")
        selected = snapshot.record.name
        environment = _account_identity.metnos_xdg_layout_v1(
            snapshot.record,
        ).environment()
        current = _account_identity.resolve_posix_account_snapshot_v1(selected)
        snapshot.assert_unchanged(current)
    except (
        TypeError,
        ValueError,
        _account_identity.PosixAccountResolutionError,
        _account_identity.PosixAccountSnapshotChangedError,
    ) as exc:
        raise _fail("birth_ownership_deployment_invalid") from exc
    return selected, environment


def _validated_legacy_inputs_v1(
    service_user: object,
    legacy_service_user: object,
    legacy_installation_root: object,
) -> tuple[str, Mapping[str, str], Path]:
    if not _account_identity.is_posix_account_name_v1(service_user):
        raise _fail("birth_ownership_deployment_invalid")
    selected, environment = _service_environment_v1(legacy_service_user)
    if selected == service_user:
        raise _fail("birth_ownership_deployment_invalid")
    try:
        root = Path(os.fspath(legacy_installation_root))
    except TypeError as exc:
        raise _fail("birth_ownership_deployment_invalid") from exc
    if (
        not root.is_absolute() or Path(os.path.abspath(root)) != root
        or root == Path("/")
    ):
        raise _fail("birth_ownership_deployment_invalid")
    return selected, environment, root


def _prepare_service_authorities_v1(
    service_user: str, service_environment: Mapping[str, str],
) -> None:
    """Run user-owned Birth preparation under the selected service identity."""
    from install.executor_birth_source_receiver import (
        _service_account_snapshot_v1,
    )

    account = _service_account_snapshot_v1(service_user)
    entry = _REPOSITORY / "install" / "executor_birth_transition.py"
    command = [
        sys.executable, "-I", entry.as_posix(), "prepare",
        "--service-user", service_user,
    ]
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True,
            check=False, close_fds=True, cwd="/",
            env=_install_environment_v1(_REPOSITORY, service_environment),
            user=account.uid, group=account.gid,
            extra_groups=account.supplementary_gids, umask=0o077,
            timeout=_ACTIVATION_TIMEOUT_SECONDS_V1,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _fail("birth_provisioning_io_unavailable") from exc
    if completed.returncode != 0:
        try:
            code = completed.stderr.decode("ascii").strip()
        except UnicodeDecodeError:
            code = ""
        raise _fail(
            code if _ERROR_RE.fullmatch(code) is not None
            else "birth_provisioning_io_unavailable"
        )
    if completed.stdout != b'{"prepared":true}\n':
        raise _fail("birth_provisioning_io_unavailable")


def _prepare_service_authorities_child_v1(service_user: object) -> dict:
    """Prepare only after the process identity matches the selected account."""
    selected_user, service_environment = _service_environment_v1(service_user)
    try:
        account = _account_identity.resolve_posix_account_v1(selected_user)
        supplementary = _account_identity.resolve_supplementary_gids_v1(
            selected_user, account.gid,
        )
    except _account_identity.PosixAccountResolutionError as exc:
        raise _fail("birth_ownership_deployment_invalid") from exc
    if (
        not hasattr(os, "geteuid")
        or os.geteuid() != account.uid
        or os.getegid() != account.gid
        or tuple(sorted(set(os.getgroups()))) != supplementary
    ):
        raise _fail("birth_ownership_deployment_invalid")
    os.environ.clear()
    os.environ.update(_install_environment_v1(_REPOSITORY, service_environment))
    from install.birth_authority_provisioner import (
        ensure_executor_birth_authorities_prepared,
    )

    ensure_executor_birth_authorities_prepared()
    return {"prepared": True}


def _install_environment_v1(
    root: Path, service_environment: Mapping[str, str],
) -> dict[str, str]:
    if not root.is_absolute() or not root.is_dir():
        raise _fail("birth_ownership_distribution_invalid")
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "METNOS_INSTALL_ROOT": root.as_posix(),
    }
    environment.update(service_environment)
    return environment


def _canonical_json_v1(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise _fail("birth_ownership_deployment_invalid") from exc


def _handoff_frame_v1(
    *, source_id: object, encoded: object, signature: object,
) -> bytes:
    if (
        type(source_id) is not str
        or _DIGEST_RE.fullmatch(source_id) is None
        or type(encoded) is not bytes
        or not encoded
        or type(signature) is not bytes
        or len(signature) != 64
    ):
        raise _fail("birth_ownership_distribution_invalid")
    frame = _canonical_json_v1({
        "distribution_payload": base64.b64encode(encoded).decode("ascii"),
        "distribution_signature": base64.b64encode(signature).decode("ascii"),
        "schema": _FRAME_SCHEMA_V1,
        "source_id": source_id,
    })
    if len(frame) > _MAX_FRAME_BYTES_V1:
        raise _fail("birth_ownership_distribution_invalid")
    return frame


def _decode_handoff_frame_v1(payload: object) -> tuple[str, bytes, bytes]:
    if type(payload) is not bytes or not payload or len(payload) > _MAX_FRAME_BYTES_V1:
        raise _fail("birth_ownership_distribution_invalid")
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("birth_ownership_distribution_invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "source_id", "distribution_payload",
            "distribution_signature",
        }
        or value.get("schema") != _FRAME_SCHEMA_V1
        or _canonical_json_v1(value) != payload
    ):
        raise _fail("birth_ownership_distribution_invalid")
    source_id = value.get("source_id")
    if type(source_id) is not str or _DIGEST_RE.fullmatch(source_id) is None:
        raise _fail("birth_ownership_distribution_invalid")
    try:
        encoded = base64.b64decode(value["distribution_payload"], validate=True)
        signature = base64.b64decode(
            value["distribution_signature"], validate=True,
        )
    except (TypeError, ValueError) as exc:
        raise _fail("birth_ownership_distribution_invalid") from exc
    if not encoded or len(signature) != 64:
        raise _fail("birth_ownership_distribution_invalid")
    return source_id, encoded, signature


def _public_error_code_v1(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if type(code) is str and _ERROR_RE.fullmatch(code) is not None:
        return code
    return "birth_ownership_recovery_required"


def _activate_signed_topology_v1(distribution: object, descriptor: object) -> dict:
    """Start only the target and readiness unit selected by the signed catalog."""
    from executor_birth_service_catalog import capture_current_service_catalog_v1

    loaded = capture_current_service_catalog_v1(distribution)
    targets = tuple(
        item.unit_name for item in loaded.catalog.entries
        if item.class_name == "target"
    )
    readiness = tuple(
        item.unit_name for item in loaded.catalog.entries if item.readiness_owner
    )
    if (
        len(targets) != 1 or targets[0] is None
        or len(readiness) != 1 or readiness[0] is None
    ):
        raise _fail("birth_transition_topology_invalid")
    environment = {
        "LANG": "C", "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }

    def run(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                [descriptor.systemctl_executable, *arguments],
                stdin=subprocess.DEVNULL, capture_output=True, check=False,
                close_fds=True, env=environment,
                timeout=_ACTIVATION_TIMEOUT_SECONDS_V1,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise _fail("birth_transition_activation_failed") from exc

    started = run("start", "--", targets[0])
    if started.returncode != 0:
        raise _fail("birth_transition_activation_failed")
    for unit in (targets[0], readiness[0]):
        active = run("is-active", "--quiet", "--", unit)
        if active.returncode != 0:
            raise _fail("birth_transition_activation_failed")
    return {"readiness_unit": readiness[0], "target_unit": targets[0]}


def _complete_closed_v1(
    *, expected_source_id: str, expected_service_user: str,
    expected_legacy_service_user: str,
    expected_legacy_installation_root: str,
    expected_service_state_root: object, frame: bytes,
) -> dict:
    source_id, encoded, signature = _decode_handoff_frame_v1(frame)
    if source_id != expected_source_id:
        raise _fail("birth_ownership_request_conflict")
    from executor_birth_distribution_manifest import (
        capture_current_deployment_descriptor_v1,
        verify_current_installation_distribution_v1,
    )
    from install.birth_authority_provisioner import complete_transition_cutover_v2

    distribution = verify_current_installation_distribution_v1(encoded, signature)
    distribution, descriptor = capture_current_deployment_descriptor_v1(
        distribution,
    )
    try:
        selected_state_root = Path(os.fspath(expected_service_state_root))
    except TypeError as exc:
        raise _fail("birth_ownership_request_conflict") from exc
    signed_state_root = (
        Path(descriptor.service_home) / ".local" / "state" / "metnos"
    )
    if (
        descriptor.service_user != expected_service_user
        or not selected_state_root.is_absolute()
        or Path(os.path.abspath(selected_state_root))
        != Path(os.path.abspath(signed_state_root))
    ):
        raise _fail("birth_ownership_request_conflict")
    result = complete_transition_cutover_v2(
        distribution, source_id,
        service_state_root=selected_state_root,
        legacy_service_user=expected_legacy_service_user,
        legacy_installation_root=expected_legacy_installation_root,
    )
    if getattr(getattr(result, "state", None), "value", None) != "PREFLIGHT_VERIFIED":
        raise _fail("birth_transition_final_state_missing")
    activated = _activate_signed_topology_v1(distribution, descriptor)
    return {
        **activated,
        "closed_build_id": distribution.identity.closed_build_id,
        "cutover_id": result.cutover_id,
        "request_id": result.request_id,
        "state": result.state.value,
    }


def _invoke_closed_release_v1(
    *, distribution: object, source_id: str, service_user: str,
    legacy_service_user: str, legacy_installation_root: str,
    service_environment: Mapping[str, str],
) -> dict:
    from executor_birth_distribution_manifest import (
        authenticate_distribution_record_v1,
    )
    from executor_birth_service_catalog import load_service_catalog_v1

    record = authenticate_distribution_record_v1(
        distribution.encoded, distribution.signature,
    )
    release_root = Path(record.installation_root)
    entry = release_root / "install" / "executor_birth_transition.py"
    matching = tuple(
        item for item in record.files
        if item.path == "install/executor_birth_transition.py"
    )
    if len(matching) != 1 or not entry.is_file():
        raise _fail("birth_ownership_distribution_invalid")
    loaded = load_service_catalog_v1(record)
    python_executables = {
        item.target_executable for item in loaded.catalog.entries
        if item.execution_kind == "python_module"
    }
    if (
        len(python_executables) != 1
        or None in python_executables
        or not Path(next(iter(python_executables))).is_file()
    ):
        raise _fail("birth_ownership_service_catalog_invalid")
    service_python = str(next(iter(python_executables)))
    frame = _handoff_frame_v1(
        source_id=source_id, encoded=distribution.encoded,
        signature=distribution.signature,
    )
    command = [
        service_python, "-I", "-B", entry.as_posix(), "complete",
        "--source-id", source_id, "--service-user", service_user,
        "--legacy-service-user", legacy_service_user,
        "--legacy-installation-root", legacy_installation_root,
    ]
    try:
        completed = subprocess.run(
            command, input=frame, capture_output=True, check=False,
            close_fds=True,
            env=_install_environment_v1(release_root, service_environment),
            timeout=_CLOSED_RELEASE_TIMEOUT_SECONDS_V1,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _fail("birth_transition_activation_failed") from exc
    if completed.returncode != 0:
        try:
            code = completed.stderr.decode("ascii").strip()
        except UnicodeDecodeError:
            code = ""
        raise _fail(
            code if _ERROR_RE.fullmatch(code) is not None
            else "birth_ownership_recovery_required"
        )
    try:
        result = json.loads(completed.stdout.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("birth_ownership_recovery_required") from exc
    if not isinstance(result, dict) or result.get("state") != "PREFLIGHT_VERIFIED":
        raise _fail("birth_ownership_recovery_required")
    return result


def deploy_source_v1(
    source: object, service_user: object, legacy_service_user: object,
    legacy_installation_root: object,
) -> dict:
    """Receive, build, cross and activate one exact reviewed source tree."""
    _require_root_linux_v1()
    selected_legacy_user, _legacy_environment, selected_legacy_root = (
        _validated_legacy_inputs_v1(
            service_user, legacy_service_user, legacy_installation_root,
        )
    )
    selected_user, service_environment = _provisioned_service_environment_v1(
        service_user,
    )
    if selected_legacy_user == selected_user:
        raise _fail("birth_ownership_deployment_invalid")
    os.environ.update(service_environment)
    os.environ["METNOS_INSTALL_ROOT"] = _REPOSITORY.as_posix()
    from install.birth_ownership_authority_provisioner import (
        provision_root_ownership_authorities_v1,
    )
    from install.executor_birth_distribution_release import (
        build_and_install_received_source_v1,
    )
    from install.executor_birth_source_receiver import _receive_source_v1

    _prepare_service_authorities_v1(selected_user, service_environment)
    provision_root_ownership_authorities_v1()
    source_id = _receive_source_v1(source, selected_user)
    distribution = build_and_install_received_source_v1(source_id)
    if distribution.release_sequence == 1:
        from install.executor_birth_systemd_quiescence import (
            quiesce_legacy_systemd_v1,
        )
        legacy_snapshot = _account_identity.resolve_posix_account_snapshot_v1(
            selected_legacy_user,
        )
        quiesce_legacy_systemd_v1(legacy_snapshot)
    return _invoke_closed_release_v1(
        distribution=distribution, source_id=source_id,
        service_user=selected_user,
        legacy_service_user=selected_legacy_user,
        legacy_installation_root=selected_legacy_root.as_posix(),
        service_environment=service_environment,
    )


def _parse_cli_v1(argv: object) -> tuple[str, str, str, str | None, str | None]:
    if type(argv) is not list or any(type(item) is not str for item in argv):
        raise _fail("birth_ownership_deployment_invalid")
    if (
        len(argv) == 9 and argv[0] == "deploy"
        and argv[1] == "--source" and argv[3] == "--service-user"
        and argv[5] == "--legacy-service-user"
        and argv[7] == "--legacy-installation-root"
    ):
        return "deploy", argv[2], argv[4], argv[6], argv[8]
    if (
        len(argv) == 9 and argv[0] == "complete"
        and argv[1] == "--source-id" and argv[3] == "--service-user"
        and argv[5] == "--legacy-service-user"
        and argv[7] == "--legacy-installation-root"
    ):
        return "complete", argv[2], argv[4], argv[6], argv[8]
    if (
        len(argv) == 3 and argv[0] == "prepare"
        and argv[1] == "--service-user"
    ):
        return "prepare", "", argv[2], None, None
    raise _fail("birth_ownership_deployment_invalid")


def main(argv: list[str] | None = None) -> int:
    # Root must not add ignored bytecode files to the reviewed source before
    # the receiver inventories it.  Keep this invariant independent of the
    # installer or operator command that invokes the transition entry.
    sys.dont_write_bytecode = True
    try:
        operation, value, service_user, legacy_service_user, legacy_root = _parse_cli_v1(
            list(sys.argv[1:] if argv is None else argv),
        )
        if operation == "prepare":
            result = _prepare_service_authorities_child_v1(service_user)
        else:
            _require_root_linux_v1()
            if legacy_service_user is None or legacy_root is None:
                raise _fail("birth_ownership_deployment_invalid")
            if operation == "deploy":
                result = deploy_source_v1(
                    value, service_user, legacy_service_user, legacy_root,
                )
                sys.stdout.write(
                    _canonical_json_v1(result).decode("ascii") + "\n"
                )
                return 0
            selected_user, service_environment = _service_environment_v1(
                service_user,
            )
            os.environ.update(service_environment)
            os.environ["METNOS_INSTALL_ROOT"] = _REPOSITORY.as_posix()
            frame = sys.stdin.buffer.read(_MAX_FRAME_BYTES_V1 + 1)
            result = _complete_closed_v1(
                expected_source_id=value,
                expected_service_user=selected_user,
                expected_legacy_service_user=legacy_service_user,
                expected_legacy_installation_root=legacy_root,
                expected_service_state_root=(
                    service_environment["METNOS_USER_STATE"]
                ),
                frame=frame,
            )
        sys.stdout.write(_canonical_json_v1(result).decode("ascii") + "\n")
        return 0
    except BaseException as exc:
        try:
            sys.stderr.write(_public_error_code_v1(exc) + "\n")
        except BaseException:
            pass
        return 78


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["deploy_source_v1", "main"]
