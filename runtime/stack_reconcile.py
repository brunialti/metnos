#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
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
import re
import signal
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
from durable_workloads.activity import activity_snapshot as _durable_activity_snapshot
from executor_birth_maintenance_units import (
    CONTRACT_CUTOVER_UNITS,
    MAINTENANCE_TARGETS_V1,
)


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
# These refusals invalidate the shared admission authority, not just a
# candidate. Unexpected exceptions and failed store rereads also stop the
# sweep; ordinary contract refusals are collected before reporting failure.
_GLOBAL_BIRTH_FAILURE_PREFIXES = (
    "birth_context_", "birth_bootstrap_", "birth_authority_",
    "birth_producer_registry_", "birth_commit_link_", "birth_unavailable",
    "catalog_", "lock_", "unsafe_lock", "productive_",
    "installation_", "store_integrity_",
)


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
        if path is None:
            from services_registry import service_user, stack_scope

            try:
                scope = stack_scope()
            except ValueError as exc:
                raise StackFailure(
                    "stack_profile_unavailable", "installed stack profile is unavailable",
                ) from exc
            if scope == "system":
                # Administrative callers and the service (including watchdog)
                # must hold the same lock, without requiring a login session.
                path = _state_dir() / "metnos-stack-reconcile.lock"
                if owner_uid is None:
                    try:
                        owner_uid = pwd.getpwnam(service_user()).pw_uid
                    except KeyError as exc:
                        raise StackFailure(
                            "service_user_invalid", "Metnos service user does not exist",
                        ) from exc
            else:
                runtime = Path(os.environ.get(
                    "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}",
                ))
                path = runtime / "metnos-stack-reconcile.lock"
        self.path = path
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


class ReconcileBoundaries:
    """The two boundaries a reconcile holds, with the catalog releasable early.

    They are not interchangeable.  The lifecycle boundary serialises the whole
    operation and must be held to its end.  The catalog boundary only protects
    reading and signing contracts: holding it while systemd restarts the stack
    starves the very server being started, which cannot read its catalog, is
    therefore never ready, and is quarantined — the reconcile taking down what
    it was repairing.  It also blocks every ordinary turn for as long as the
    restart and the readiness wait take, which is minutes, not seconds.
    """

    def __init__(self, lifecycle: ReconcileLock, release_catalog):
        self.lifecycle = lifecycle
        self._release_catalog = release_catalog

    def release_catalog(self) -> None:
        """Free the catalog boundary; idempotent, and safe to never call."""
        self._release_catalog()


@contextlib.contextmanager
def catalog_reconcile_lock(
    *,
    lock: ReconcileLock | None = None,
    path: Path | None = None,
    owner_uid: int | None = None,
    catalog_trusted_owner: tuple[int, int] | None = None,
    wait_s: float = 2.0,
):
    """Acquire the catalog and lifecycle boundaries in one canonical order."""
    from contract_store import ContractStoreError, catalog_admission_lock

    try:
        catalog_options = {"timeout": wait_s}
        if catalog_trusted_owner is not None:
            catalog_options["trusted_owner"] = catalog_trusted_owner
        catalog = catalog_admission_lock(**catalog_options)
        catalog.__enter__()
    except ContractStoreError as exc:
        raise StackFailure(
            "catalog_lock_unavailable", f"{exc.code}: {exc.detail}",
        ) from exc
    catalog_held = True

    def _release_catalog(*exc_info) -> None:
        nonlocal catalog_held
        if not catalog_held:
            return
        catalog_held = False
        catalog.__exit__(*(exc_info or (None, None, None)))

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
            yield ReconcileBoundaries(selected, _release_catalog)
        finally:
            selected.release()
    finally:
        _release_catalog(*sys.exc_info())


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
        # The service topology is the sole authority for both productive and
        # legacy maintenance observations.  In particular, cutover must prove
        # the retired system timers idle without widening this adapter to
        # arbitrary systemd units or accepting a catalog unit in the wrong
        # manager scope.
        from executor_birth_service_catalog import SERVICE_SOURCE_V1

        productive = scope == "system" and any(
            item.unit_name == unit for item in SERVICE_SOURCE_V1
        )
        if (scope, unit) not in MAINTENANCE_TARGETS_V1 and not productive:
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
                              probe_endpoint: bool | None = None,
                              spec: Any | None = None) -> dict:
    """Resolve one closed-catalog dependency and add process-state evidence.

    SearXNG and LRE are checked functionally.  The LLM is deliberately not
    HTTP-probed during every readiness pass because a long inference may
    occupy its slots; its critical false-``active`` failure is instead
    detected from a stopped systemd MainPID (Linux state ``T``/``t``).
    Recovery performs an explicit endpoint probe before declaring success.
    """
    from services_registry import get, snapshot_one

    spec = get(key) if spec is None else spec
    if spec is None or spec.key != key or key not in WATCHED_SERVICE_KEYS:
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


