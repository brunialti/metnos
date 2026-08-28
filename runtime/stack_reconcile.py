#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Transactional readiness and restart authority for the local Metnos stack.

This module never discovers arbitrary units and never signs arbitrary paths.
Executor names are resolved below ``executors/`` and systemd operations use a
closed unit list.  The public installer invokes ``check`` from the readiness
unit; operators use ``deploy`` when named executor changes must be signed and
the complete target restarted.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import pwd
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import config as _C
from executor_birth_maintenance_units import CONTRACT_CUTOVER_UNITS


SCHEMA_VERSION = 1
TARGET_UNIT = "metnos.target"
CONTROL_PLANE_UNITS = (
    "metnos-stack-ready.service",
    "metnos-stack-quarantine.service",
    "metnos-stack-watchdog.service",
)
STACK_UNITS = (
    "metnos-http.service",
    "metnos-durable-worker.service",
    "metnos-side-display.service",
    "metnos-playwright.service",
    "metnos-telegram-daemon.service",
    "metnos-llm.service",
    "metnos-searxng.service",
    "metnos-photon.service",
    "metnos-i18n-translator.service",
    "metnos-i18n-translator.timer",
    "metnos-stack-watchdog.timer",
)
# The canonical cutover inventory is projected from the RM-0008 service source.
# Every directly startable user unit is included; absence is an observed state,
# never a reason to omit an optional service from the maintenance proof.
RUNTIME_COMPONENT_UNITS = (
    "metnos-durable-worker.service",
    "metnos-side-display.service",
    "metnos-playwright.service",
    "metnos-telegram-daemon.service",
    "metnos-llm.service",
    "metnos-searxng.service",
    "metnos-photon.service",
    "metnos-i18n-translator.timer",
)
FAILURE_WINDOW_S = 10 * 60
FAILURE_LIMIT = 3
OPEN_INTERVAL_S = 15 * 60
WATCHED_SERVICE_KEYS = ("searxng", "llm", "durable_workloads")
_DURABLE_NON_RESTARTABLE_REASONS = frozenset({
    "already_active",
    "runtime_bindings_unavailable",
    "schema_incompatible",
})


