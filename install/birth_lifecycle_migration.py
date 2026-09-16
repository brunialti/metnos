"""Administrative one-time cutover to the epoch lifecycle owner.

One process cannot both write the service-owned stores and the root-owned
marker, so the cutover is two stages in one command. A child resolves the
selected sources from the running service, permanently drops to the service
account before any database access, and reports what it preserved and decided.
The root parent validates that report and only then records the marker that
makes the epoch store authoritative.

The order is deliberate. The marker is the last thing written, so an
interruption anywhere before it leaves an installation that still uses its
name-based state and can simply be run again. Nothing here issues a
certificate, publishes an executor, or retires a file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
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
SERVICE_UNIT_V1 = "metnos-http.service"
# Filesystem seam for isolated tests; the productive value is fixed.
_PROC_ROOT_V1 = Path("/proc")
_MAX_REPORT_BYTES = 1 << 20
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


def _child_migration(argv: list[str]) -> int:
    """Run as the service account only: read the stores, preserve and decide."""
    report_fd = int(argv[1])
    account = resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1)
    pid = _service_main_pid()
    environment = _service_environment(pid, account)
    sources = selected_sources_v1(environment, account)
    if _service_main_pid() != pid:
        raise LifecycleCutoverError("cutover_service_unavailable", "service changed")
    account.assert_unchanged(resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1))
    os.setgroups(list(account.supplementary_gids))
    os.setgid(account.record.gid)
    os.setuid(account.record.uid)
    if os.geteuid() != account.record.uid or os.getuid() != account.record.uid:
        raise LifecycleCutoverError("cutover_privilege_retained")
    for key, value in metnos_xdg_layout_v1(account.record).environment().items():
        os.environ[key] = environment.get(key, value)
    report = _migrate_as_service(sources)
    payload = json.dumps(report, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    if len(payload) > _MAX_REPORT_BYTES:
        raise LifecycleCutoverError("cutover_report_oversized")
    os.write(report_fd, payload)
    os.close(report_fd)
    return 0


def _migrate_as_service(sources: tuple[tuple[str, Path, str], ...]) -> dict:
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
    catalog = load_catalog(verify=True, lang="en")
    executors = tuple(catalog.executors.values())
    # The epoch store must already hold every selectable generation before a
    # restriction is applied, or a restricted executor would be visible again
    # between this cutover and the first ordinary load.
    admitted = admit_generations(executors, db_path=epoch_db)
    selectable = {}
    for executor in executors:
        contract_id = getattr(executor, "contract_id", None)
        generation_id = getattr(executor, "generation_id", None)
        if isinstance(contract_id, str) and isinstance(generation_id, str):
            selectable[executor.name] = (contract_id, generation_id)
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
        "installation_scope": {
            "epoch_db": str(epoch_db),
            "selectable": len(selectable),
        },
    }


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


def _run_child_migration() -> dict:
    """Run the privileged half in a child that can drop privilege for good.

    Dropping is irreversible, so it cannot happen in the process that must
    still write a root-owned marker. The child reports through a pipe; it
    returns no object and shares no handle.
    """
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - the real child, exercised natively
        os.close(read_fd)
        try:
            os._exit(_child_migration([sys.argv[0], str(write_fd)]))
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
    if status != 0 or "applied" not in report:
        raise LifecycleCutoverError(
            str(report.get("error") or "cutover_child_failed"),
            str(report.get("detail") or ""),
        )
    return report


def run_cutover_v1(*, apply: bool) -> dict:
    """Perform the cutover, or report exactly what it would do."""
    if not _managed_authority_platform_supported_v1():
        raise LifecycleCutoverError("cutover_platform_unsupported")
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise LifecycleCutoverError("cutover_root_required")
    report = _run_child_migration()
    applied = report["applied"]
    if applied.get("pending"):
        # An open case must receive a disposition before the marker makes the
        # epoch store authoritative; otherwise retirement loses it in silence.
        raise LifecycleCutoverError(
            "cutover_pending_disposition", str(applied["pending"]))
    if not apply:
        report["marker"] = None
        return report
    report["marker"] = str(_install_marker(
        _installation_id(), applied["migration_id"]))
    return report


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments not in (["plan"], ["apply"]):
        print("usage: birth_lifecycle_migration.py plan|apply", file=sys.stderr)
        return 64
    try:
        report = run_cutover_v1(apply=arguments == ["apply"])
    except (LifecycleCutoverError, OwnershipAuthorityError) as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - administrative entry point
    raise SystemExit(main())
