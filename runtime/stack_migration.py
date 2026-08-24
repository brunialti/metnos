#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Non-destructive pilot, rollback proof and guarded HTTP scope cutover.

``prepare`` materializes the effective legacy Metnos runtime settings as a
0600 user-unit drop-in.  Interpreter, import path and working directory remain
owned by the target Metnos unit, so migration cannot carry an external Python
environment into the autonomous installation. ``pilot`` performs at least two
user-target E2E cycles and restores the system-service baseline after every
cycle. ``cutover`` accepts only a successful pilot report and rolls back
automatically on any failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

import config as _C
from stack_reconcile import (
    CONTROL_PLANE_UNITS,
    ReconcileLock,
    RUNTIME_COMPONENT_UNITS,
    STACK_UNITS,
    StackFailure,
    StackReconciler,
    Systemctl,
    TARGET_UNIT,
    _atomic_json,
    _admin_key,
)


SCHEMA_VERSION = 1
SYSTEM_HTTP = "metnos-http.service"
# The translator timer is already one of the runtime components.  Keep one
# canonical entry so pilot baseline capture/restore cannot report it twice.
BASELINE_USER_UNITS = RUNTIME_COMPONENT_UNITS
STACK_CONTRACT_UNITS = (
    TARGET_UNIT,
    SYSTEM_HTTP,
    *CONTROL_PLANE_UNITS,
    "metnos-stack-watchdog.timer",
    *BASELINE_USER_UNITS,
)
STACK_SOURCE_ROOTS = (
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parents[1] / "executors",
)
STACK_SOURCE_SUFFIXES = frozenset({
    ".html", ".j2", ".js", ".json", ".py", ".sh", ".sig", ".sql",
    ".toml", ".txt", ".webmanifest", ".yaml", ".yml",
})
USER_STACK_UNITS = tuple(dict.fromkeys((
    TARGET_UNIT,
    *CONTROL_PLANE_UNITS,
    *STACK_UNITS,
)))