def _release_plan_context(evidence: bytes | None = None):
    """Read sealed authority only; never bootstrap, recover or select a head."""
    from executor_birth_prepared_root import (
        load_previous_context_runtime_v1, load_required_context_runtime_v1,
    )
    if evidence is None:
        return load_required_context_runtime_v1()
    from executor_birth_distribution_manifest import (
        authenticate_distribution_record_v1, verify_current_installation_distribution_v1,
    )
    pair = json.loads(evidence)
    encoded = pair["distribution"].encode("utf-8")
    signature = bytes.fromhex(pair["signature"])
    # Verify the successor's bytes with its reviewed policy, and N's context
    # through the existing explicit transition reader. Never pretend N+1 is
    # selected or feed N into N+1's live runtime bootstrap (C15).
    verify_current_installation_distribution_v1(encoded, signature)
    return load_previous_context_runtime_v1(
        authenticate_distribution_record_v1(encoded, signature))


def _release_plan_snapshot(ref, current, trusted):
    """Owned read snapshot; no store lock creation, repair or state writes."""
    from contract_store import ContractStoreError, current_contract
    from executor_birth_snapshot import _acquire_authenticated_current_snapshot
    from manifest_code_digest import code_digest_of_payloads

    snapshot, signature = _acquire_authenticated_current_snapshot(ref.manifest_dir)
    try:
        if (snapshot.manifest_bytes != current.manifest_bytes
                or snapshot.language_state_bytes != current.language_state_bytes
                or signature != current.signature_bytes
                or code_digest_of_payloads(
                    current.parsed["code"]["files"], snapshot.code_files
                ) != current.declared_code_digest
                or current_contract(ref, trusted_publics=trusted) != current):
            raise ContractStoreError("birth_reattestation_source_changed")
        return snapshot
    except BaseException:
        snapshot.close()
        raise


def _release_plan_details(ref, current, served, prepared, language, code, context):
    """Informational projection of bytes, not an admission or future promise."""
    import hashlib
    import tomllib
    from contract_store import inspect_birth_receipts
    from executor_birth_operational import birth_failure_diagnostic

    candidate, previous = tomllib.loads(prepared.decode()), tomllib.loads(served.manifest_bytes.decode())
    changes = [key for key, before, after in (
        ("manifest", served.manifest_bytes, prepared),
        ("language_state", served.language_state_bytes, language),
        ("code", dict(served.code_files), code),
    ) if before != after]
    row = {
        "changes": changes, "served_version": previous.get("version"),
        "candidate_version": candidate.get("version"),
        "candidate_manifest_sha256": hashlib.sha256(prepared).hexdigest(),
        "candidate_language_sha256": hashlib.sha256(language).hexdigest(),
        "candidate_code_digest": candidate.get("code", {}).get("digest"),
        "served_code_digest": previous.get("code", {}).get("digest"),
        "selected_head_id": context.required_head_id,
        "selected_context_id": context.selection.admission_context_id,
        "destination": {"status": "not_evaluated", "reason": "candidate_not_admitted"},
        "runtime_module": {"status": "not_evaluated", "reason": "verified_by_loader_at_activation"},
    }
    try:
        row.update(inspect_birth_receipts(
            ref, current.generation_id, prepared, language, context_runtime=context))
    except Exception as exc:
        row["receipts"] = {"status": "not_evaluated", "diagnostic": dataclasses.asdict(
            birth_failure_diagnostic(exc, "receipt"))}
    return row


