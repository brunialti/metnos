"""Administrative one-time cutover to the epoch lifecycle owner.

The cutover is two commands, because its two halves need opposite conditions.
`plan` runs while the service runs: only a live process can say which stores
this installation actually selected. `apply` runs with the stack quiescent,
because every legacy writer lives in those services and a single call during
the copy would write a row nobody preserves — silent loss is the one outcome a
migration may not produce.

Within `apply`, privilege also splits. Dropping to the service account is
irreversible, so the process that writes the service-owned stores cannot also
write the root-owned marker: a child migrates, the parent records. The marker is
the last thing written, so an interruption anywhere before it leaves an
installation that still uses its name-based state and can simply be run again.

Nothing here issues a certificate, publishes an executor or deletes a file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from executor_birth_account_identity import (
    PosixAccountSnapshotV1, metnos_xdg_layout_v1, resolve_posix_account_snapshot_v1,
)
from executor_birth_authority_files import (
    DEFAULT_OWNERSHIP_ROOT_V1, OwnershipAuthorityError,
    _directory_metadata, _managed_authority_platform_supported_v1,
    _read_regular, _root_owned_chain,
)
from executor_birth_host_path_policy import SERVICE_ACCOUNT_NAME_V1
from install.birth_ownership_authority_provisioner import (
    _provisioning_lock, _sync_directory,
)


ACTIVATION_DIRECTORY_V1 = DEFAULT_OWNERSHIP_ROOT_V1 / "certification-v1"
MARKER_BASENAME_V1 = "migration.json"
HANDOFF_BASENAME_V1 = "migration-plan.json"
HANDOFF_PURPOSE_V1 = "f5_migration_plan_v1"
SERVICE_UNIT_V1 = "metnos-http.service"
# Filesystem seam for isolated tests; the productive value is fixed.
_PROC_ROOT_V1 = Path("/proc")
_MAX_REPORT_BYTES = 1 << 20
_CARRIED_ENVIRONMENT_V1 = frozenset({
    "HOME", "METNOS_USER_DATA", "METNOS_USER_STATE", "METNOS_USER_CONFIG",
    "METNOS_EXECUTOR_STATS_DB", "METNOS_PROMOTER_DB",
})
# Every legacy store this cutover knows how to read, and the exact table in it.
_SELECTED_SOURCES_V1 = (
    ("statistics", "METNOS_EXECUTOR_STATS_DB", "state", "executor_stats.db",
     "executor_stats"),
    ("promotions", "METNOS_PROMOTER_DB", "data", "promoter.sqlite",
     "proposal_promote"),
)


class LifecycleCutoverError(RuntimeError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _service_main_pid() -> int:
    """Ask systemd which process is the service, never guess from a name."""
    try:
        raw = subprocess.check_output(
            ["/usr/bin/systemctl", "show", SERVICE_UNIT_V1, "-p", "MainPID",
             "--value"], timeout=10, text=True,
        ).strip()
        return int(raw)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise LifecycleCutoverError("cutover_service_unavailable") from exc


def _service_environment(pid: int, account: PosixAccountSnapshotV1) -> dict[str, str]:
    """Read only the path overrides the running service actually selected."""
    if pid <= 1:
        raise LifecycleCutoverError("cutover_service_unavailable", "main pid")
    process = _PROC_ROOT_V1 / str(pid)
    if process.stat().st_uid != account.record.uid:
        raise LifecycleCutoverError("cutover_service_unavailable", "owner mismatch")
    allowed = {"HOME", "METNOS_USER_DATA", "METNOS_USER_STATE",
               "METNOS_EXECUTOR_STATS_DB", "METNOS_PROMOTER_DB"}
    with open(process / "environ", "rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise LifecycleCutoverError("cutover_service_unavailable", "environment size")
    selected = {}
    for entry in raw.split(b"\0"):
        key, separator, value = entry.partition(b"=")
        if separator and key.decode("utf-8", "replace") in allowed:
            selected[key.decode()] = value.decode("utf-8", "replace")
    return selected


def selected_sources_v1(
    environment: dict[str, str], account: PosixAccountSnapshotV1,
) -> tuple[tuple[str, Path, str], ...]:
    """Resolve the exact stores the service uses, mirroring its own defaults."""
    layout = metnos_xdg_layout_v1(account.record)
    home = Path(environment.get("HOME") or layout.home)
    roots = {
        "data": Path(environment.get("METNOS_USER_DATA")
                     or home / ".local/share/metnos"),
        "state": Path(environment.get("METNOS_USER_STATE")
                      or home / ".local/state/metnos"),
    }
    resolved = []
    for kind, override, root, basename, table in _SELECTED_SOURCES_V1:
        raw = environment.get(override)
        path = Path(raw) if raw else roots[root] / basename
        if not path.is_absolute():
            raise LifecycleCutoverError("cutover_source_invalid", kind)
        resolved.append((kind, path, table))
    return tuple(resolved)


def service_epoch_db_v1(environment: dict[str, str]) -> Path:
    """Locate the service's epoch store, never the caller's own home.

    Root has its own state directory, so a root-run tool that resolves the path
    from its own environment looks in the wrong place and reports the migration
    missing. The overrides recorded when the migration ran are the ones that
    found it then.
    """
    account = resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1)
    layout = metnos_xdg_layout_v1(account.record)
    state = Path(environment.get("METNOS_USER_STATE") or (
        Path(environment["HOME"]) / ".local/state/metnos"
        if environment.get("HOME") else layout.state))
    return state / "birth" / "executor_epochs.sqlite"


def _drop_to_service(account: PosixAccountSnapshotV1) -> None:
    """Become the service account permanently, and prove it."""
    account.assert_unchanged(resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1))
    os.setgroups(list(account.supplementary_gids))
    os.setgid(account.record.gid)
    os.setuid(account.record.uid)
    if os.geteuid() != account.record.uid or os.getuid() != account.record.uid:
        raise LifecycleCutoverError("cutover_privilege_retained")
    for key, value in metnos_xdg_layout_v1(account.record).environment().items():
        os.environ.setdefault(key, value)


def _plan_as_service(report_fd: int) -> int:
    """Observe the running installation: which stores, and what they mean."""
    account = resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1)
    pid = _service_main_pid()
    environment = _service_environment(pid, account)
    sources = selected_sources_v1(environment, account)
    if _service_main_pid() != pid:
        raise LifecycleCutoverError("cutover_service_unavailable", "service changed")
    _drop_to_service(account)
    for key, value in environment.items():
        os.environ[key] = value
    _write_report(report_fd, _observe_installation(sources))
    return 0


def _apply_as_service(report_fd: int, handoff: dict) -> int:
    """Migrate with the stack already quiescent, against the planned stores."""
    account = resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1)
    _drop_to_service(account)
    for key, value in handoff["environment"].items():
        os.environ[key] = value
    sources = tuple((item["kind"], Path(item["path"]), item["legacy_table"])
                    for item in handoff["sources"])
    observed = _observe_installation(sources)
    if observed["migration_id"] != handoff["migration_id"]:
        # The stores or the catalog moved between planning and applying. The
        # plan is the reviewed decision; a different one is not a retry.
        raise LifecycleCutoverError("cutover_plan_stale", handoff["migration_id"])
    _write_report(report_fd, _migrate_as_service(sources, handoff))
    return 0


def _write_report(report_fd: int, report: dict) -> None:
    payload = json.dumps(report, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    if len(payload) > _MAX_REPORT_BYTES:
        raise LifecycleCutoverError("cutover_report_oversized")
    os.write(report_fd, payload)
    os.close(report_fd)


def _selectable_from_catalog() -> dict[str, tuple[str, str]]:
    """The exact generations a verified catalog load currently selects."""
    from loader import load_catalog

    selectable = {}
    for executor in load_catalog(verify=True, lang="en").executors.values():
        contract_id = getattr(executor, "contract_id", None)
        generation_id = getattr(executor, "generation_id", None)
        if isinstance(contract_id, str) and isinstance(generation_id, str):
            selectable[executor.name] = (contract_id, generation_id)
    return selectable


def _observe_installation(sources: tuple[tuple[str, Path, str], ...]) -> dict:
    """Census every selected store and decide, writing nothing."""
    from executor_birth_lifecycle_migration import (
        LifecycleMigrationError, plan_digest_v1, plan_migration,
    )

    selectable = _selectable_from_catalog()
    try:
        plan = plan_migration([(path, table) for _kind, path, table in sources],
                              selectable=selectable)
    except LifecycleMigrationError as exc:
        raise LifecycleCutoverError(exc.code, exc.detail) from exc
    return {
        "migration_id": plan.migration_id,
        "environment": {key: value for key, value in os.environ.items()
                        if key in _CARRIED_ENVIRONMENT_V1},
        "sources": [{
            "kind": kind, "path": str(source.identity.path),
            "legacy_table": source.legacy_table,
            "source_id": source.identity.source_id,
            "content_id": source.identity.content_id,
            "rows": len(source.rows),
            "plan_id": plan_digest_v1(source.dispositions),
            "pending": sum(1 for item in source.dispositions
                           if item.kind.value == "pending_disposition"),
            "restrictions": sum(1 for item in source.dispositions
                                if item.kind.value == "current_restriction"),
        } for (kind, _path, _table), source in zip(sources, plan.sources)],
        "selectable": {name: list(identity)
                       for name, identity in sorted(selectable.items())},
    }


def _migrate_as_service(
    sources: tuple[tuple[str, Path, str], ...], handoff: dict,
) -> dict:
    """Admit the selectable generations, then preserve, decide and restrict."""
    import config
    from executor_birth_lifecycle_migration import (
        LifecycleMigrationError, apply_migration, plan_migration,
    )
    from executor_lifecycle_state import admit_generations
    from loader import load_catalog

    epoch_db = Path(config.PATH_USER_STATE) / "birth" / "executor_epochs.sqlite"
    if not epoch_db.is_file():
        raise LifecycleCutoverError("cutover_epoch_store_absent", str(epoch_db))
    executors = tuple(load_catalog(verify=True, lang="en").executors.values())
    # The epoch store must already hold every selectable generation before a
    # restriction is applied, or a restricted executor would be visible again
    # between this cutover and the first ordinary load.
    admitted = admit_generations(executors, db_path=epoch_db)
    selectable = _selectable_from_catalog()
    if {name: list(identity) for name, identity in sorted(selectable.items())} != \
            handoff["selectable"]:
        raise LifecycleCutoverError("cutover_plan_stale", "catalog selection")
    try:
        plan = plan_migration([(path, table) for _kind, path, table in sources],
                              selectable=selectable)
        applied = apply_migration(plan, epoch_db_path=epoch_db,
                                  applied_at=_utc_now())
    except LifecycleMigrationError as exc:
        raise LifecycleCutoverError(exc.code, exc.detail) from exc
    return {
        "admitted_generations": admitted,
        "applied": applied,
        "retired_sources": _retire_sources(sources),
        "installation_scope": {
            "epoch_db": str(epoch_db),
            "selectable": len(selectable),
        },
    }


def _retire_sources(sources: tuple[tuple[str, Path, str], ...]) -> list[dict]:
    """Make each copied store unwritable, and say so rather than assume it.

    This refuses the ordinary writer that opens the file again. It cannot close
    a handle a running process already holds, which is why the quiescent stack
    is a precondition and not an optimisation.
    """
    retired = []
    for kind, path, _table in sources:
        try:
            os.chmod(path, 0o400)
            mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            retired.append({"kind": kind, "read_only": False,
                            "error": type(exc).__name__})
            continue
        retired.append({"kind": kind, "read_only": mode == 0o400,
                        "mode": oct(mode)})
    return retired


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _install_marker(installation_id: str, migration_id: str) -> Path:
    """Record the marker last, so an earlier interruption changes nothing."""
    from executor_birth_activation_mode import encode_migration_marker_v1

    encoded = encode_migration_marker_v1(
        installation_id=installation_id, migration_id=migration_id,
        completed_at=_utc_now(),
    )
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    with _provisioning_lock(DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True):
        directory = ACTIVATION_DIRECTORY_V1
        if not directory.exists():
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
            _sync_directory(DEFAULT_OWNERSHIP_ROOT_V1)
        _directory_metadata(directory, root_owned=True)
        path = directory / MARKER_BASENAME_V1
        if path.exists():
            from executor_birth_activation_mode import MIGRATION_MAX_BYTES_V1

            existing = _read_regular(path, maximum=MIGRATION_MAX_BYTES_V1,
                                     mode=0o644, root_owned=True)
            if existing != encoded:
                # A second cutover would change which decisions are
                # authoritative. Replacing the marker is a recovery operation
                # with its own evidence, not a retry.
                raise LifecycleCutoverError("cutover_marker_present")
            return path
        staged = directory / (MARKER_BASENAME_V1 + ".staged")
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(staged, 0o644)
        os.replace(staged, path)
        _sync_directory(directory)
        # Verify with the reader the runtime will use, not with a second
        # opinion: a marker this writer accepts and that reader refuses is
        # exactly the failure an installation cannot diagnose.
        from executor_birth_activation_mode import MIGRATION_MAX_BYTES_V1

        if _read_regular(path, maximum=MIGRATION_MAX_BYTES_V1, mode=0o644,
                         root_owned=True) != encoded:
            raise LifecycleCutoverError("cutover_marker_unsafe")
        return path


def _installation_id() -> str:
    from executor_birth_lifecycle import _installation_id_v1
    from executor_birth_ownership_authorities import (
        load_ownership_public_registries_v1,
    )

    return _installation_id_v1(load_ownership_public_registries_v1())


def _in_service_child(work) -> dict:
    """Run one half in a child that can drop privilege for good.

    Dropping is irreversible, so it cannot happen in the process that must
    still hold the maintenance barrier and write a root-owned marker. The child
    reports through a pipe; it returns no object and shares no handle.
    """
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - the real child, exercised natively
        os.close(read_fd)
        try:
            os._exit(work(write_fd))
        except BaseException as exc:
            os.write(write_fd, json.dumps({
                "error": getattr(exc, "code", type(exc).__name__),
                "detail": str(getattr(exc, "detail", "") or ""),
            }).encode("ascii", "replace"))
            os._exit(1)
    os.close(write_fd)
    chunks: list[bytes] = []
    total = 0
    while total <= _MAX_REPORT_BYTES:
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    os.close(read_fd)
    _pid, status = os.waitpid(child, 0)
    try:
        report = json.loads(b"".join(chunks).decode("ascii"))
        if type(report) is not dict:
            raise ValueError("report shape")
    except (UnicodeDecodeError, ValueError) as exc:
        raise LifecycleCutoverError("cutover_report_invalid") from exc
    if status != 0 or "error" in report:
        raise LifecycleCutoverError(
            str(report.get("error") or "cutover_child_failed"),
            str(report.get("detail") or ""),
        )
    return report


def _prove_productive_services_stopped_v1(service_user: str) -> tuple[dict, ...]:
    """Prove the services that write these stores are actually stopped.

    The maintenance barrier proves its own target list quiescent, and that list
    is the legacy bindings: the entry points the F4 transition retired. They are
    masked, so asking whether they are stopped always answers yes, and after
    that transition the services that really run carry the same names in system
    scope. Observing the retired user counterparts proves nothing about the
    worker and the daemon that write the statistics database on every call.

    This asks the installed catalog which units this product runs, and refuses
    when it cannot read them: a quiescence claim over an unknown topology is
    not a claim.
    """
    from executor_birth_maintenance_units import QUIESCENT_LOAD_STATES_V1
    from services_registry import owned_service_units_v1
    from stack_reconcile import Systemctl

    try:
        units = owned_service_units_v1()
    except Exception as exc:
        raise LifecycleCutoverError(
            "cutover_topology_unknown", "installed service catalog is unreadable",
        ) from exc
    if not units:
        raise LifecycleCutoverError("cutover_topology_unknown", "no declared unit")
    systemctl = Systemctl(service_user=service_user)
    observed = []
    for scope, unit in units:
        state = systemctl.show(unit, scope)
        load_state = str(state.get("LoadState") or "")
        active_state = str(state.get("ActiveState") or "")
        try:
            main_pid = int(state.get("MainPID") or 0)
        except (TypeError, ValueError):
            main_pid = -1
        if load_state not in QUIESCENT_LOAD_STATES_V1 or state.get("ManagerError"):
            raise LifecycleCutoverError(
                "cutover_topology_unknown", f"cannot inspect {scope} unit {unit}")
        if active_state not in {"inactive", "failed"} or main_pid != 0:
            raise LifecycleCutoverError(
                "cutover_writer_running",
                f"{scope} unit {unit} is {active_state or load_state}")
        observed.append({"scope": scope, "unit": unit, "load_state": load_state,
                         "active_state": active_state, "main_pid": main_pid})
    return tuple(observed)


def _require_root_v1() -> None:
    if not _managed_authority_platform_supported_v1():
        raise LifecycleCutoverError("cutover_platform_unsupported")
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise LifecycleCutoverError("cutover_root_required")


def _handoff_path() -> Path:
    return ACTIVATION_DIRECTORY_V1 / HANDOFF_BASENAME_V1


def plan_cutover_v1() -> dict:
    """Observe the running installation and record the decision for review.

    The plan is written while the services run because only a live process can
    say which stores this installation selected. It changes nothing.
    """
    _require_root_v1()
    observed = _in_service_child(_plan_as_service)
    document = {
        "schema_version": 1, "purpose": HANDOFF_PURPOSE_V1,
        "service_user": SERVICE_ACCOUNT_NAME_V1, "planned_at": _utc_now(),
        **observed,
    }
    _write_handoff(document)
    return document


def _write_handoff(document: dict) -> Path:
    """Stage the reviewed decision under the same root custody as the marker."""
    encoded = json.dumps(document, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    with _provisioning_lock(DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True):
        directory = ACTIVATION_DIRECTORY_V1
        if not directory.exists():
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
            _sync_directory(DEFAULT_OWNERSHIP_ROOT_V1)
        _directory_metadata(directory, root_owned=True)
        path = _handoff_path()
        staged = directory / (HANDOFF_BASENAME_V1 + ".staged")
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(staged, 0o644)
        os.replace(staged, path)
        _sync_directory(directory)
        if _read_regular(path, maximum=_MAX_REPORT_BYTES, mode=0o644,
                         root_owned=True) != encoded:
            raise LifecycleCutoverError("cutover_handoff_unsafe")
        return path


def read_handoff_v1() -> dict:
    """Read the reviewed plan, refusing anything that is not exactly one."""
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    _directory_metadata(ACTIVATION_DIRECTORY_V1, root_owned=True)
    encoded = _read_regular(_handoff_path(), maximum=_MAX_REPORT_BYTES,
                            mode=0o644, root_owned=True)
    try:
        document = json.loads(encoded.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LifecycleCutoverError("cutover_handoff_invalid", "document") from exc
    required = {"schema_version", "purpose", "service_user", "planned_at",
                "migration_id", "environment", "sources", "selectable"}
    if (type(document) is not dict or set(document) != required
            or document["schema_version"] != 1
            or document["purpose"] != HANDOFF_PURPOSE_V1
            or document["service_user"] != SERVICE_ACCOUNT_NAME_V1
            or not isinstance(document["sources"], list)
            or not document["sources"]
            or not isinstance(document["selectable"], dict)
            or not isinstance(document["environment"], dict)):
        raise LifecycleCutoverError("cutover_handoff_invalid", "schema")
    return document


def apply_cutover_v1() -> dict:
    """Migrate with the stack quiescent, then record the marker.

    The barrier is not an optimisation. Every legacy writer lives in the
    services it stops, and one call during the copy would write a row nobody
    preserves.
    """
    _require_root_v1()
    handoff = read_handoff_v1()
    from contract_cutover_guard import _contract_cutover_guard_for_service_user_v1

    with _contract_cutover_guard_for_service_user_v1(handoff["service_user"]):
        # The barrier holds lifecycle exclusion and proves the retired entry
        # points idle. It does not prove the current ones idle, so that is
        # asked here, of the installed catalog, before anything is copied.
        stopped = _prove_productive_services_stopped_v1(handoff["service_user"])
        report = _in_service_child(
            lambda descriptor: _apply_as_service(descriptor, handoff))
        applied = report["applied"]
        if applied.get("pending"):
            # An open case must receive a disposition before the marker makes
            # the epoch store authoritative; otherwise retirement loses it.
            raise LifecycleCutoverError(
                "cutover_pending_disposition", str(applied["pending"]))
        if applied["migration_id"] != handoff["migration_id"]:
            raise LifecycleCutoverError("cutover_plan_stale", "applied migration")
        report["stopped_services"] = list(stopped)
        report["marker"] = str(_install_marker(
            _installation_id(), applied["migration_id"]))
    return report


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments not in (["plan"], ["apply"]):
        print("usage: birth_lifecycle_migration.py plan|apply", file=sys.stderr)
        return 64
    try:
        report = (apply_cutover_v1() if arguments == ["apply"]
                  else plan_cutover_v1())
    except (LifecycleCutoverError, OwnershipAuthorityError) as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - administrative entry point
    raise SystemExit(main())
