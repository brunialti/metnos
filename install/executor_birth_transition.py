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

try:
    import pwd
except ImportError:  # pragma: no cover - exercised by the Windows gate
    pwd = None


_REPOSITORY = Path(__file__).resolve().parents[1]
_RUNTIME = _REPOSITORY / "runtime"
for _IMPORT_ROOT in (_REPOSITORY, _RUNTIME):
    if str(_IMPORT_ROOT) not in sys.path:
        sys.path.insert(0, str(_IMPORT_ROOT))
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_ERROR_RE = re.compile(r"birth_[a-z0-9_]{1,96}\Z")
_FRAME_SCHEMA_V1 = "metnos.executor-birth.transition-handoff/1"
_MAX_FRAME_BYTES_V1 = 4 * 1024 * 1024
_ACTIVATION_TIMEOUT_SECONDS_V1 = 300


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
    if type(service_user) is not str or _ACCOUNT_RE.fullmatch(service_user) is None:
        raise _fail("birth_ownership_deployment_invalid")
    if pwd is None:
        raise _fail("birth_ownership_platform_unsupported")
    try:
        account = pwd.getpwnam(service_user)
    except (KeyError, OSError) as exc:
        raise _fail("birth_ownership_deployment_invalid") from exc
    home = Path(account.pw_dir)
    if not home.is_absolute() or home == Path("/"):
        raise _fail("birth_ownership_deployment_invalid")
    data = home / ".local" / "share" / "metnos"
    state = home / ".local" / "state" / "metnos"
    config = home / ".config" / "metnos"
    cache = home / ".cache" / "metnos"
    return service_user, {
        "HOME": home.as_posix(),
        "LOGNAME": service_user,
        "USER": service_user,
        "METNOS_USER_DATA": data.as_posix(),
        "METNOS_USER_STATE": state.as_posix(),
        "METNOS_USER_CONFIG": config.as_posix(),
        "METNOS_USER_CACHE": cache.as_posix(),
        "METNOS_WORKSPACE": (data / "workspace").as_posix(),
    }


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
    service_environment: Mapping[str, str],
) -> dict:
    release_root = Path(distribution.installation_root)
    entry = release_root / "install" / "executor_birth_transition.py"
    matching = tuple(
        item for item in distribution.files
        if item.path == "install/executor_birth_transition.py"
    )
    if len(matching) != 1 or not entry.is_file():
        raise _fail("birth_ownership_distribution_invalid")
    frame = _handoff_frame_v1(
        source_id=source_id, encoded=distribution.encoded,
        signature=distribution.signature,
    )
    command = [
        sys.executable, "-I", entry.as_posix(), "complete",
        "--source-id", source_id, "--service-user", service_user,
    ]
    try:
        completed = subprocess.run(
            command, input=frame, capture_output=True, check=False,
            close_fds=True,
            env=_install_environment_v1(release_root, service_environment),
            timeout=_ACTIVATION_TIMEOUT_SECONDS_V1 * 2,
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


def deploy_source_v1(source: object, service_user: object) -> dict:
    """Receive, build, cross and activate one exact reviewed source tree."""
    _require_root_linux_v1()
    selected_user, service_environment = _service_environment_v1(service_user)
    os.environ.update(service_environment)
    os.environ["METNOS_INSTALL_ROOT"] = _REPOSITORY.as_posix()
    from install.birth_authority_provisioner import (
        ensure_executor_birth_authorities_prepared,
    )
    from install.birth_ownership_authority_provisioner import (
        provision_root_ownership_authorities_v1,
    )
    from install.executor_birth_distribution_release import (
        build_and_install_received_source_v1,
    )
    from install.executor_birth_source_receiver import _receive_source_v1

    ensure_executor_birth_authorities_prepared()
    provision_root_ownership_authorities_v1()
    source_id = _receive_source_v1(source, selected_user)
    distribution = build_and_install_received_source_v1(source_id)
    return _invoke_closed_release_v1(
        distribution=distribution, source_id=source_id,
        service_user=selected_user, service_environment=service_environment,
    )


def _parse_cli_v1(argv: object) -> tuple[str, str, str]:
    if type(argv) is not list or any(type(item) is not str for item in argv):
        raise _fail("birth_ownership_deployment_invalid")
    if (
        len(argv) == 5 and argv[0] == "deploy"
        and argv[1] == "--source" and argv[3] == "--service-user"
    ):
        return "deploy", argv[2], argv[4]
    if (
        len(argv) == 5 and argv[0] == "complete"
        and argv[1] == "--source-id" and argv[3] == "--service-user"
    ):
        return "complete", argv[2], argv[4]
    raise _fail("birth_ownership_deployment_invalid")


def main(argv: list[str] | None = None) -> int:
    try:
        _require_root_linux_v1()
        operation, value, service_user = _parse_cli_v1(
            list(sys.argv[1:] if argv is None else argv),
        )
        if operation == "deploy":
            result = deploy_source_v1(value, service_user)
        else:
            selected_user, service_environment = _service_environment_v1(
                service_user,
            )
            os.environ.update(service_environment)
            os.environ["METNOS_INSTALL_ROOT"] = _REPOSITORY.as_posix()
            frame = sys.stdin.buffer.read(_MAX_FRAME_BYTES_V1 + 1)
            result = _complete_closed_v1(
                expected_source_id=value,
                expected_service_user=selected_user,
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