def verify_named_executors(
        names: list[str], *, sign_first: bool = False,
        changed_only: bool = False, plan_only: bool = False,
        preview_evidence: bytes | None = None,
        source_root: Path | None = None) -> list[dict]:
    """Admit/verify explicitly named direct children of ``executors/``.

    ``sign_first`` is retained as the public compatibility flag.  It now
    means admission through the sealed Executor Birth service; it never
    selects a technical publisher.

    ``changed_only`` compares each first-party executor of this installation
    with the generation the store serves - manifest, language state and every
    declared file by name - and admits only those that differ. ``plan_only``
    reports that comparison without any Birth request. Each row carries one
    outcome: ``unchanged``, ``not_installed``, ``changed`` (plan),
    ``store_verified`` (admitted, and the store now serves that generation),
    ``error`` or ``not_attempted``. Publications are per contract: a refusal
    after an admission leaves the earlier admission in place and reported.

    An explicit ``source_root`` selects untrusted authoring inputs only. It
    never changes runtime imports, configuration, authority or the installed
    catalog. Explicit names prevent a partial checkout from becoming a sweep.
    """
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise StackFailure("invalid_executor", "executor name is not canonical")
    if source_root is not None:
        if not names or not (sign_first or plan_only) or preview_evidence is not None:
            raise StackFailure(
                "source_root_invalid", "authoring input requires named admission or a plan",
            )
        source_root = Path(source_root)
        try:
            if (not source_root.is_absolute() or ".." in source_root.parts
                    or source_root.resolve(strict=True) != source_root
                    or not source_root.is_dir()):
                raise ValueError("non-canonical source root")
        except (OSError, RuntimeError, ValueError) as exc:
            raise StackFailure("source_root_invalid", "authoring root is unavailable or linked") from exc
        candidate_root = source_root / "executors"
        for name in names:
            directory = candidate_root / name
            try:
                if (directory.resolve(strict=True) != directory
                        or not (directory / "manifest.toml").is_file()):
                    raise ValueError("candidate missing or linked")
            except (OSError, RuntimeError, ValueError) as exc:
                raise StackFailure("candidate_unavailable", f"{name}: authoring input is unavailable") from exc
    from manifest_inventory import (
        ContractId, ManifestLayout, ManifestOrigin, inventory_manifests,
        resolve_manifest_layout,
    )
    from sign import verify_executor

    layout = resolve_manifest_layout()
    root = ((source_root or _repo_root()) / "executors").resolve()
    store_refs = (
        inventory_manifests().by_id()
        if layout is ManifestLayout.STORE_ONLY else {}
    )
    if changed_only or plan_only:
        if layout is not ManifestLayout.STORE_ONLY or not (sign_first or plan_only):
            raise StackFailure(
                "changed_only_invalid",
                "changed-only admission needs the store-only layout and --sign or --plan",
            )
        from contract_store import (
            ContractStoreError, VerifiedManifest,
            acquire_current_reattestation_snapshot, current_contract,
        )
        from executor_birth_intent import (
            BirthIntent, submit_builtin_generation_birth, submit_stack_reconcile_birth,
        )
        from executor_birth_identity import (
            CandidateIdentityInput, ExecutorOrigin, IdentityError, RevisionAuthor,
            semantic_core_id,
        )
        from executor_birth_snapshot import (
            LANGUAGE_STATE_FILE, MANIFEST_FILE, CandidateSnapshotError,
            _declared_code_files, _read_regular,
            materialize_birth_candidate_from_authoring,
        )
        from sign import list_trusted_publics
        from i18n_materializer import LanguageStateError, decode_language_state
        import tomllib
        import tomlkit

        from executor_birth_operational import birth_failure_diagnostic
        context, context_error = None, None
        if plan_only:
            try:
                context = _release_plan_context(preview_evidence)
            except Exception as exc:
                context_error = dataclasses.asdict(birth_failure_diagnostic(exc, "context"))
        trusted = (tuple(context.authorities.author.verifier_keys.items()) if context
                   else () if plan_only else tuple(list_trusted_publics()))
        core, builtin = ManifestOrigin.CORE, ManifestOrigin.BUILTIN
        # ``prepare`` derives each builtin copy from the reviewed code, and
        # Birth admits it with the builtin capability.  One list and one loop
        # serve both origins, so a refusal always reports every later entry.
        sources = {
            core: root,
            builtin: (_repo_root() / "runtime" / "builtin_executor_contracts").resolve(),
        }
        submitters = {core: (submit_stack_reconcile_birth, "executor"),
                      builtin: (submit_builtin_generation_birth, "builtin")}
        pending = [(core, name) for name in list(names) or sorted(
            path.name for path in root.iterdir()
            if (path / MANIFEST_FILE).is_file()
        )]
        # An explicit selection names core executors only; the whole
        # changed-only sweep also covers the installed builtin contracts.
        if not names and sources[builtin].is_dir():
            pending += [(builtin, path.name) for path in sorted(sources[builtin].iterdir())
                        if (path / MANIFEST_FILE).is_file()]

        def identity(origin, name):
            # Core rows keep their historical shape: an absent origin is core.
            return {"name": name} if origin is core else {"name": name, "origin": origin.value}

        outcomes: list[dict] = []
        first_failure: StackFailure | None = None

        def refuse(index, code, detail):
            outcomes.extend({**identity(*item), "outcome": "not_attempted"}
                            for item in pending[index + 1:])
            return StackFailure(code, detail, details={"outcomes": outcomes})

        with tempfile.TemporaryDirectory(prefix="metnos-reconcile-release-") as raw:
            for index, (origin, name) in enumerate(pending):
                if not name or name in {".", ".."} or "/" in name or "\\" in name:
                    raise StackFailure("invalid_executor", "executor name is not canonical")
                base = identity(origin, name)
                contract_id = ContractId(origin, f"{name}/manifest.toml")
                ref = store_refs.get(contract_id)
                if plan_only and context is None:
                    outcomes.append({**base, "outcome": "not_evaluated",
                                     "diagnostic": context_error})
                    continue
                try:
                    current = (
                        None if ref is None
                        else current_contract(ref, trusted_publics=trusted)
                    )
                    new_core = ref is None and origin is core
                    if not new_core and not isinstance(current, VerifiedManifest):
                        outcomes.append({**base, "outcome": "not_installed"})
                        continue
                    # The closed candidate check refuses anything undeclared,
                    # caches included: an installation carries none.
                    candidate = materialize_birth_candidate_from_authoring(
                        sources[origin] / name,
                        Path(raw) / f"candidate-{origin.value}-{name}",
                    )
                    prepared = _read_regular(candidate, MANIFEST_FILE)
                    if origin is builtin and _read_regular(
                            sources[origin] / name, MANIFEST_FILE) != prepared:
                        # Its digest had to be recomputed: the generator did
                        # not derive this copy from the code being released.
                        raise CandidateSnapshotError("builtin_candidate_stale", name)
                    language_state = _read_regular(candidate, LANGUAGE_STATE_FILE)
                    try:
                        decode_language_state(
                            language_state, manifest=tomllib.loads(prepared.decode("utf-8")))
                    except LanguageStateError as exc:
                        # Preview must reject the same stale companion that
                        # publication would reject after the release cutover.
                        raise CandidateSnapshotError(exc.code, exc.detail) from exc
                    declared = _declared_code_files(prepared)
                    code = {item: _read_regular(candidate, item) for item in declared}
                    same = False
                    if not new_core:
                        snapshot = (_release_plan_snapshot(ref, current, trusted) if plan_only
                                    else acquire_current_reattestation_snapshot(
                                ref, current.generation_id,
                                trusted_publics=trusted))
                        with snapshot as served:
                            if origin is builtin:
                                # Regeneration owns code/schema, not the served
                                # line's version. Normalize before changed-only
                                # and before Birth, after the stale-copy check.
                                document = tomlkit.parse(prepared.decode("utf-8"))
                                served_document = tomlkit.parse(served.manifest_bytes.decode("utf-8"))
                                if (tomlkit.dumps(document).encode("utf-8") != prepared
                                        or tomlkit.dumps(served_document).encode("utf-8") != served.manifest_bytes):
                                    raise CandidateSnapshotError("builtin_version_roundtrip_changed", name)
                                version = served_document.get("version")
                                if not isinstance(version, str) or not version:
                                    raise CandidateSnapshotError("builtin_version_invalid", name)
                                if document.get("version") != version:
                                    document["version"] = version
                                    prepared = tomlkit.dumps(document).encode("utf-8")
                                    (candidate / MANIFEST_FILE).write_bytes(prepared)
                            same = (
                                served.manifest_bytes == prepared
                                and served.language_state_bytes == language_state
                                and dict(served.code_files) == code
                            )
                            if plan_only:
                                base.update(_release_plan_details(
                                    ref, current, served, prepared, language_state, code, context))
                    if plan_only:
                        # The semantic projection ignores provenance. This
                        # read-only input issues no identity or authority for
                        # a future request; Birth constructs its own envelope.
                        import hashlib
                        base["candidate_semantic_core_id"] = semantic_core_id(
                            CandidateIdentityInput(
                                contract_id=contract_id, manifest_bytes=prepared,
                                language_state_bytes=language_state, code_files=code,
                                executor_origin=ExecutorOrigin(origin.value),
                                revision_authorship=RevisionAuthor.MAINTENANCE,
                                objective_hash="sha256:" + hashlib.sha256(prepared).hexdigest(),
                            ))
                        base["destination_context"] = (
                            {"status": "not_evaluated", "reason": "cutover_not_completed"}
                            if preview_evidence is not None else
                            {"status": "selected", "admission_context_id": context.selection.admission_context_id})
                except (OSError, CandidateSnapshotError, ContractStoreError, IdentityError) as exc:
                    outcomes.append({**base, "outcome": "error", "diagnostic": dataclasses.asdict(
                        birth_failure_diagnostic(exc, "candidate"))})
                    if plan_only:
                        continue
                    # Preserve the existing admission failure shape. The
                    # read-only preview emits only the redacted diagnostic.
                    outcomes[-1]["error"] = str(exc)
                    if not isinstance(exc, CandidateSnapshotError):
                        raise refuse(index, "candidate_unavailable", f"{name}: {exc}") from exc
                    if first_failure is None:
                        first_failure = StackFailure(
                            "candidate_unavailable", f"{name}: {exc}", details={"outcomes": outcomes},
                        )
                    continue
                if same or plan_only:
                    outcomes.append({**base, "outcome": "unchanged" if same else "changed",
                                     "generation_id": current.generation_id if current else None})
                    continue
                submit, kind = submitters[origin]
                failure = ""
                try:
                    birth = submit(BirthIntent(
                        candidate_source_root=candidate,
                        contract_id=contract_id,
                        reason=f"release edit admission {kind}={name}",
                    ))
                except Exception as exc:
                    birth, failure = None, str(exc)
                row = {**base, "outcome": "error",
                       "request_id": getattr(birth, "request_id", None)}
                if getattr(birth, "diagnostic", None) is not None:
                    row["diagnostic"] = dataclasses.asdict(birth.diagnostic)
                publication = getattr(birth, "publication", None)
                if birth is None or birth.error_code or publication is None:
                    row["error"] = failure or (birth.error_code if birth else "") \
                        or "publication_missing"
                else:
                    row.update({
                        "candidate_id": birth.report.candidate_id,
                        "previous_generation_id": publication.previous_generation_id,
                        "current_generation_id": publication.current_generation_id,
                    })
                    try:
                        # A first admission must be visible through the ordinary
                        # store inventory, not a fabricated source reference.
                        if new_core:
                            ref = inventory_manifests().by_id().get(contract_id)
                        reread = current_contract(ref, trusted_publics=trusted) if ref else None
                    except ContractStoreError as exc:
                        reread, row["error"] = None, str(exc)
                    if (isinstance(reread, VerifiedManifest) and reread.generation_id
                            == publication.current_generation_id):
                        row["outcome"] = "store_verified"
                    else:
                        row.setdefault("error", "store_generation_mismatch")
                outcomes.append(row)
                if row["outcome"] == "error":
                    # A returned contract refusal is local. An unexpected
                    # exception, lost authority or invalid publication
                    # postcondition cannot authorize the next mutation.
                    if (birth is None or publication is not None
                            or str(row["error"]).startswith(_GLOBAL_BIRTH_FAILURE_PREFIXES)):
                        raise refuse(index, "birth_admission_failed", f"{name}: {row['error']}")
                    if first_failure is None:
                        first_failure = StackFailure(
                            "birth_admission_failed", f"{name}: {row['error']}",
                            details={"outcomes": outcomes},
                        )
        if first_failure is not None:
            raise first_failure
        return outcomes

    selected: list[tuple[str, Path | None, object | None]] = []
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise StackFailure("invalid_executor", "executor name is not canonical")
        contract_id = ContractId(ManifestOrigin.CORE, f"{name}/manifest.toml")
        if layout is ManifestLayout.STORE_ONLY:
            ref = store_refs.get(contract_id)
            if ref is None:
                raise StackFailure("unknown_executor", f"executor {name!r} is not installed")
            # A deploy publishes the operator's working copy; without one it
            # re-admits the bytes the store already names.
            working = root / name
            directory = (
                working.resolve() if (working / "manifest.toml").is_file()
                else None
            )
            if directory is not None:
                try:
                    directory.relative_to(root)
                except ValueError as exc:
                    raise StackFailure(
                        "invalid_executor", "executor escapes the catalog root",
                    ) from exc
        else:
            ref = None
            directory = (root / name).resolve()
            try:
                directory.relative_to(root)
            except ValueError as exc:
                raise StackFailure("invalid_executor", "executor escapes the catalog root") from exc
        if directory is not None and not (directory / "manifest.toml").is_file():
            raise StackFailure("unknown_executor", f"executor {name!r} is not installed")
        selected.append((name, directory, ref))
        if sign_first:
            try:
                from executor_birth_intent import BirthIntent, submit_stack_reconcile_birth
                with tempfile.TemporaryDirectory(
                    prefix=f"metnos-reconcile-{name}-birth-",
                ) as raw_staging:
                    from executor_birth_snapshot import (
                        materialize_birth_candidate_from_authoring,
                        materialize_birth_candidate_from_manifest_ref,
                    )

                    staging = (
                        materialize_birth_candidate_from_authoring(
                            directory, Path(raw_staging) / name,
                        ) if directory is not None else
                        materialize_birth_candidate_from_manifest_ref(
                            ref, Path(raw_staging) / name,
                        )
                    )
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
        # The verified store loader is the live admission proof after cutover;
        # source verification alone cannot prove which generation is current.
        from loader import load_catalog

        catalog = load_catalog(
            include_synth=True,
            include_verb_unique=False,
        )
        for name, _directory, _ref in selected:
            executor = catalog.executors.get(name)
            row = {"name": name, "ok": executor is not None}
            if executor is None:
                row["reason"] = "executor absent from verified store catalog"
            else:
                row["digest"] = executor.digest
            results.append(row)
    else:
        for name, directory, _ref in selected:
            assert directory is not None
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


