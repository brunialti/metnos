#!/usr/bin/env python3
"""Start packages registered on a Windows device, never arbitrary commands.

The public executor already exists in the canonical Metnos vocabulary. This
implementation makes it real without accepting an executable path, command,
arguments, task name, or shell fragment. The elevated helper resolves an exact
package identifier from machine-owned installation metadata (ADR 0211).

The executor is intentionally two-phase. Phase one verifies that every package
is installed and asks whether it should run until restart or at every startup.
Phase two receives the runtime-owned consent token and starts the packages.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys

sys.path.insert(0, os.environ.get("METNOS_SHIM_DIR", ""))

from executor_helpers import run_stdio  # noqa: E402
from messages import get as _msg  # noqa: E402


_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:@-]{0,127}$")
_MAX_PACKAGES = 10
_HELPER_TIMEOUT_S = 15
_LIFETIMES = frozenset({"session", "persistent"})


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
    }


def _helper_call(*arguments: str) -> dict | None:
    """Call the authenticated helper through the Rust client.

    The client owns peer authentication. Duplicating pipe access here would
    create a second security implementation that could drift.
    """
    executable = os.environ.get("METNOS_CLIENT_EXE") or ""
    if not executable or not sys.platform.startswith("win"):
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


def _machine_name() -> str:
    try:
        return socket.gethostname() or ""
    except OSError:
        return ""


def _consent_token(package_ids: list[str], lifetime: str) -> str:
    payload = json.dumps(
        {"packages": package_ids, "lifetime": lifetime},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _approval_dialog(package_ids: list[str]) -> dict:
    machine = _machine_name() or _msg("MSG_CREATE_PROCESSES_MACHINE_UNKNOWN")

    def branch(lifetime: str) -> dict:
        return {
            "tool": "create_processes",
            "args": {
                "programs": package_ids,
                "lifetime": lifetime,
                "actor_consent_token": _consent_token(package_ids, lifetime),
            },
        }

    return {
        "title": _msg("MSG_CREATE_PROCESSES_APPROVAL_TITLE"),
        "description": _msg(
            "MSG_CREATE_PROCESSES_APPROVAL_DESCRIPTION",
            packages=", ".join(package_ids),
            machine=machine,
        ),
        "dialog": [{
            "var": "decision",
            "prompt": _msg("MSG_CREATE_PROCESSES_APPROVAL_PROMPT"),
            "schema": {
                "kind": "choice",
                "choices": [
                    {
                        "label": _msg("MSG_CREATE_PROCESSES_BTN_SESSION"),
                        "value": "session",
                    },
                    {
                        "label": _msg("MSG_CREATE_PROCESSES_BTN_PERSISTENT"),
                        "value": "persistent",
                    },
                    {"label": _msg("MSG_BTN_REJECT"), "value": "reject"},
                ],
            },
        }],
        "fmt": "auto",
        "on_complete": {
            "type": "gate_dispatch",
            "branches": {
                "session": branch("session"),
                "persistent": branch("persistent"),
            },
        },
    }


def _helper_error(package_id: str, answer: dict | None, *, stopping: bool = False) -> dict:
    if answer is None:
        return {
            "package_id": package_id,
            "ok": False,
            "error": _msg("ERR_CREATE_PROCESSES_HELPER_UNAVAILABLE"),
            "error_code": "helper_unavailable",
            "error_class": "capability_missing",
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
        "package_not_registered": "ERR_CREATE_PROCESSES_NOT_INSTALLED",
        "package_operation_failed": "ERR_CREATE_PROCESSES_NOT_INSTALLED",
        "package_start_unsupported": "ERR_CREATE_PROCESSES_UNSUPPORTED",
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
    invalid = next((value for value in package_ids if not _PACKAGE_ID.fullmatch(value)), "")
    if invalid:
        return _failure(
            "ERR_CREATE_PROCESSES_INVALID_PACKAGE",
            "invalid_package_id",
            package=invalid[:80],
        )
    if not sys.platform.startswith("win"):
        return _failure(
            "ERR_CREATE_PROCESSES_WINDOWS_ONLY",
            "platform_unsupported",
            error_class="capability_missing",
        )

    lifetime = str(args.get("lifetime") or "").strip().lower()
    consent = str(args.get("actor_consent_token") or "").strip()

    if not consent:
        for package_id in package_ids:
            answer = _helper_call("query", "--package-id", package_id)
            if not answer or not answer.get("ok") or answer.get("aligned") is False:
                failed = _helper_error(package_id, answer)
                return {
                    "ok": False,
                    "results": [],
                    "failed": [failed],
                    "ok_count": 0,
                    "fail_count": 1,
                    "error": failed["error"],
                    "error_code": failed["error_code"],
                    "error_class": failed["error_class"],
                }
        return {
            "ok": True,
            "decision": "needs_inputs",
            "started": False,
            "results": [],
            "failed": [],
            "ok_count": 0,
            "fail_count": 0,
            "needs_inputs": _approval_dialog(package_ids),
        }

    if lifetime not in _LIFETIMES or consent != _consent_token(package_ids, lifetime):
        return _failure(
            "ERR_CREATE_PROCESSES_CONSENT_INVALID",
            "consent_invalid",
            error_class="policy_denied",
        )

    results, failed, process_receipts = [], [], []
    untracked_mutation = False
    for package_id in package_ids:
        answer = _helper_call(
            "start",
            "--package-id",
            package_id,
            "--lifetime",
            lifetime,
        )
        if answer and answer.get("ok") and answer.get("aligned") is not False:
            payload = answer.get("payload")
            payload = payload if isinstance(payload, dict) else {}
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
                    failed.append(_helper_error(package_id, {
                        "error_code": "managed_start_receipt_invalid"}))
                    continue
                process_receipts.append({
                    "package_id": package_id,
                    "pid": pid,
                    "creation_time": creation_time,
                })
            results.append({
                "package_id": package_id,
                "ok": True,
                "lifetime": lifetime,
                "already_running": not created_process,
            })
        else:
            failed.append(_helper_error(package_id, answer))

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
        identity = (package_id, pid, creation_time)
        if (not isinstance(package_id, str)
                or not _PACKAGE_ID.fullmatch(package_id)
                or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                or not isinstance(creation_time, int)
                or isinstance(creation_time, bool) or creation_time <= 0
                or identity in seen):
            failed.append({"package_id": package_id, "ok": False,
                           "error_class": "invalid_receipt"})
            continue
        seen.add(identity)
        answer = _helper_call(
            "stop", "--package-id", package_id,
            "--pid", str(pid), "--creation-time", str(creation_time),
        )
        if answer and answer.get("ok") and answer.get("aligned") is not False:
            payload = answer.get("payload")
            stopped = (payload.get("stopped") is True
                       if isinstance(payload, dict) else False)
            reversed_results.append({
                "package_id": package_id,
                "pid": pid,
                "ok": True,
                "stopped": stopped,
            })
        else:
            failed.append(_helper_error(package_id, answer, stopping=True))
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