class StackFailure(RuntimeError):
    """Stable failure suitable for machine reports and service logs."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclasses.dataclass(frozen=True)
class Endpoints:
    http: str = "http://127.0.0.1:8770"
    sidecar: str = "http://127.0.0.1:8771"

    @classmethod
    def from_env(cls) -> "Endpoints":
        return cls(
            os.environ.get("METNOS_HTTP_URL", cls.http).rstrip("/"),
            os.environ.get("METNOS_PLAYWRIGHT_URL", cls.sidecar).rstrip("/"),
        )


def _runtime_dir() -> Path:
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    return _runtime_dir().parent


def _state_dir() -> Path:
    path = Path(_C.PATH_USER_STATE)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


class ReconcileLock:
    """Process lock hardened against symlink substitution."""

    def __init__(self, path: Path | None = None, *, owner_uid: int | None = None):
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        self.path = path or runtime / "metnos-stack-reconcile.lock"
        self.owner_uid = os.getuid() if owner_uid is None else owner_uid
        self.fd: int | None = None

    def acquire(self, *, wait_s: float = 0.0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self.fd = os.open(self.path, flags, 0o600)
        info = os.fstat(self.fd)
        if (
            os.geteuid() == 0
            and self.owner_uid != 0
            and info.st_uid == 0
        ):
            os.fchown(self.fd, self.owner_uid, -1)
            info = os.fstat(self.fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner_uid:
            self.release()
            raise StackFailure("unsafe_lock", "reconcile lock is not an owned regular file")
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(self.fd, 0)
                os.write(self.fd, f"{os.getpid()}\n".encode())
                return
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    self.release()
                    raise StackFailure("reconcile_busy", "another stack reconcile is running") from exc
                time.sleep(0.1)

    def release(self) -> None:
        if self.fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "ReconcileLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


@contextlib.contextmanager
def catalog_reconcile_lock(
    *,
    lock: ReconcileLock | None = None,
    path: Path | None = None,
    owner_uid: int | None = None,
    wait_s: float = 2.0,
):
    """Acquire the catalog and lifecycle boundaries in one canonical order."""
    from contract_store import ContractStoreError, catalog_admission_lock

    try:
        catalog = catalog_admission_lock(timeout=wait_s)
        catalog.__enter__()
    except ContractStoreError as exc:
        raise StackFailure(
            "catalog_lock_unavailable", f"{exc.code}: {exc.detail}",
        ) from exc
    try:
        if lock is not None and (path is not None or owner_uid is not None):
            raise ValueError("lock cannot be combined with path or owner_uid")
        selected = lock
        if selected is None:
            if path is None and owner_uid is None:
                selected = ReconcileLock()
            elif owner_uid is None:
                selected = ReconcileLock(path)
            else:
                selected = ReconcileLock(path, owner_uid=owner_uid)
        selected.acquire(wait_s=wait_s)
        try:
            yield selected
        finally:
            selected.release()
    finally:
        catalog.__exit__(*sys.exc_info())


class Systemctl:
    """Closed systemd adapter used by reconcile and the migration gate."""

    def __init__(self, *, service_user: str | None = None):
        self.service_user = service_user or os.environ.get("METNOS_SERVICE_USER", "")

    def _service_uid(self) -> int:
        if not self.service_user:
            return os.getuid()
        try:
            return pwd.getpwnam(self.service_user).pw_uid
        except KeyError as exc:
            raise StackFailure(
                "service_user_invalid", "Metnos service user does not exist",
            ) from exc

    def _user_env(self) -> dict[str, str]:
        env = os.environ.copy()
        uid = self._service_uid()
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
        return env

    def run(self, scope: str, *args: str, timeout_s: float = 120) -> subprocess.CompletedProcess:
        if scope not in {"user", "system"}:
            raise StackFailure("invalid_scope", "systemd scope must be user or system")
        command = ["systemctl"]
        env = None
        if scope == "user":
            command.append("--user")
            env = self._user_env()
            if (
                os.geteuid() == 0
                and self.service_user
                and self._service_uid() != 0
            ):
                command = [
                    "runuser", "--user", self.service_user, "--", *command,
                ]
        command.extend(args)
        try:
            return subprocess.run(
                command, capture_output=True, text=True, check=False,
                timeout=timeout_s, env=env,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            raise StackFailure("systemctl_failed", type(exc).__name__) from exc

    def show(self, unit: str, scope: str = "user") -> dict[str, str]:
        if unit not in {*STACK_UNITS, *CONTROL_PLANE_UNITS, TARGET_UNIT}:
            raise StackFailure("unknown_unit", "unit is outside the closed stack catalog")
        result = self.run(
            scope, "show", unit,
            "--property=Id,LoadState,ActiveState,SubState,UnitFileState,MainPID",
            timeout_s=10,
        )
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        if result.returncode and not values:
            values["LoadState"] = "error"
            values["ManagerError"] = (result.stderr or "")[-300:]
        return values


def _json_request(url: str, *, admin_key: str = "", timeout_s: float = 5) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "metnos-stack-reconcile/1"}
    if admin_key:
        headers["Authorization"] = f"Bearer {admin_key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
            if not isinstance(payload, dict):
                raise StackFailure("invalid_health", "health response is not an object")
            return payload
    except urllib.error.HTTPError as exc:
        raise StackFailure(
            "http_status", f"{url} returned HTTP {exc.code}",
            details={"status": exc.code},
        ) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise StackFailure("endpoint_unavailable", f"{url}: {type(exc).__name__}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StackFailure("invalid_health", f"{url}: invalid JSON") from exc


def _admin_key(path: Path | None = None) -> str:
    path = path or Path(_C.PATH_USER_CONFIG) / "admin.key"
    try:
        return _RedactedSecret(path.read_text(encoding="utf-8").strip())
    except OSError as exc:
        raise StackFailure("admin_key_unavailable", "local admin key is unavailable") from exc


class _RedactedSecret(str):
    """String-compatible secret that cannot leak through traceback locals."""

    def __repr__(self) -> str:
        return "<redacted>"


def _catalog_names() -> set[str]:
    from loader import load_catalog

    catalog = load_catalog()
    if hasattr(catalog, "executors"):
        return set(catalog.executors)
    out: set[str] = set()
    for item in catalog:
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", "")
        if isinstance(name, str) and name:
            out.add(name)
    return out


def _read_process_state(pid: int) -> str:
    """Read the one-letter Linux process state for an exact numeric PID."""
    if pid <= 1:
        return ""
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.startswith("State:"):
                value = line.split(":", 1)[1].strip()
                return value[:1]
    except OSError:
        return ""
    return ""


def _read_process_uid(pid: int) -> int | None:
    """Read the real UID of an exact PID from procfs."""
    if pid <= 1:
        return None
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Uid:"):
                return int(line.split(":", 1)[1].split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _service_uid() -> int:
    from services_registry import service_user
    try:
        return pwd.getpwnam(service_user()).pw_uid
    except KeyError as exc:
        raise StackFailure(
            "service_user_invalid", "Metnos service user does not exist",
        ) from exc


def _watched_service_snapshot(key: str, *,
                              probe_endpoint: bool | None = None) -> dict:
    """Resolve one closed-catalog dependency and add process-state evidence.

    SearXNG and LRE are checked functionally.  The LLM is deliberately not
    HTTP-probed during every readiness pass because a long inference may
    occupy its slots; its critical false-``active`` failure is instead
    detected from a stopped systemd MainPID (Linux state ``T``/``t``).
    Recovery performs an explicit endpoint probe before declaring success.
    """
    from services_registry import get, snapshot_one

    spec = get(key)
    if spec is None or key not in WATCHED_SERVICE_KEYS:
        raise StackFailure("unknown_service", "service is outside watchdog scope")
    should_probe = key == "searxng" if probe_endpoint is None else probe_endpoint
    row = snapshot_one(spec, probe_endpoint=should_probe)
    if key == "llm":
        try:
            pid = int(row.get("main_pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        process_state = _read_process_state(pid)
        row["process_state"] = process_state
        row["process_stopped"] = process_state in {"T", "t"}
    elif key == "durable_workloads":
        from durable_workloads.service import health_snapshot

        application = health_snapshot()
        row.update({
            "application_state": application.get("state"),
            "application_enabled": application.get("enabled"),
            "application_worker_available": application.get(
                "worker_available"
            ),
            "healthy": application.get("state") == "ready",
            "health_detail": str(
                application.get("reason_code") or ""
            )[:64],
        })
    return row


def _watched_service_ok(key: str, row: dict) -> bool:
    if row.get("load_state") == "not-found" or not row.get("installed"):
        return not bool(row.get("observation_error"))
    if row.get("observation_error"):
        return False
    if row.get("active_state") != "active":
        return False
    if key == "searxng":
        return row.get("healthy") is True
    if key == "llm":
        return not bool(row.get("process_stopped"))
    if key == "durable_workloads":
        state = row.get("application_state")
        if state is None:
            # Compatibility for closed test adapters and older snapshots.
            return row.get("healthy") is True
        reason = row.get("health_detail")
        if reason == "feature_disabled":
            return row.get("application_enabled") is False
        if state == "recovering" and reason == "recovery_incomplete":
            return row.get("application_worker_available") is True
        return (
            state == "ready"
            and reason == "none"
            and row.get("application_enabled") is True
            and row.get("application_worker_available") is True
        )
    return False


def verify_named_executors(names: list[str], *, sign_first: bool = False) -> list[dict]:
    """Admit/verify explicitly named direct children of ``executors/``.

    ``sign_first`` is retained as the public compatibility flag.  It now
    means admission through the sealed Executor Birth service; it never
    selects a technical publisher.
    """
    from manifest_inventory import ManifestLayout, resolve_manifest_layout
    from sign import verify_executor

    root = (_repo_root() / "executors").resolve()
    layout = resolve_manifest_layout()
    directories: list[tuple[str, Path]] = []
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise StackFailure("invalid_executor", "executor name is not canonical")
        directory = (root / name).resolve()
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise StackFailure("invalid_executor", "executor escapes the catalog root") from exc
        if not (directory / "manifest.toml").is_file():
            raise StackFailure("unknown_executor", f"executor {name!r} is not installed")
        directories.append((name, directory))
        if sign_first:
            try:
                from executor_birth_intent import BirthIntent, submit_stack_reconcile_birth
                from manifest_inventory import ContractId, ManifestOrigin
                with tempfile.TemporaryDirectory(
                    prefix=f"metnos-reconcile-{name}-birth-",
                ) as raw_staging:
                    staging = Path(raw_staging) / name
                    shutil.copytree(directory, staging)
                    birth = submit_stack_reconcile_birth(BirthIntent(
                        candidate_source_root=staging,
                        contract_id=ContractId(
                            ManifestOrigin.CORE, f"{name}/manifest.toml",
                        ),
                        reason=f"restart admission executor={name}",
                    ))
            except Exception as exc:
                raise StackFailure("birth_unavailable", str(exc)) from exc
            if birth.error_code or birth.publication is None:
                raise StackFailure(
                    "birth_admission_failed",
                    birth.error_code or "publication_missing",
                )

    results: list[dict] = []
    if layout is ManifestLayout.STORE_ONLY:
        # The authoring tree is deliberately unsigned after cutover.  The
        # verified store loader is the live admission proof; checking source
        # bytes with ``verify_executor`` would report a false signature
        # failure even after a successful publication.
        from loader import load_catalog

        catalog = load_catalog(
            include_synth=True,
            include_verb_unique=False,
        )
        for name, _directory in directories:
            executor = catalog.executors.get(name)
            row = {"name": name, "ok": executor is not None}
            if executor is None:
                row["reason"] = "executor absent from verified store catalog"
            else:
                row["digest"] = executor.digest
            results.append(row)
    else:
        for name, directory in directories:
            ok, info = verify_executor(directory)
            row = {"name": name, "ok": bool(ok)}
            if ok:
                row["digest"] = info.get("digest", "")
            else:
                row["reason"] = info.get("reason", "verification failed")
            results.append(row)
    failed = [row["name"] for row in results if not row["ok"]]
    if failed:
        raise StackFailure(
            "signature_invalid", "one or more named executors failed verification",
            details={"executors": failed},
        )
    return results


class CircuitBreaker:
    def __init__(self, path: Path | None = None):
        self.path = path or _state_dir() / "stack_reconcile_circuit.json"

    def _load(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def assert_closed(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        opened_until = float(self._load().get("opened_until") or 0)
        if opened_until > current:
            raise StackFailure(
                "circuit_open", "automatic stack restart circuit is open",
                details={"retry_after_s": int(opened_until - current)},
            )

    def success(self) -> None:
        _atomic_json(self.path, {"schema_version": 1, "failures": [], "opened_until": 0})

    def failure(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        state = self._load()
        failures = [
            float(value) for value in state.get("failures", [])
            if current - float(value) <= FAILURE_WINDOW_S
        ]
        failures.append(current)
        opened_until = current + OPEN_INTERVAL_S if len(failures) >= FAILURE_LIMIT else 0
        _atomic_json(self.path, {
            "schema_version": 1,
            "failures": failures,
            "opened_until": opened_until,
        })


class StackReconciler:
    def __init__(self, *, endpoints: Endpoints | None = None,
                 systemctl: Systemctl | None = None,
                 report_path: Path | None = None,
                 admin_key_path: Path | None = None,
                 catalog_names_provider: Callable[[], set[str]] | None = None,
                 default_write_report: bool = True):
        self.endpoints = endpoints or Endpoints.from_env()
        self.systemctl = systemctl or Systemctl()
        self.report_path = report_path or _state_dir() / "stack_reconcile_last.json"
        self.admin_key_path = admin_key_path
        self.catalog_names_provider = catalog_names_provider or _catalog_names
        self.default_write_report = default_write_report

    def _sidecar_required(self, mode: str) -> bool:
        if mode == "yes":
            return True
        if mode == "no":
            return False
        from services_registry import desired_state
        if desired_state("playwright") == "stopped":
            return False
        state = self.systemctl.show("metnos-playwright.service")
        return state.get("LoadState") not in {"not-found", "error", ""}

    def check(self, *, require_sidecar: str = "auto",
              require_quiescent: bool = False,
              write_report: bool | None = None) -> dict:
        started = time.time()
        checks: list[dict[str, Any]] = []

        health = _json_request(f"{self.endpoints.http}/agent/health")
        checks.append({"name": "http_health", "ok": bool(health.get("ok"))})

        key = _admin_key(self.admin_key_path)
        composite = _json_request(
            f"{self.endpoints.http}/agent/stack/health", admin_key=key,
        )
        checks.append({
            "name": "http_contract",
            "ok": bool((composite.get("http") or {}).get("contract_aligned")),
        })

        local_names = self.catalog_names_provider()
        live_names = set((composite.get("catalog") or {}).get("names") or [])
        catalog_ok = local_names == live_names and bool(local_names)
        checks.append({
            "name": "catalog_parity",
            "ok": catalog_ok,
            "local_count": len(local_names),
            "live_count": len(live_names),
            "missing_live": sorted(local_names - live_names),
            "unexpected_live": sorted(live_names - local_names),
        })

        sidecar_required = self._sidecar_required(require_sidecar)
        sidecar = composite.get("sidecar") or {}
        sidecar_ok = bool(sidecar.get("ok")) if sidecar_required else True
        checks.append({
            "name": "sidecar_contract",
            "ok": sidecar_ok,
            "required": sidecar_required,
            "available": bool(sidecar.get("available")),
            "contract_aligned": bool(sidecar.get("contract_aligned")),
        })

        component_states = []
        from services_registry import desired_state, key_for_unit
        for unit in RUNTIME_COMPONENT_UNITS:
            state = self.systemctl.show(unit)
            installed = state.get("LoadState") not in {"not-found", "error", ""}
            active = state.get("ActiveState") == "active"
            service_key = key_for_unit(unit, "user")
            target_state = desired_state(service_key) if service_key else "running"
            component_states.append({
                "unit": unit,
                "installed": installed,
                "active": active,
                "service": service_key,
                "desired_state": target_state,
            })
        components_ok = all(
            not row["installed"]
            or row["active"]
            or row["desired_state"] == "stopped"
            for row in component_states
        )
        checks.append({
            "name": "managed_components",
            "ok": components_ok,
            "components": component_states,
        })

        # Dependencies that previously produced false-positive ``active``
        # states: SearXNG is probed functionally and llama-server is checked
        # for a kernel-stopped MainPID observed during a provider outage.
        for service_key in WATCHED_SERVICE_KEYS:
            try:
                service = _watched_service_snapshot(service_key)
                target_state = desired_state(service_key)
                service["desired_state"] = target_state
                service_ok = (
                    target_state == "stopped"
                    or _watched_service_ok(service_key, service)
                )
            except Exception as exc:  # keep a complete diagnostic report
                service = {
                    "observation_error": type(exc).__name__,
                    "detail": str(exc),
                }
                service_ok = False
            checks.append({
                "name": f"service_health:{service_key}",
                "ok": service_ok,
                "service": service,
            })

        quiescent = bool(composite.get("quiescent"))
        checks.append({
            "name": "quiescent",
            "ok": quiescent if require_quiescent else True,
            "observed": quiescent,
            "required": require_quiescent,
            "active_turns": int((composite.get("http") or {}).get("active_turns") or 0),
            "active_sessions": int(sidecar.get("active_sessions") or 0),
            "approval_pending_sessions": int(sidecar.get("approval_pending_sessions") or 0),
            "factor_pending_sessions": int(sidecar.get("factor_pending_sessions") or 0),
            "pending_opens": int(sidecar.get("pending_opens") or 0),
        })

        ok = all(check["ok"] for check in checks)
        report = {
            "schema_version": SCHEMA_VERSION,
            "kind": "metnos.stack.check",
            "ok": ok,
            "ready": ok,
            "duration_ms": int((time.time() - started) * 1000),
            "checks": checks,
        }
        persist_report = (
            self.default_write_report if write_report is None else write_report
        )
        if persist_report:
            _atomic_json(self.report_path, report)
        if not ok:
            failed = [check["name"] for check in checks if not check["ok"]]
            raise StackFailure(
                "stack_not_ready", "composite readiness failed",
                details={"failed_checks": failed, "report": report},
            )
        return report

    def wait_ready(self, *, timeout_s: float = 120,
                   require_sidecar: str = "auto") -> dict:
        deadline = time.monotonic() + timeout_s
        last: StackFailure | None = None
        while time.monotonic() < deadline:
            try:
                return self.check(require_sidecar=require_sidecar)
            except StackFailure as exc:
                last = exc
                time.sleep(1)
        raise StackFailure(
            "ready_timeout", "stack did not become ready before timeout",
            details={"last_error": last.code if last else "unknown"},
        )

    def require_quiescent(self) -> dict:
        """Prove that no HTTP turn or browser operation can be interrupted.

        Readiness is deliberately not required here: a stale catalog or
        contract is precisely what a coordinated restart may repair.  When
        HTTP is unreachable, recovery is allowed only if systemd also proves
        the HTTP process inactive and the sidecar broker independently reports
        zero work.
        """
        try:
            composite = _json_request(
                f"{self.endpoints.http}/agent/stack/health",
                admin_key=_admin_key(self.admin_key_path),
            )
            if not composite.get("quiescent"):
                raise StackFailure(
                    "stack_busy", "active turns or browser operations block restart",
                )
            return {
                "ok": True,
                "source": "composite_health",
                "active_turns": int((composite.get("http") or {}).get("active_turns") or 0),
            }
        except StackFailure as exc:
            if exc.code == "stack_busy":
                raise
            http_state = self.systemctl.show("metnos-http.service")
            if http_state.get("ActiveState") in {"active", "activating", "reloading"}:
                raise StackFailure(
                    "quiescence_unknown",
                    "HTTP is active but its in-flight turn counter is unavailable",
                ) from exc
            try:
                sidecar = _json_request(f"{self.endpoints.sidecar}/health")
            except StackFailure as sidecar_exc:
                sidecar_state = self.systemctl.show("metnos-playwright.service")
                if sidecar_state.get("ActiveState") in {
                    "active", "activating", "reloading",
                }:
                    raise StackFailure(
                        "quiescence_unknown",
                        "Playwright is active but broker state is unavailable",
                    ) from sidecar_exc
                return {
                    "ok": True,
                    "source": "inactive_http_and_inactive_sidecar",
                }
            broker = sidecar.get("broker") if isinstance(sidecar.get("broker"), dict) else {}
            busy = any(
                int(broker.get(key) or 0) != 0
                for key in (
                    "active_sessions", "approval_pending_sessions",
                    "factor_pending_sessions", "pending_opens",
                )
            )
            if busy:
                raise StackFailure(
                    "stack_busy", "browser broker activity blocks restart",
                )
            return {"ok": True, "source": "inactive_http_and_sidecar_broker"}

    def restart(self, *, executor_names: list[str] | None = None,
                sign_first: bool = False, automatic: bool = False,
                require_sidecar: str = "auto") -> dict:
        names = executor_names or []
        locks = contextlib.ExitStack()
        locks.enter_context(catalog_reconcile_lock(wait_s=2))
        breaker = CircuitBreaker()
        try:
            if automatic:
                breaker.assert_closed()
            self.require_quiescent()
            signed = verify_named_executors(names, sign_first=sign_first)
            target = self.systemctl.show(TARGET_UNIT)
            if target.get("LoadState") in {"not-found", "error", ""}:
                raise StackFailure(
                    "target_not_installed",
                    "metnos.target is not installed; use the migration pilot first",
                )
            legacy_http = self.systemctl.show("metnos-http.service", "system")
            if legacy_http.get("ActiveState") in {
                "active", "activating", "reloading",
            }:
                raise StackFailure(
                    "legacy_baseline_active",
                    "refusing to start the user target beside active system HTTP",
                )
            result = self.systemctl.run("user", "restart", TARGET_UNIT, timeout_s=180)
            if result.returncode != 0:
                raise StackFailure(
                    "target_restart_failed", "systemd rejected target restart",
                    details={"detail": (result.stderr or result.stdout or "")[-300:]},
                )
            ready = self.wait_ready(require_sidecar=require_sidecar)
            breaker.success()
            return {"ok": True, "signed": signed, "readiness": ready}
        except StackFailure as exc:
            # ``assert_closed`` is itself the circuit's refusal to attempt a
            # restart.  Recording that refusal as a new restart failure moves
            # ``opened_until`` forward on every watchdog tick, so the circuit
            # can never close while the endpoint remains unavailable.  Only a
            # restart attempt that passed the circuit gate contributes a
            # failure sample.
            if automatic and exc.code != "circuit_open":
                breaker.failure()
            raise
        finally:
            locks.close()

    @staticmethod
    def _validate_watched_target(key: str, row: dict) -> tuple[str, str]:
        from services_registry import get

        spec = get(key)
        allowed = {
            (target.scope, target.unit) for target in (spec.targets if spec else ())
        }
        target = (str(row.get("scope") or ""), str(row.get("unit") or ""))
        if key not in WATCHED_SERVICE_KEYS or target not in allowed:
            raise StackFailure(
                "invalid_service_target",
                "resolved service target is outside the closed catalog",
                details={"service": key, "scope": target[0], "unit": target[1]},
            )
        return target

    @staticmethod
    def _wait_watched_healthy(key: str, *, timeout_s: float = 20) -> dict:
        deadline = time.monotonic() + timeout_s
        last: dict = {}
        while time.monotonic() < deadline:
            last = _watched_service_snapshot(key, probe_endpoint=True)
            if _watched_service_ok(key, last) and (
                key == "durable_workloads" or last.get("healthy") is True
            ):
                return last
            time.sleep(0.5)
        raise StackFailure(
            "service_repair_timeout", f"{key} did not become healthy",
            details={"service": key, "last": last},
        )

    def _repair_watched(self, keys: list[str], *,
                        require_sidecar: str = "auto") -> dict:
        locks = contextlib.ExitStack()
        locks.enter_context(catalog_reconcile_lock(wait_s=2))
        breaker = CircuitBreaker()
        actions: list[dict] = []
        try:
            breaker.assert_closed()
            for key in keys:
                row = _watched_service_snapshot(key)
                scope, unit = self._validate_watched_target(key, row)
                if _watched_service_ok(key, row):
                    continue

                if key == "llm" and row.get("process_stopped"):
                    try:
                        pid = int(row.get("main_pid") or 0)
                    except (TypeError, ValueError):
                        pid = 0
                    process_uid = _read_process_uid(pid)
                    expected_uid = _service_uid()
                    # Re-read state immediately before the signal.  PID,
                    # ownership and closed unit identity must all still agree.
                    if (
                        pid <= 1
                        or process_uid is None
                        or process_uid != expected_uid
                        or _read_process_state(pid) not in {"T", "t"}
                    ):
                        raise StackFailure(
                            "unsafe_process_resume",
                            "refusing to resume an unverified LLM process",
                            details={
                                "pid": pid, "process_uid": process_uid,
                                "expected_uid": expected_uid,
                            },
                        )
                    os.kill(pid, signal.SIGCONT)
                    actions.append({
                        "service": key, "action": "sigcont", "pid": pid,
                        "scope": scope, "unit": unit,
                    })
                    self._wait_watched_healthy(key)
                    continue

                if (
                    key == "durable_workloads"
                    and row.get("health_detail")
                    in _DURABLE_NON_RESTARTABLE_REASONS
                ):
                    raise StackFailure(
                        "service_repair_unsafe",
                        "LRE requires configuration or operator repair",
                        details={
                            "service": key,
                            "reason_code": row.get("health_detail"),
                        },
                    )

                # A functional SearXNG failure (or a normally stopped LLM)
                # needs a real unit restart.  Prove no user turn/browser
                # operation can be interrupted before asking systemd.
                if key != "durable_workloads":
                    self.require_quiescent()
                result = self.systemctl.run(
                    scope, "restart", unit, timeout_s=60)
                if result.returncode != 0:
                    raise StackFailure(
                        "service_restart_failed",
                        f"systemd rejected restart of {unit}",
                        details={
                            "service": key,
                            "detail": (result.stderr or result.stdout or "")[-300:],
                        },
                    )
                actions.append({
                    "service": key, "action": "restart",
                    "scope": scope, "unit": unit,
                })
                self._wait_watched_healthy(key)

            ready = self.wait_ready(require_sidecar=require_sidecar)
            breaker.success()
            return {"ok": True, "repaired": actions, "readiness": ready}
        except StackFailure as exc:
            if exc.code != "circuit_open":
                breaker.failure()
            raise
        finally:
            locks.close()

    def watchdog(self, *, require_sidecar: str = "auto") -> dict:
        try:
            from service_health_monitor import run as monitor_services
            monitoring = monitor_services()
        except Exception as exc:  # notification failure must not mask repair
            monitoring = {
                "ok": False,
                "error": type(exc).__name__,
            }
        try:
            result = self.check(require_sidecar=require_sidecar)
            result["service_monitor"] = monitoring
            return result
        except StackFailure as exc:
            failed = set(exc.details.get("failed_checks") or [])
            prefix = "service_health:"
            watched_checks = {f"{prefix}{key}" for key in WATCHED_SERVICE_KEYS}
            if failed and failed.issubset(watched_checks):
                keys = sorted(name[len(prefix):] for name in failed)
                result = self._repair_watched(
                    keys, require_sidecar=require_sidecar)
            else:
                result = self.restart(
                    automatic=True, require_sidecar=require_sidecar,
                )
            result["service_monitor"] = monitoring
            return result


def _failure_payload(exc: StackFailure) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "error_code": exc.code,
        "error": str(exc),
        "details": exc.details,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "check", "wait-ready", "deploy", "watchdog", "inventory",
            "catalog",
        ),
    )
    parser.add_argument("--executor", action="append", default=[])
    parser.add_argument("--sign", action="store_true")
    parser.add_argument("--require-sidecar", choices=("auto", "yes", "no"), default="auto")
    parser.add_argument("--require-quiescent", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reconciler = StackReconciler()
    try:
        if args.command == "check":
            out = reconciler.check(
                require_sidecar=args.require_sidecar,
                require_quiescent=args.require_quiescent,
            )
        elif args.command == "wait-ready":
            out = reconciler.wait_ready(
                timeout_s=args.timeout,
                require_sidecar=args.require_sidecar,
            )
        elif args.command == "deploy":
            if args.sign and not args.executor:
                raise StackFailure("executor_required", "--sign requires at least one --executor")
            out = reconciler.restart(
                executor_names=args.executor,
                sign_first=args.sign,
                require_sidecar=args.require_sidecar,
            )
        elif args.command == "watchdog":
            out = reconciler.watchdog(require_sidecar=args.require_sidecar)
        elif args.command == "inventory":
            out = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "target": reconciler.systemctl.show(TARGET_UNIT),
                "units": {
                    unit: reconciler.systemctl.show(unit)
                    for unit in STACK_UNITS
                },
            }
        else:
            out = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "names": sorted(_catalog_names()),
            }
    except StackFailure as exc:
        print(json.dumps(_failure_payload(exc), ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
