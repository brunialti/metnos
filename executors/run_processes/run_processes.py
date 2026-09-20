#!/usr/bin/env python3
"""Run packages registered on a Windows device, never arbitrary commands.

The canonical `run` verb is distinct from opening a web session. This
implementation handles it without accepting an executable path, command,
arguments, task name, or shell fragment. An exact registered identity selects
the provider: portable packages use the helper; AppX and desktop shortcuts
activate in the interactive user's session.

The executor is intentionally two-phase. Phase one verifies that every package
is installed and asks how long the launch permission should remain valid.
Phase two receives the runtime-owned consent token and starts the packages.
Reusable permission is recorded by the authenticated server, never here.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_SHIM_DIR", ""))

from executor_helpers import approval_digest, run_stdio  # noqa: E402
from messages import get as _msg  # noqa: E402


_PORTABLE_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:@-]{0,127}$")
_APPX_PACKAGE_ID = re.compile(r"^appx:[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")
_MAX_PACKAGES = 10
_HELPER_TIMEOUT_S = 15
_LIFETIMES = frozenset({"session", "persistent"})
_AUTHORIZATION_SCOPES = frozenset({"once", "until_restart", "always"})


def _failure(message_key: str, code: str, *, error_class: str = "invalid_input",
             **values) -> dict:
    message = _msg(message_key, **values)
    return {
        "ok": False,
        "results": [],
        "failed": [{
            "error": message,
            "error_code": code,
            "error_class": error_class,
        }],
        "ok_count": 0,
        "fail_count": 1,
        "error": message,
        "error_code": code,
        "error_class": error_class,
        # This helper is used only before any provider mutation.  The signed
        # per-execution undo contract therefore has an explicit, truthful
        # outcome instead of asking the runtime to infer one from an error.
        "_undo": {"outcome": "no_effect"},
    }


def _helper_call(*arguments: str) -> dict | None:
    """Call the authenticated helper through the Rust client.

    The client owns peer authentication. Duplicating pipe access here would
    create a second security implementation that could drift.
    """
    executable = os.environ.get("METNOS_CLIENT_EXE") or ""
    if not executable:
        return None
    try:
        process = subprocess.run(
            [executable, "helper", *arguments],
            capture_output=True,
            text=True,
            timeout=_HELPER_TIMEOUT_S,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed((process.stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _appx_call(*arguments: str) -> dict | None:
    """Call the user-session AppX resolver in the Rust client.

    This deliberately does not use the LocalSystem helper: Microsoft app
    activation targets the caller's current session, and session 0 is not the
    owner's visible desktop.
    """
    executable = os.environ.get("METNOS_CLIENT_EXE") or ""
    if not executable:
        return None
    try:
        process = subprocess.run(
            [executable, "package-app", *arguments],
            capture_output=True,
            text=True,
            timeout=_HELPER_TIMEOUT_S,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed((process.stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _identity_family(package_id: str) -> str | None:
    if package_id.startswith("desktop:"):
        from windows_desktop_apps import IDENTITY_RE
        return "desktop" if IDENTITY_RE.fullmatch(package_id) else None
    if _APPX_PACKAGE_ID.fullmatch(package_id):
        return "appx"
    if _PORTABLE_PACKAGE_ID.fullmatch(package_id):
        return "portable"
    return None


def _launch_call(package_id: str, operation: str, *arguments: str) -> dict | None:
    family = _identity_family(package_id)
    if family == "desktop":
        from windows_desktop_apps import call
        return call(package_id, operation, *arguments)
    if family == "appx":
        return _appx_call(operation, "--package-id", package_id, *arguments)
    if family == "portable":
        return _helper_call(operation, "--package-id", package_id, *arguments)
    return None


def _supported_lifetimes(package_ids: list[str]) -> tuple[str, ...]:
    """Intersection of resolver-family capabilities, in presentation order."""
    supported = set(_LIFETIMES)
    for package_id in package_ids:
        family = _identity_family(package_id)
        if family in {"appx", "desktop"}:
            supported.intersection_update({"session"})
        elif family != "portable":
            supported.clear()
    return tuple(value for value in ("session", "persistent") if value in supported)


def _machine_name() -> str:
    try:
        return socket.gethostname() or ""
    except OSError:
        return ""


def _boot_id() -> str:
    """Read the OS boot identity, not the lifetime of the Metnos process.

    Fixed local CIM query: no machine name, path or command from the caller.
    LastBootUpTime is a read-only OS property; UTC file time avoids locale
    formatting and changes when Windows is restarted.
    """
    root = os.environ.get("SystemRoot") or ""
    if not root or not Path(root).is_absolute():
        return ""
    executable = Path(root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    source = ("$ErrorActionPreference='Stop'; "
              "[Console]::Write((Get-CimInstance -ClassName Win32_OperatingSystem "
              "-Property LastBootUpTime).LastBootUpTime.ToUniversalTime().ToFileTimeUtc())")
    try:
        out = subprocess.run(
            [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", base64.b64encode(source.encode("utf-16le")).decode("ascii")],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=10, shell=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    value = (out.stdout or "").strip()
    return value if (out.returncode == 0 and re.fullmatch(r"[0-9]{1,20}", value)
                     and 0 < int(value) < 2**64) else ""


def _consent_token(package_ids: list[str], lifetime: str,
                   authorization_scope: str, authorization_boot_id: str) -> str:
    return approval_digest({"programs": package_ids, "lifetime": lifetime,
                            "authorization_scope": authorization_scope,
                            "authorization_boot_id": authorization_boot_id})


def _approval_dialog(
        package_ids: list[str],
        boot_id: str,
        package_names: list[str] | None = None) -> dict:
    machine = _machine_name() or _msg("MSG_CREATE_PROCESSES_MACHINE_UNKNOWN")

    def branch(scope: str) -> dict:
        boot = boot_id if scope == "until_restart" else ""
        return {
            "tool": "run_processes",
            "args": {
                "programs": package_ids,
                "lifetime": "session",
                "authorization_scope": scope,
                "authorization_boot_id": boot,
                "actor_consent_token": _consent_token(package_ids, "session", scope, boot),
            },
        }

    scopes = ("once", "until_restart", "always")
    choices = [{"label": _msg(key), "value": scope} for scope, key in (
        ("once", "MSG_RUN_PROGRAMS_ALLOW_ONCE"),
        ("until_restart", "MSG_RUN_PROGRAMS_ALLOW_UNTIL_RESTART"),
        ("always", "MSG_RUN_PROGRAMS_ALLOW_ALWAYS"))]
    choices.append({"label": _msg("MSG_BTN_REJECT"), "value": "reject"})

    description = _msg("MSG_RUN_PROGRAMS_AUTHORIZATION_DESCRIPTION",
                       packages=", ".join(package_names or package_ids), machine=machine)

    return {
        "title": _msg("MSG_CREATE_PROCESSES_APPROVAL_TITLE"),
        "description": description,
        "dialog": [{
            "var": "decision",
            "prompt": _msg("MSG_RUN_PROGRAMS_AUTHORIZATION_PROMPT"),
            "schema": {
                "kind": "choice",
                "choices": choices,
            },
        }],
        "fmt": "auto",
        "on_complete": {
            "type": "gate_dispatch",
            "branches": {scope: branch(scope) for scope in scopes},
        },
    }


def _launch_error(package_id: str, answer: dict | None, *, stopping: bool = False) -> dict:
    if answer is None:
        return {
            "package_id": package_id,
            "ok": False,
            "error": _msg("ERR_CREATE_PROCESSES_HELPER_UNAVAILABLE"),
            "error_code": "helper_unavailable",
            "error_class": "capability_missing",
        }
    if (not sys.platform.startswith("win")
            and answer.get("error_code") in {"helper_not_available", "platform_unsupported"}):
        return {
            "package_id": package_id, "ok": False,
            "error": _msg("ERR_CREATE_PROCESSES_WINDOWS_ONLY"),
            "error_code": "platform_unsupported", "error_class": "capability_missing",
        }
    if answer.get("aligned") is False:
        update_pending = answer.get("error_code") == "helper_update_pending"
        return {
            "package_id": package_id,
            "ok": False,
            "error": _msg(
                "ERR_CREATE_PROCESSES_HELPER_UPDATE_PENDING"
                if update_pending else "ERR_CREATE_PROCESSES_HELPER_MISMATCH",
                package=package_id,
            ),
            "error_code": (
                "helper_update_pending"
                if update_pending else "helper_protocol_mismatch"
            ),
            "error_class": "capability_missing",
        }
    code = str(answer.get("error_code") or "package_start_failed")
    message_key = ({
        "package_process_identity_mismatch": "ERR_CREATE_PROCESSES_STOP_IDENTITY",
        "package_stop_failed": "ERR_CREATE_PROCESSES_STOP_FAILED",
        "package_stop_unverified": "ERR_CREATE_PROCESSES_STOP_UNVERIFIED",
        "package_process_probe_failed": "ERR_CREATE_PROCESSES_STOP_FAILED",
    } if stopping else {
        # Launcher registration is not the installed-package inventory.
        # Its absence or a failed query cannot prove the app is uninstalled.
        "package_not_registered": "ERR_CREATE_PROCESSES_TARGET_MISSING",
        "package_operation_failed": "ERR_CREATE_PROCESSES_START_FAILED",
        "package_start_unsupported": "ERR_CREATE_PROCESSES_UNSUPPORTED",
        "package_persistence_unsupported": "ERR_CREATE_PROCESSES_UNSUPPORTED",
        "package_target_missing": "ERR_CREATE_PROCESSES_TARGET_MISSING",
        "package_target_ambiguous": "ERR_CREATE_PROCESSES_TARGET_AMBIGUOUS",
        "package_install_location_unavailable": "ERR_CREATE_PROCESSES_TARGET_MISSING",
        "package_target_unavailable": "ERR_CREATE_PROCESSES_TARGET_MISSING",
        "package_target_invalid": "ERR_CREATE_PROCESSES_TARGET_INVALID",
        "package_process_probe_failed": "ERR_CREATE_PROCESSES_PROCESS_PROBE_FAILED",
        "package_persistence_failed": "ERR_CREATE_PROCESSES_PERSISTENCE_FAILED",
        "package_start_failed": "ERR_CREATE_PROCESSES_START_FAILED",
        "package_start_unverified": "ERR_CREATE_PROCESSES_START_UNVERIFIED",
    }).get(code, (
        "ERR_CREATE_PROCESSES_STOP_FAILED"
        if stopping else "ERR_CREATE_PROCESSES_START_FAILED"))
    return {
        "package_id": package_id,
        "ok": False,
        "error": _msg(message_key, package=package_id, code=code),
        "error_code": code,
        "error_class": "resource_unavailable",
    }


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return _failure("ERR_ARGS_NOT_OBJECT", "args_not_object")

    raw_packages = args.get("programs")
    if raw_packages is None or raw_packages == []:
        return _failure(
            "ERR_ARG_NOT_NONEMPTY_STRING",
            "programs_missing",
            error_class="missing_input",
            arg="programs",
        )
    if not isinstance(raw_packages, list):
        return _failure("ERR_ARG_NOT_LIST", "programs_not_list", arg="programs")
    invalid_type = next(
        (value for value in raw_packages if not isinstance(value, str)), None)
    if invalid_type is not None:
        return _failure(
            "ERR_CREATE_PROCESSES_INVALID_PACKAGE",
            "invalid_package_id",
            package=repr(invalid_type)[:80],
        )
    package_ids = [value.strip() for value in raw_packages]
    if any(not value for value in package_ids):
        return _failure(
            "ERR_CREATE_PROCESSES_INVALID_PACKAGE",
            "invalid_package_id",
            package="",
        )
    if len(package_ids) > _MAX_PACKAGES:
        return _failure(
            "ERR_CREATE_PROCESSES_TOO_MANY",
            "too_many_packages",
            count=len(package_ids),
            maximum=_MAX_PACKAGES,
        )
    if len({package_id.casefold() for package_id in package_ids}) != len(package_ids):
        return _failure("ERR_CREATE_PROCESSES_DUPLICATE", "duplicate_package")
    invalid = next((value for value in package_ids
                    if _identity_family(value) is None), "")
    if invalid:
        return _failure(
            "ERR_CREATE_PROCESSES_INVALID_PACKAGE",
            "invalid_package_id",
            package=invalid[:80],
        )
    if not sys.platform.startswith("win") and not os.environ.get("METNOS_CLIENT_EXE"):
        return _failure(
            "ERR_CREATE_PROCESSES_WINDOWS_ONLY",
            "platform_unsupported",
            error_class="capability_missing",
        )

    lifetime = str(args.get("lifetime") or "").strip().lower()
    consent = str(args.get("actor_consent_token") or "").strip()
    scope = args.get("authorization_scope")
    approved_boot = args.get("authorization_boot_id")
    supported_lifetimes = _supported_lifetimes(package_ids)

    boot_id = ""
    if consent:
        if (lifetime not in supported_lifetimes
                or not isinstance(scope, str) or scope not in _AUTHORIZATION_SCOPES
                or not isinstance(approved_boot, str)
                or (scope == "until_restart" and not re.fullmatch(r"[0-9]{1,20}", approved_boot))
                or (scope != "until_restart" and approved_boot != "")
                or consent != _consent_token(package_ids, lifetime, scope, approved_boot)):
            return _failure("ERR_CREATE_PROCESSES_CONSENT_INVALID", "consent_invalid",
                            error_class="policy_denied")
        if scope == "until_restart":
            boot_id = _boot_id()
            if not boot_id:
                return _failure("ERR_RUN_PROGRAMS_BOOT_UNVERIFIED", "boot_unverified",
                                error_class="resource_unavailable")
            if boot_id != approved_boot:
                consent = ""  # Windows restarted: ask again, before any launch.

    if not consent:
        package_names = []
        for package_id in package_ids:
            answer = _launch_call(package_id, "query")
            if not answer or not answer.get("ok") or answer.get("aligned") is False:
                failed = _launch_error(package_id, answer)
                return {
                    "ok": False,
                    "results": [],
                    "failed": [failed],
                    "ok_count": 0,
                    "fail_count": 1,
                    "error": failed["error"],
                    "error_code": failed["error_code"],
                    "error_class": failed["error_class"],
                    "_undo": {"outcome": "no_effect"},
                }
            name = answer.get("name")
            package_names.append(name if isinstance(name, str) and name else package_id)
        boot_id = boot_id or _boot_id()
        if not boot_id:
            return _failure("ERR_RUN_PROGRAMS_BOOT_UNVERIFIED", "boot_unverified",
                            error_class="resource_unavailable")
        return {
            "ok": True,
            "decision": "needs_inputs",
            "started": False,
            "results": [],
            "failed": [],
            "ok_count": 0,
            "fail_count": 0,
            "_undo": {"outcome": "no_effect"},
            "needs_inputs": _approval_dialog(package_ids, boot_id, package_names),
        }

    results, failed, process_receipts = [], [], []
    untracked_mutation = False
    for package_id in package_ids:
        answer = _launch_call(
            package_id,
            "start",
            "--lifetime",
            lifetime,
        )
        if answer and answer.get("ok") and answer.get("aligned") is not False:
            payload = answer.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if (_identity_family(package_id) == "desktop"
                    and payload.get("visible_window") is not True):
                untracked_mutation = True
                failed.append(_launch_error(package_id, {
                    "error_code": "package_start_unverified"}))
                continue
            created_process = payload.get("created_process") is True
            if lifetime == "session" and created_process:
                process = payload.get("process")
                pid = process.get("pid") if isinstance(process, dict) else None
                creation_time = (
                    process.get("creation_time") if isinstance(process, dict)
                    else None)
                if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                        or not isinstance(creation_time, int)
                        or isinstance(creation_time, bool) or creation_time <= 0):
                    untracked_mutation = True
                    failed.append(_launch_error(package_id, {
                        "error_code": "managed_start_receipt_invalid"}))
                    continue
                receipt = {
                    "package_id": package_id,
                    "pid": pid,
                    "creation_time": creation_time,
                }
                if _identity_family(package_id) in {"appx", "desktop"}:
                    activation_boundary = payload.get("activation_boundary")
                    preexisting_processes = payload.get("preexisting_processes")
                    if (not isinstance(activation_boundary, int)
                            or isinstance(activation_boundary, bool)
                            or activation_boundary <= 0
                            or not isinstance(preexisting_processes, list)
                            or len(preexisting_processes) > 64
                            or any(
                                not isinstance(previous, dict)
                                or not isinstance(previous.get("pid"), int)
                                or isinstance(previous.get("pid"), bool)
                                or previous["pid"] <= 0
                                or not isinstance(
                                    previous.get("creation_time"), int)
                                or isinstance(
                                    previous.get("creation_time"), bool)
                                or previous["creation_time"] <= 0
                                for previous in preexisting_processes
                            )):
                        untracked_mutation = True
                        failed.append(_launch_error(package_id, {
                            "error_code": "managed_start_receipt_invalid"}))
                        continue
                    receipt.update({
                        "activation_boundary": activation_boundary,
                        "preexisting_processes": preexisting_processes,
                    })
                process_receipts.append(receipt)
            results.append({
                "package_id": package_id,
                "ok": True,
                "lifetime": lifetime,
                "already_running": not created_process,
            })
        else:
            if answer and answer.get("effects_attempted") is True:
                untracked_mutation = True
            failed.append(_launch_error(package_id, answer))

    outcome = (
        "irreversible" if untracked_mutation
        else "irreversible" if lifetime == "persistent" and results
        else "reversible" if process_receipts
        else "no_effect"
    )
    return {
        "ok": not failed,
        "started": bool(results),
        "lifetime": lifetime,
        "results": results,
        "failed": failed,
        "ok_count": len(results),
        "fail_count": len(failed),
        "partial": bool(results and failed),
        "_undo": {
            "outcome": outcome,
            **({"processes": process_receipts}
               if outcome == "reversible" else {}),
        },
        **({
            "error": failed[0]["error"],
            "error_code": failed[0]["error_code"],
            "error_class": failed[0]["error_class"],
        } if failed else {}),
    }


def reverse(_plan: dict, results: dict) -> dict:
    metadata = results.get("_undo") if isinstance(results, dict) else None
    receipts = metadata.get("processes") if isinstance(metadata, dict) else None
    if (not isinstance(receipts, list) or not receipts
            or len(receipts) > _MAX_PACKAGES):
        return {"ok": False, "results": [], "failed": [],
                "ok_count": 0, "fail_count": 1,
                "error_class": "invalid_receipt"}

    reversed_results, failed = [], []
    seen = set()
    for receipt in reversed(receipts):
        if not isinstance(receipt, dict):
            failed.append({"ok": False, "error_class": "invalid_receipt"})
            continue
        package_id = receipt.get("package_id")
        pid = receipt.get("pid")
        creation_time = receipt.get("creation_time")
        activation_boundary = receipt.get("activation_boundary")
        preexisting_processes = receipt.get("preexisting_processes")
        identity = (package_id, pid, creation_time)
        if (not isinstance(package_id, str)
                or _identity_family(package_id) is None
                or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                or not isinstance(creation_time, int)
                or isinstance(creation_time, bool) or creation_time <= 0
                or identity in seen):
            failed.append({"package_id": package_id, "ok": False,
                           "error_class": "invalid_receipt"})
            continue
        seen.add(identity)
        arguments = [
            "--pid", str(pid), "--creation-time", str(creation_time),
        ]
        if _identity_family(package_id) in {"appx", "desktop"}:
            if (not isinstance(activation_boundary, int)
                    or isinstance(activation_boundary, bool)
                    or activation_boundary <= 0
                    or not isinstance(preexisting_processes, list)
                    or len(preexisting_processes) > 64):
                failed.append({"package_id": package_id, "ok": False,
                               "error_class": "invalid_receipt"})
                continue
            encoded_preexisting = []
            valid_preexisting = True
            for process in preexisting_processes:
                if not isinstance(process, dict):
                    valid_preexisting = False
                    break
                previous_pid = process.get("pid")
                previous_creation = process.get("creation_time")
                if (not isinstance(previous_pid, int)
                        or isinstance(previous_pid, bool) or previous_pid <= 0
                        or not isinstance(previous_creation, int)
                        or isinstance(previous_creation, bool)
                        or previous_creation <= 0):
                    valid_preexisting = False
                    break
                encoded_preexisting.append(
                    f"{previous_pid}:{previous_creation}")
            if not valid_preexisting:
                failed.append({"package_id": package_id, "ok": False,
                               "error_class": "invalid_receipt"})
                continue
            arguments.extend([
                "--activation-boundary", str(activation_boundary),
            ])
            for process in encoded_preexisting:
                arguments.extend(["--preexisting-process", process])
        answer = _launch_call(package_id, "stop", *arguments)
        if answer and answer.get("ok") and answer.get("aligned") is not False:
            payload = answer.get("payload")
            restored = (payload.get("restored") is True
                        if isinstance(payload, dict) else False)
            stopped = (payload.get("stopped") is True
                       if isinstance(payload, dict) else False)
            # A transport/provider `ok` is not the undo postcondition.  The
            # provider must attest that the state was restored; otherwise an
            # already-dead activation PID can never become a false success.
            if restored:
                reversed_results.append({
                    "package_id": package_id,
                    "pid": pid,
                    "ok": True,
                    "stopped": stopped,
                })
            else:
                failed.append({
                    "package_id": package_id,
                    "ok": False,
                    "error_class": "postcondition_failed",
                })
        else:
            failed.append(_launch_error(package_id, answer, stopping=True))
    return {
        "ok": not failed,
        "results": reversed_results,
        "failed": failed,
        "ok_count": len(reversed_results),
        "fail_count": len(failed),
        "partial": bool(reversed_results and failed),
    }


def main() -> None:
    run_stdio(invoke)


if __name__ == "__main__":
    main()