class PendingActivation:
    """A restart owed to generations the store may already serve.

    The intent is written before the first Birth request and cleared only
    once the stack is restarted and ready - or once the batch is known to
    have admitted nothing. Anything in between (a refused restart, a reread
    that failed after publication, a process killed mid-batch) leaves the
    intent behind, and the next changed-only deploy restarts even when it
    admits nothing new. Rows keep the outcome they were reported with: an
    uncertain publication stays uncertain. An unreadable record counts as
    owed: doubt costs one restart, never a silent no-op.
    """

    _INTENT = {"name": "*", "outcome": "intent"}
    _UNREADABLE = {"name": "?", "outcome": "unreadable_record"}

    def __init__(self, path: Path | None = None):
        self.path = path or _state_dir() / "stack_reconcile_pending_activation.json"

    def rows(self) -> list[dict]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            return [dict(self._UNREADABLE)]
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            return [dict(self._UNREADABLE)]
        return rows

    def begin(self) -> None:
        self._write([dict(self._INTENT)])

    def record(self, outcomes: list) -> None:
        """Keep every row whose publication happened, verified or not."""
        self._write([row for row in outcomes if isinstance(row, dict)
                     and row.get("current_generation_id")])

    def published(self) -> list[dict]:
        return [row for row in self.rows() if row.get("current_generation_id")]

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _write(self, rows: list[dict]) -> None:
        if rows:
            merged = {(row.get("origin") or "core", row.get("name")): row
                      for row in (*self.rows(), *rows)}
            _atomic_json(self.path, {"rows": list(merged.values())})


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

    def _sidecar_required(self, mode: str, spec: Any) -> bool:
        if mode == "yes":
            return True
        if mode == "no":
            return False
        from services_registry import desired_state
        if desired_state("playwright") == "stopped":
            return False
        target = spec.targets[0]
        state = self.systemctl.show(target.unit, target.scope)
        return state.get("LoadState") not in {"not-found", "error", ""}

    def check(self, *, require_sidecar: str = "auto",
              require_quiescent: bool = False,
              write_report: bool | None = None) -> dict:
        started = time.time()
        checks: list[dict[str, Any]] = []
        from services_registry import desired_state, key_for_unit, readiness_catalog

        try:
            services = {spec.key: spec for spec in readiness_catalog()}
        except Exception as exc:
            raise StackFailure(
                "service_catalog_unavailable", "readiness service catalog is unavailable",
            ) from exc

        health = _json_request(f"{self.endpoints.http}/agent/health")
        checks.append({"name": "http_health", "ok": bool(health.get("ok"))})

        key = _admin_key(self.admin_key_path)
        composite = _json_request(
            f"{self.endpoints.http}/agent/stack/health", admin_key=key,
        )
        if (composite.get("http") or {}).get("operational") is False:
            # This authenticated observation precedes executor catalog loading,
            # which may be unavailable during repair. Liveness is not
            # readiness, and a watchdog restart cannot repair configuration.
            checks.append({"name": "http_runtime", "ok": False,
                           "maintenance_only": True})
            return self._finish_check(
                started, checks, write_report, error_code="runtime_maintenance",
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

        sidecar_required = self._sidecar_required(require_sidecar, services["playwright"])
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
        for unit in RUNTIME_COMPONENT_UNITS:
            service_key = key_for_unit(unit)
            target = next(target for target in services[service_key].targets
                          if target.unit == unit)
            state = self.systemctl.show(target.unit, target.scope)
            installed = state.get("LoadState") not in {"not-found", "error", ""}
            active = state.get("ActiveState") == "active"
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
                service = _watched_service_snapshot(service_key, spec=services[service_key])
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
        durable = composite.get("durable_workloads") or {}
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
            "durable_activity_known": durable.get("known") is True,
            "active_lre_attempts": int(durable.get("active_attempts") or 0),
        })

        return self._finish_check(started, checks, write_report)

    def _finish_check(self, started: float, checks: list[dict],
                      write_report: bool | None, *,
                      error_code: str = "stack_not_ready") -> dict:
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
                error_code, "composite readiness failed",
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
        """Prove that no HTTP, browser or durable operation can be interrupted.

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
            durable = composite.get("durable_workloads") or {}
            if durable.get("known") is not True:
                raise StackFailure(
                    "quiescence_unknown",
                    "durable workload activity is unavailable",
                )
            if not composite.get("quiescent"):
                raise StackFailure(
                    "stack_busy",
                    "active turns, browser operations or durable executions block restart",
                )
            return {
                "ok": True,
                "source": "composite_health",
                "active_turns": int((composite.get("http") or {}).get("active_turns") or 0),
                "active_lre_attempts": int(durable.get("active_attempts") or 0),
            }
        except StackFailure as exc:
            if exc.code in {"stack_busy", "quiescence_unknown"}:
                raise
            http_state = self.systemctl.show("metnos-http.service")
            if http_state.get("ActiveState") in {"active", "activating", "reloading"}:
                raise StackFailure(
                    "quiescence_unknown",
                    "HTTP is active but its in-flight turn counter is unavailable",
                ) from exc
            durable_state = self.systemctl.show("metnos-durable-worker.service")
            durable_active = durable_state.get("ActiveState") in {
                "active", "activating", "reloading",
            }
            if durable_active:
                durable = _durable_activity_snapshot()
                if durable.get("known") is not True:
                    raise StackFailure(
                        "quiescence_unknown",
                        "durable worker is active but its attempt counter is unavailable",
                    ) from exc
                if int(durable.get("active_attempts") or 0) != 0:
                    raise StackFailure(
                        "stack_busy", "durable workload activity blocks restart",
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
                require_sidecar: str = "auto",
                changed_only: bool = False,
                source_root: Path | None = None) -> dict:
        from services_registry import stack_scope

        names = executor_names or []
        locks = contextlib.ExitStack()
        boundaries = locks.enter_context(catalog_reconcile_lock(wait_s=2))
        breaker = CircuitBreaker()
        pending = PendingActivation() if changed_only else None
        signed: list[dict] = []
        try:
            if automatic:
                breaker.assert_closed()
            self.require_quiescent()
            # Refuse an unusable topology before any admission, not after a
            # successful publication that the target cannot activate.
            scope = stack_scope()
            target = self.systemctl.show(TARGET_UNIT, scope)
            if target.get("LoadState") in {"not-found", "error", ""}:
                raise StackFailure(
                    "target_not_installed",
                    "metnos.target is not installed; use the migration pilot first",
                )
            legacy_http = (
                self.systemctl.show("metnos-http.service", "system")
                if scope == "user" else {}
            )
            if legacy_http.get("ActiveState") in {"active", "activating", "reloading"}:
                raise StackFailure(
                    "legacy_baseline_active",
                    "refusing to start the user target beside active system HTTP",
                )
            owed = pending.rows() if pending is not None else []
            if pending is not None:
                # Before any effect: from here a restart is owed until the
                # batch proves it published nothing, or the stack is ready.
                pending.begin()
            try:
                signed = verify_named_executors(
                    names, sign_first=sign_first, changed_only=changed_only,
                    **({"source_root": source_root} if source_root is not None else {}),
                )
            except StackFailure as exc:
                if pending is not None:
                    pending.record(exc.details.get("outcomes") or [])
                raise
            if pending is not None:
                pending.record(signed)
                if not owed and not pending.published():
                    pending.clear()
                    return {"ok": True, "signed": signed, "restarted": False}
            # Every contract read and signature is done.  From here on the
            # operation belongs to systemd, and the server it is starting must
            # be able to read its own catalog while it boots.  The lifecycle
            # boundary stays held: it is what still serialises this restart.
            boundaries.release_catalog()
            result = self.systemctl.run(scope, "restart", TARGET_UNIT, timeout_s=180)
            if result.returncode != 0:
                raise StackFailure(
                    "target_restart_failed", "systemd rejected target restart",
                    details={"detail": (result.stderr or result.stdout or "")[-300:]},
                )
            ready = self.wait_ready(require_sidecar=require_sidecar)
            breaker.success()
            if pending is None:
                return {"ok": True, "signed": signed, "readiness": ready}
            activated = pending.published()
            pending.clear()
            return {"ok": True, "signed": signed, "readiness": ready,
                    "restarted": True, "activated": activated}
        except StackFailure as exc:
            if pending is not None and pending.rows():
                details = dict(exc.details)
                if signed and "outcomes" not in details:
                    details["outcomes"] = signed
                details["activation_pending"] = pending.published()
                details["restart_owed"] = True
                exc.details = details
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
        boundaries = locks.enter_context(catalog_reconcile_lock(wait_s=2))
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
                    # Observation is over; from here the repair belongs to
                    # the kernel and to systemd.  See ``ReconcileBoundaries``.
                    boundaries.release_catalog()
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
                boundaries.release_catalog()
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

            boundaries.release_catalog()
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
            if exc.code in {"runtime_maintenance", "service_catalog_unavailable"}:
                # Preserve authenticated repair and the failure report. Only
                # an explicit maintenance restart should retry bootstrap; an
                # unverified service catalog cannot authorize a restart either.
                raise
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


_ERROR_TYPE_MAX = 256
_ERROR_TYPE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _unexpected_failure_payload(exc: Exception) -> dict:
    """Report an unforeseen failure by type only.

    The message is withheld: it can quote paths, values or secrets, and the
    caller (the release wrapper) forwards this report to operators.
    """
    kind = type(exc)
    name = kind.__qualname__
    if kind.__module__ not in ("builtins", "__main__"):
        name = f"{kind.__module__}.{name}"
    if len(name) > _ERROR_TYPE_MAX or not _ERROR_TYPE_RE.fullmatch(name):
        # A name that cannot be shown whole is not shown at all: truncating
        # it could still copy part of whatever it was built from.
        name = "unrepresentable"
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "error_code": "unexpected_failure",
        "error_type": name,
        "details": {},
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
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--sign", action="store_true")
    parser.add_argument("--changed-only", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--require-sidecar", choices=("auto", "yes", "no"), default="auto")
    parser.add_argument("--require-quiescent", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        # Built inside the report boundary: a failure here must reach the
        # caller as a JSON report, not as an empty stdout (review C15).
        if args.preview and not (
                args.command == "deploy" and args.plan and args.changed_only and not args.sign):
            raise StackFailure("option_invalid", "preview requires a read-only release plan")
        if args.source_root is not None and (
                args.command != "deploy" or not args.executor
                or not (args.plan or args.sign) or args.preview):
            raise StackFailure(
                "option_invalid", "--source-root requires a named deploy admission or plan",
            )
        reconciler = None if args.plan else StackReconciler()
        if args.command != "deploy" and (args.plan or args.changed_only or args.sign):
            raise StackFailure(
                "option_invalid",
                "--plan, --changed-only and --sign belong to deploy only",
            )
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
            if args.plan:
                if not args.changed_only or args.sign:
                    raise StackFailure(
                        "plan_invalid", "--plan needs --changed-only and no --sign",
                    )
                rows = verify_named_executors(
                    args.executor, changed_only=True, plan_only=True,
                    preview_evidence=sys.stdin.buffer.read() if args.preview else None,
                    **({"source_root": args.source_root} if args.source_root is not None else {}),
                )
                out = {"ok": all(row["outcome"] in {"unchanged", "changed", "not_installed"}
                                 for row in rows), "plan": rows,
                       "admission_attempted": False}
            else:
                if args.sign and not args.executor and not args.changed_only:
                    raise StackFailure(
                        "executor_required", "--sign requires at least one --executor",
                    )
                out = reconciler.restart(
                    executor_names=args.executor,
                    sign_first=args.sign,
                    require_sidecar=args.require_sidecar,
                    changed_only=args.changed_only,
                    **({"source_root": args.source_root} if args.source_root is not None else {}),
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
    except Exception as exc:  # noqa: BLE001 - reported, never turned into success
        # KeyboardInterrupt and SystemExit are not Exception: they still stop
        # the process as before.
        print(json.dumps(_unexpected_failure_payload(exc),
                         ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 1 if args.plan and out.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