def _unit_quote(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise StackFailure("unsafe_unit_value", "unit value contains a control character")
    return json.dumps(value.replace("%", "%%"), ensure_ascii=True)


def _unit_path(value: str) -> str:
    """Escape one absolute systemd path without turning quotes into data."""
    if "\n" in value or "\r" in value or "\x00" in value:
        raise StackFailure("unsafe_unit_value", "unit path contains a control character")
    escaped: list[str] = []
    for char in value:
        if char == "%":
            escaped.append("%%")
        elif char.isspace() or char in {'\\', '"', "'"}:
            escaped.append(f"\\x{ord(char):02x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def _parse_environment(value: str) -> dict[str, str]:
    try:
        entries = shlex.split(value)
    except ValueError as exc:
        raise StackFailure("legacy_environment_invalid", "cannot parse legacy environment") from exc
    out: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            continue
        key, item = entry.split("=", 1)
        # The target unit owns Python, HOME and its import path.  Only Metnos
        # runtime settings cross the legacy boundary; unrelated environment
        # and infrastructure paths are intentionally not inherited.
        if key.startswith("METNOS_"):
            out[key] = item
    return out


def _parse_execstart(value: str) -> list[str]:
    match = re.search(r"argv\[\]=(.*?)\s+;\s+ignore_errors=", value)
    if not match:
        raise StackFailure("legacy_exec_invalid", "cannot parse legacy ExecStart")
    try:
        argv = shlex.split(match.group(1))
    except ValueError as exc:
        raise StackFailure("legacy_exec_invalid", "cannot parse legacy ExecStart argv") from exc
    if not argv or not Path(argv[0]).is_absolute():
        raise StackFailure("legacy_exec_invalid", "legacy ExecStart executable is not absolute")
    return argv


def _service_process_environment(
        service_user: str, systemctl: Systemctl | None = None) -> dict[str, str]:
    """Build the bounded service identity used by a privileged cutover.

    Only Metnos' XDG roots cross this boundary.  Code paths, Python settings,
    arbitrary unit variables and HOME remain owned by the privileged process.
    """
    try:
        identity = pwd.getpwnam(service_user)
    except KeyError as exc:
        raise StackFailure(
            "service_user_invalid", "Metnos service user does not exist",
        ) from exc
    adapter = systemctl or Systemctl(service_user=service_user)
    result = adapter.run(
        "user", "show", SYSTEM_HTTP, "--property=Environment", "--value",
        timeout_s=15,
    )
    if result.returncode != 0:
        raise StackFailure(
            "service_environment_unavailable",
            "cannot inspect the Metnos service identity",
            details={"detail": (result.stderr or "")[-300:]},
        )
    unit_environment = _parse_environment(result.stdout.strip())
    defaults = {
        "METNOS_USER_DATA": Path(identity.pw_dir) / ".local/share/metnos",
        "METNOS_USER_STATE": Path(identity.pw_dir) / ".local/state/metnos",
        "METNOS_USER_CONFIG": Path(identity.pw_dir) / ".config/metnos",
        "METNOS_USER_CACHE": Path(identity.pw_dir) / ".cache/metnos",
    }
    out = os.environ.copy()
    for key, default in defaults.items():
        value = Path(unit_environment.get(key) or default)
        if not value.is_absolute():
            raise StackFailure(
                "service_environment_invalid",
                "service identity paths must be absolute",
            )
        out[key] = str(value)
    out["METNOS_INSTALL_ROOT"] = str(Path(__file__).resolve().parents[1])
    out["METNOS_SERVICE_USER"] = service_user
    out["XDG_RUNTIME_DIR"] = f"/run/user/{identity.pw_uid}"
    out["DBUS_SESSION_BUS_ADDRESS"] = (
        f"unix:path=/run/user/{identity.pw_uid}/bus"
    )
    out["METNOS_MIGRATION_IDENTITY_READY"] = "1"
    return out


class HttpScopeMigration:
    def __init__(self, *, systemctl: Systemctl | None = None,
                 reconciler: StackReconciler | None = None,
                 user_unit_dir: Path | None = None,
                 evidence_path: Path | None = None,
                 turn_probe: Callable[[], dict] | None = None,
                 service_user: str | None = None):
        self.service_user = (
            service_user or os.environ.get("METNOS_SERVICE_USER", "").strip()
            or pwd.getpwuid(os.getuid()).pw_name
        )
        try:
            identity = pwd.getpwnam(self.service_user)
        except KeyError as exc:
            raise StackFailure("service_user_invalid", "Metnos service user does not exist") from exc
        self.service_uid = identity.pw_uid
        self.service_gid = identity.pw_gid
        self.service_home = Path(identity.pw_dir)
        self.systemctl = systemctl or Systemctl(service_user=self.service_user)
        state_dir = (
            Path(_C.PATH_USER_STATE) if self.service_uid == os.getuid()
            else self.service_home / ".local/state/metnos"
        )
        config_dir = (
            Path(_C.PATH_USER_CONFIG) if self.service_uid == os.getuid()
            else self.service_home / ".config/metnos"
        )
        self.reconciler = reconciler or StackReconciler(
            systemctl=self.systemctl,
            report_path=state_dir / "stack_reconcile_last.json",
            admin_key_path=config_dir / "admin.key",
            catalog_names_provider=(
                self._service_catalog_names
                if self.service_uid != os.getuid() else None
            ),
            default_write_report=self.service_uid == os.getuid(),
        )
        self.user_unit_dir = (
            user_unit_dir or self.service_home / ".config/systemd/user"
        )
        self.evidence_path = evidence_path or state_dir / "stack_migration_pilot.json"
        self.turn_probe = turn_probe or self._natural_turn
        runtime_dir = (
            Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{self.service_uid}"))
            if self.service_uid == os.getuid()
            else Path(f"/run/user/{self.service_uid}")
        )
        self.lock_path = runtime_dir / "metnos-stack-reconcile.lock"

    def _lock(self) -> ReconcileLock:
        """Use the service user's lock in both unprivileged pilot and root cutover."""
        return ReconcileLock(self.lock_path, owner_uid=self.service_uid)

    def _show_value(self, scope: str, prop: str) -> str:
        if prop not in {"Environment", "ExecStart", "WorkingDirectory"}:
            raise StackFailure("invalid_property", "property is outside migration contract")
        result = self.systemctl.run(
            scope, "show", SYSTEM_HTTP, f"--property={prop}", "--value",
            timeout_s=15,
        )
        if result.returncode != 0:
            raise StackFailure(
                "legacy_inventory_failed", f"cannot inspect legacy {prop}",
                details={"detail": (result.stderr or "")[-300:]},
            )
        return result.stdout.strip()

    def _service_catalog_names(self) -> set[str]:
        """Load executable contracts under the service identity, never root.

        The executor loader reads instance-owned keys and may import admitted
        synthesized contracts.  A privileged migration process therefore
        delegates this one local inventory operation to the declared service
        account with the exact path contract from its HTTP unit.
        """
        unit_environment = _parse_environment(
            self._show_value("user", "Environment")
        )
        defaults = {
            "METNOS_USER_DATA": self.service_home / ".local/share/metnos",
            "METNOS_USER_STATE": self.service_home / ".local/state/metnos",
            "METNOS_USER_CONFIG": self.service_home / ".config/metnos",
            "METNOS_USER_CACHE": self.service_home / ".cache/metnos",
        }
        identity_environment = {
            key: str(unit_environment.get(key) or value)
            for key, value in defaults.items()
        }
        if any(not Path(value).is_absolute()
               for value in identity_environment.values()):
            raise StackFailure(
                "service_environment_invalid",
                "service identity paths must be absolute",
            )
        target_argv = _parse_execstart(
            self._show_value("user", "ExecStart")
        )
        command = [
            target_argv[0],
            str(Path(__file__).resolve().with_name("stack_reconcile.py")),
            "catalog",
        ]
        environment = os.environ.copy()
        environment.update(identity_environment)
        environment["METNOS_INSTALL_ROOT"] = str(Path(__file__).resolve().parents[1])
        environment["XDG_RUNTIME_DIR"] = f"/run/user/{self.service_uid}"
        environment["DBUS_SESSION_BUS_ADDRESS"] = (
            f"unix:path=/run/user/{self.service_uid}/bus"
        )
        if os.geteuid() == 0 and self.service_uid != 0:
            command = [
                "runuser", "--user", self.service_user, "--", *command,
            ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=environment,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            raise StackFailure(
                "service_catalog_failed", type(exc).__name__,
            ) from exc
        try:
            payload = json.loads(result.stdout)
            names = payload.get("names") if isinstance(payload, dict) else None
        except json.JSONDecodeError as exc:
            raise StackFailure(
                "service_catalog_failed", "service catalog output is invalid",
            ) from exc
        if result.returncode != 0 or not isinstance(names, list) or not names:
            raise StackFailure(
                "service_catalog_failed",
                "service account could not load the executor catalog",
                details={"detail": (result.stderr or result.stdout)[-300:]},
            )
        return {str(name) for name in names if isinstance(name, str) and name}

    def prepare(self) -> dict:
        """Render a runtime-settings drop-in without stopping either scope."""
        environment = _parse_environment(self._show_value("system", "Environment"))
        legacy_argv = _parse_execstart(self._show_value("system", "ExecStart"))
        working_dir = self._show_value("system", "WorkingDirectory")
        if working_dir and not Path(working_dir).is_absolute():
            raise StackFailure("legacy_workdir_invalid", "legacy WorkingDirectory is not absolute")

        # Do not override ExecStart.  The rendered target unit points at the
        # installation's own venv and module entry point; the pilot proves that
        # environment instead of reproducing the legacy interpreter.
        lines = ["[Service]"]
        if working_dir:
            lines.append(f"WorkingDirectory={_unit_path(working_dir)}")
        for key in sorted(environment):
            lines.append(f"Environment={_unit_quote(f'{key}={environment[key]}')}")
        body = "\n".join(lines) + "\n"

        dropin = self.user_unit_dir / "metnos-http.service.d" / "90-legacy-contract.conf"
        dropin.parent.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            os.chown(dropin.parent, self.service_uid, self.service_gid)
        tmp = dropin.with_name(f".{dropin.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            if os.geteuid() == 0:
                os.fchown(fd, self.service_uid, self.service_gid)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, dropin)
        finally:
            if tmp.exists():
                tmp.unlink()
        self.systemctl.run("user", "daemon-reload", timeout_s=30)
        target_argv = _parse_execstart(self._show_value("user", "ExecStart"))
        return {
            "ok": True,
            "dropin": str(dropin),
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "environment_keys": sorted(environment),
            "legacy_exec_program": legacy_argv[0],
            "target_exec_program": target_argv[0],
        }

    def _stack_contract_sha256(self) -> str:
        """Bind pilot evidence to effective units and executable sources.

        Hashing the declared source roots avoids a hand-maintained dependency
        list that can silently miss a new dynamic import.  Runtime caches,
        databases and compiled bytecode are excluded by construction.
        """
        digest = hashlib.sha256()
        for unit in STACK_CONTRACT_UNITS:
            state = self.systemctl.show(unit, "user")
            digest.update(f"unit:{unit}\0".encode())
            if state.get("LoadState") in {"not-found", "error", ""}:
                digest.update(b"not-installed\0")
                continue
            result = self.systemctl.run(
                "user", "cat", unit, "--no-pager", timeout_s=15,
            )
            if result.returncode != 0:
                raise StackFailure(
                    "stack_inventory_failed",
                    f"cannot read effective unit {unit}",
                )
            digest.update(result.stdout.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
        install_root = Path(__file__).resolve().parents[1]
        sources = sorted(
            path
            for root in STACK_SOURCE_ROOTS
            for path in root.rglob("*")
            if (
                path.is_file()
                and path.suffix in STACK_SOURCE_SUFFIXES
                and not any(part.startswith(".") for part in path.parts)
                and "__pycache__" not in path.parts
            )
        )
        if not sources:
            raise StackFailure(
                "stack_inventory_failed", "stack source inventory is empty",
            )
        for path in sources:
            try:
                payload = path.read_bytes()
            except OSError as exc:
                raise StackFailure(
                    "stack_inventory_failed",
                    f"cannot read stack source {path.name}",
                ) from exc
            relative = path.relative_to(install_root).as_posix()
            digest.update(f"source:{relative}\0".encode())
            digest.update(payload)
            digest.update(b"\0")
        return digest.hexdigest()

    def _host_fingerprint(self) -> str:
        try:
            machine_id = Path("/etc/machine-id").read_text(encoding="ascii").strip()
        except OSError as exc:
            raise StackFailure("host_identity_unavailable", "machine identity is unavailable") from exc
        return hashlib.sha256(
            f"{machine_id}\0{self.service_user}\0{self.service_uid}".encode()
        ).hexdigest()

    def _natural_turn(self) -> dict:
        body = json.dumps({
            "query": "che ore sono?",
            "actor": "stack-migration-gate",
            "conversation_id": f"stack-migration-gate-{uuid.uuid4().hex}",
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.reconciler.endpoints.http}/agent/turn",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "metnos-stack-migration/1",
                "Authorization": "Bearer " + _admin_key(
                    self.reconciler.admin_key_path
                ),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise StackFailure("pilot_turn_unavailable", type(exc).__name__) from exc
        if payload.get("final_kind") != "answer" or not payload.get("turn_id"):
            raise StackFailure("pilot_turn_failed", "pilot turn did not complete as an answer")
        steps = payload.get("steps_summary") or []
        if not steps or any(
            not isinstance(step, dict) or step.get("ok") is not True
            for step in steps
        ):
            raise StackFailure("pilot_turn_failed", "pilot turn contains a failed step")
        return {
            "turn_id": payload["turn_id"],
            "final_kind": payload["final_kind"],
            "steps": len(steps),
        }

    def _expect_system_baseline(self) -> None:
        state = self.systemctl.show(SYSTEM_HTTP, "system")
        if state.get("LoadState") != "loaded" or state.get("ActiveState") != "active":
            raise StackFailure("legacy_baseline_missing", "system HTTP baseline is not active")
        target = self.systemctl.show(TARGET_UNIT, "user")
        if target.get("LoadState") != "loaded":
            raise StackFailure("target_not_installed", "user target is not installed")
        if target.get("ActiveState") == "active":
            raise StackFailure("target_already_active", "user target must be inactive before pilot")

    def _active_user_baseline(self) -> tuple[str, ...]:
        """Capture the exact active companion set before target ownership."""
        return tuple(
            unit for unit in BASELINE_USER_UNITS
            if self.systemctl.show(unit, "user").get("ActiveState") == "active"
        )

    def _reset_start_limits(self, candidates: tuple[str, ...]):
        """Reset rate-limit counters only for installed Metnos user units.

        A migration pilot intentionally performs several bounded stop/start
        transitions in quick succession.  Those transitions must not consume
        the operational restart budget that protects services from crashes.
        Filtering through LoadState keeps the operation scoped to the declared
        and installed Metnos stack instead of resetting unrelated user units.
        """
        loaded = tuple(
            unit for unit in dict.fromkeys(candidates)
            if self.systemctl.show(unit, "user").get("LoadState") == "loaded"
        )
        if not loaded:
            return None
        return self.systemctl.run(
            "user", "reset-failed", *loaded, timeout_s=30,
        )

    def _restore_baseline(self, active_user_units: tuple[str, ...]) -> None:
        target_stop = self.systemctl.run(
            "user", "stop", TARGET_UNIT, timeout_s=180,
        )
        quarantine_stop = self.systemctl.run(
            "user", "stop", "metnos-stack-quarantine.service", timeout_s=60,
        )
        if target_stop.returncode != 0 or quarantine_stop.returncode != 0:
            raise StackFailure(
                "rollback_stop_failed",
                "could not settle target and quarantine before rollback",
                details={
                    "target_returncode": target_stop.returncode,
                    "quarantine_returncode": quarantine_stop.returncode,
                },
            )
        reset_result = self._reset_start_limits(active_user_units)
        component_result = None
        if active_user_units:
            component_result = self.systemctl.run(
                "user", "start", *active_user_units, timeout_s=180,
            )
        http_result = self.systemctl.run(
            "system", "start", SYSTEM_HTTP, timeout_s=180,
        )
        if (
            http_result.returncode != 0
            or (reset_result is not None and reset_result.returncode != 0)
            or (component_result is not None and component_result.returncode != 0)
        ):
            raise StackFailure(
                "rollback_failed",
                "could not restart the complete legacy baseline",
                details={
                    "http_returncode": http_result.returncode,
                    "reset_returncode": (
                        reset_result.returncode
                        if reset_result is not None else 0
                    ),
                    "components_returncode": (
                        component_result.returncode
                        if component_result is not None else 0
                    ),
                },
            )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if self.reconciler.check().get("ok"):
                    return
            except StackFailure:
                pass
            time.sleep(1)
        raise StackFailure(
            "rollback_health_failed",
            "complete legacy baseline did not recover after rollback",
        )

    def pilot(self, *, cycles: int = 2) -> dict:
        if cycles < 2:
            raise StackFailure("pilot_cycles_invalid", "at least two pilot cycles are required")
        lock = self._lock()
        lock.acquire(wait_s=2)
        results: list[dict] = []
        started = time.time()
        baseline_user_units: tuple[str, ...] = ()
        mutation_started = False
        try:
            prepared = self.prepare()
            stack_contract_sha = self._stack_contract_sha256()
            host_fingerprint = self._host_fingerprint()
            self._expect_system_baseline()
            baseline_user_units = self._active_user_baseline()
            self.reconciler.check(require_quiescent=True)
            for number in range(1, cycles + 1):
                reset_result = self._reset_start_limits(USER_STACK_UNITS)
                if reset_result is not None and reset_result.returncode != 0:
                    raise StackFailure(
                        "target_reset_failed",
                        "could not reset Metnos transition rate limits",
                    )
                mutation_started = True
                stopped = self.systemctl.run("system", "stop", SYSTEM_HTTP, timeout_s=180)
                if stopped.returncode != 0:
                    raise StackFailure("legacy_stop_failed", "could not stop system HTTP")
                started_target = self.systemctl.run("user", "start", TARGET_UNIT, timeout_s=180)
                if started_target.returncode != 0:
                    raise StackFailure("target_start_failed", "could not start user target")
                readiness = self.reconciler.wait_ready(timeout_s=150)
                turn = self.turn_probe()
                self.reconciler.check(require_quiescent=True)
                self._restore_baseline(baseline_user_units)
                mutation_started = False
                results.append({
                    "cycle": number,
                    "ok": True,
                    "readiness": bool(readiness.get("ok")),
                    "turn": turn,
                    "rollback": True,
                })
            report = {
                "schema_version": SCHEMA_VERSION,
                "kind": "metnos.stack.migration-pilot",
                "ok": True,
                "cycles_required": 2,
                "cycles_completed": len(results),
                "rollback_verified": all(row["rollback"] for row in results),
                "baseline_restored": True,
                "configuration_sha256": prepared["sha256"],
                "stack_contract_sha256": stack_contract_sha,
                "host_fingerprint": host_fingerprint,
                "baseline_user_units": list(baseline_user_units),
                "duration_ms": int((time.time() - started) * 1000),
                "cycles": results,
            }
            _atomic_json(self.evidence_path, report)
            return report
        except StackFailure as exc:
            if mutation_started:
                try:
                    self._restore_baseline(baseline_user_units)
                except StackFailure as rollback_exc:
                    rollback_exc.details.setdefault("original_error", exc.code)
                    raise rollback_exc from exc
            raise
        finally:
            lock.release()

    @staticmethod
    def validate_evidence(path: Path) -> dict:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StackFailure("pilot_evidence_invalid", "pilot evidence is unreadable") from exc
        if not (
            report.get("schema_version") == SCHEMA_VERSION
            and report.get("ok") is True
            and int(report.get("cycles_completed") or 0) >= 2
            and report.get("rollback_verified") is True
            and report.get("baseline_restored") is True
            and isinstance(report.get("configuration_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", report["configuration_sha256"])
            and isinstance(report.get("stack_contract_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", report["stack_contract_sha256"])
            and isinstance(report.get("host_fingerprint"), str)
            and re.fullmatch(r"[0-9a-f]{64}", report["host_fingerprint"])
        ):
            raise StackFailure("pilot_evidence_invalid", "pilot evidence does not satisfy cutover gate")
        return report

    def cutover(self, evidence: Path) -> dict:
        if isinstance(self.systemctl, Systemctl) and os.geteuid() != 0:
            raise StackFailure(
                "cutover_authority_required",
                "cutover must run as root with an explicit Metnos service user",
            )
        proof = self.validate_evidence(evidence)
        lock = self._lock()
        lock.acquire(wait_s=2)
        baseline_user_units: tuple[str, ...] = ()
        try:
            prepared = self.prepare()
            if prepared["sha256"] != proof["configuration_sha256"]:
                raise StackFailure(
                    "pilot_evidence_stale",
                    "effective HTTP contract changed after the migration pilot",
                )
            if self._stack_contract_sha256() != proof["stack_contract_sha256"]:
                raise StackFailure(
                    "pilot_evidence_stale",
                    "effective stack contract changed after the migration pilot",
                )
            if self._host_fingerprint() != proof["host_fingerprint"]:
                raise StackFailure(
                    "pilot_evidence_foreign",
                    "pilot evidence belongs to another host or service user",
                )
            self._expect_system_baseline()
            baseline_user_units = self._active_user_baseline()
            self.reconciler.check(require_quiescent=True)
            reset_result = self._reset_start_limits(USER_STACK_UNITS)
            if reset_result is not None and reset_result.returncode != 0:
                raise StackFailure(
                    "target_reset_failed",
                    "could not reset Metnos transition rate limits",
                )
            if self.systemctl.run("system", "stop", SYSTEM_HTTP, timeout_s=180).returncode:
                raise StackFailure("legacy_stop_failed", "could not stop system HTTP")
            if self.systemctl.run("system", "disable", SYSTEM_HTTP, timeout_s=60).returncode:
                raise StackFailure("legacy_disable_failed", "could not disable system HTTP")
            result = self.systemctl.run("user", "enable", "--now", TARGET_UNIT, timeout_s=180)
            if result.returncode:
                raise StackFailure("target_enable_failed", "could not enable user target")
            readiness = self.reconciler.wait_ready(timeout_s=150)
            turn = self.turn_probe()
            self.reconciler.check(require_quiescent=True)
            return {
                "ok": True,
                "cutover": True,
                "pilot_cycles": proof["cycles_completed"],
                "readiness": readiness,
                "turn": turn,
            }
        except StackFailure as exc:
            self.systemctl.run("user", "disable", "--now", TARGET_UNIT, timeout_s=180)
            self.systemctl.run("system", "enable", SYSTEM_HTTP, timeout_s=60)
            try:
                self._restore_baseline(baseline_user_units)
            except StackFailure as rollback_exc:
                rollback_exc.details.setdefault("original_error", exc.code)
                rollback_exc.details.setdefault("original_details", exc.details)
                raise rollback_exc from exc
            raise
        finally:
            lock.release()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "pilot", "cutover"))
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--service-user")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(raw_argv)
    if args.command == "cutover" and os.geteuid() == 0 and not args.service_user:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "error_code": "service_user_required",
            "error": "root cutover requires --service-user",
            "details": {},
        }, sort_keys=True))
        return 1
    if (
        args.command == "cutover"
        and os.geteuid() == 0
        and args.service_user
        and os.environ.get("METNOS_MIGRATION_IDENTITY_READY") != "1"
    ):
        environment = _service_process_environment(args.service_user)
        os.execve(
            sys.executable,
            [sys.executable, str(Path(__file__).resolve()), *raw_argv],
            environment,
        )
    migration = HttpScopeMigration(service_user=args.service_user)
    try:
        if args.command == "prepare":
            out = migration.prepare()
        elif args.command == "pilot":
            out = migration.pilot(cycles=args.cycles)
        else:
            if args.evidence is None:
                raise StackFailure("pilot_evidence_required", "cutover requires --evidence")
            out = migration.cutover(args.evidence)
    except StackFailure as exc:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "error_code": exc.code,
            "error": str(exc),
            "details": exc.details,
        }, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
