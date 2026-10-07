"""Exclusive F6 maintenance over the existing installed owners (Linux only).

No service is stopped here and no caller chooses a root, account or lock.
The installed administrative operation enters this boundary only after the
authorized stop. The recovery path takes the same locks even when active.json
blocks every ordinary start and administrative operation.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from typing import Callable
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

from executor_birth_retention import RetentionError



@dataclass(frozen=True, slots=True)
class _RetentionExclusionContextV1:
    """Borrowed native capabilities; validity is the live administrative checker.

    Readonly fields expose the already held session and resolved installation,
    never an instruction to reopen locks or resolve a caller-selected account.
    Calling this object preserves the previous callback contract.
    """
    session: object
    account: object
    layout: object
    frontier: tuple[str, str]
    _checker: Callable[[], None] = field(repr=False, compare=False)

    def require_exclusion(self):
        self._checker()

    def __call__(self):
        self.require_exclusion()

def _require_empty_units_v1(service_user: str) -> tuple[dict, ...]:
    from install.birth_lifecycle_migration import _prove_productive_services_stopped_v1
    from stack_reconcile import Systemctl

    observed = _prove_productive_services_stopped_v1(service_user)
    systemctl = Systemctl(service_user=service_user)
    for unit in observed:
        # Timers have no processes. The native observer already proves their
        # inactive state, and proves absent user managers have no cgroup.
        if not unit["unit"].endswith(".service") or unit["load_state"] == "manager-absent":
            continue
        result = systemctl.run(unit["scope"], "show", unit["unit"],
                               "--property=Id,ControlPID,ControlGroup", timeout_s=10)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if (result.returncode or values != {
            "Id": unit["unit"], "ControlPID": "0", "ControlGroup": "",
        }):
            raise RetentionError("retention_writer_running", "service control process or cgroup")
    return observed


def _require_authority_lock_v1(descriptor: int, path: Path, owner: tuple[int, int]) -> None:
    """Recheck the already held authority lock; never silently reopen it."""
    before = os.fstat(descriptor)
    current = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or (before.st_uid, before.st_gid) != owner
            or (before.st_dev, before.st_ino, before.st_mode, before.st_nlink)
               != (current.st_dev, current.st_ino, current.st_mode, current.st_nlink)):
        raise RetentionError("retention_exclusion_lost", "authority lock changed")


@contextmanager
def _birth_exclusion_v1(account):
    """Open the existing secure Birth owner as its account; retain its lock.

    Root custody is restored before yielding so the root-owned F6 journal is
    never exposed to the service. The identity owner refuses multithreaded use.
    """
    from install.birth_authority_provisioner import _service_owned_birth_identity_v2
    from install.birth_authority_provisioning import open_birth_provisioning_layout_v1

    identity = SimpleNamespace(service_uid=account.record.uid,
                               service_gid=account.record.gid,
                               service_supplementary_gids=account.supplementary_gids)
    resources = ExitStack()
    try:
        with _service_owned_birth_identity_v2(identity):
            try:
                session = resources.enter_context(open_birth_provisioning_layout_v1().birth_session)
                resources.enter_context(session.global_lock(exclusive=True, create=False))
            except BaseException:
                resources.close()
                raise
        yield session
    finally:
        with _service_owned_birth_identity_v2(identity):
            resources.close()


@contextmanager
def administrative_retention_exclusion_v1():
    """Yield a live checker under all native locks, with no public test bypass."""
    if not sys.platform.startswith("linux") or os.geteuid() != 0 or os.getegid() != 0:
        raise RetentionError("retention_administrator_required")
    from config import PATH_ROOT, PATH_USER_CONFIG, PATH_USER_STATE, PATH_USER_DATA
    from contract_cutover_guard import (
        _contract_cutover_guard_for_service_user_v1, _require_maintenance_session_v1,
    )
    from executor_birth_account_identity import (
        metnos_xdg_layout_v1, resolve_posix_account_snapshot_v1,
    )
    from executor_birth_authority_files import DEFAULT_OWNERSHIP_ROOT_V1
    from executor_birth_host_path_policy import SERVICE_ACCOUNT_NAME_V1
    from executor_birth_ownership_chain import OwnershipChainStore
    from executor_birth_ownership_coordinator import (
        _deployment_exclusion_v1, _require_deployment_lock_session_v1,
    )
    from executor_birth_startup_gate import (
        _exclusive_startup_gate_v1, _require_exclusive_startup_gate_session_v1,
    )
    from install.birth_ownership_authority_provisioner import (
        _LOCK_BASENAME_V1, _provisioning_exclusion_v1,
    )

    process = os.getpid()
    active = True
    account = resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1)
    layout = metnos_xdg_layout_v1(account.record)
    if (account.record.uid <= 0 or account.record.gid <= 0
            or Path(PATH_USER_CONFIG) != layout.config or Path(PATH_USER_STATE) != layout.state
            or Path(PATH_USER_DATA) != layout.data):
        raise RetentionError("retention_service_identity_invalid")
    try:
        with ExitStack() as stack:
            deployment = stack.enter_context(_deployment_exclusion_v1())
            startup = stack.enter_context(_exclusive_startup_gate_v1())
            authority = stack.enter_context(_provisioning_exclusion_v1(
                DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True))
            birth = stack.enter_context(_birth_exclusion_v1(account))
            maintenance, _evidence = stack.enter_context(_contract_cutover_guard_for_service_user_v1(
                account.record.name, catalog_trusted_owner=(account.record.uid, account.record.gid)))
            def installation_frontier():
                window = OwnershipChainStore().read_required_window_v1()
                if Path(window.required_distribution.installation_root) != Path(PATH_ROOT):
                    raise RetentionError("retention_installation_changed")
                return (window.required_head.head_id,
                        window.required_distribution.identity.closed_build_id)

            frontier = installation_frontier()

            def require_exclusion():
                if not active or process != os.getpid() or os.geteuid() != 0 or os.getegid() != 0:
                    raise RetentionError("retention_exclusion_lost")
                account.assert_unchanged(resolve_posix_account_snapshot_v1(account.record.name))
                _require_deployment_lock_session_v1(deployment)
                _require_exclusive_startup_gate_session_v1(startup)
                _require_authority_lock_v1(authority, DEFAULT_OWNERSHIP_ROOT_V1 / _LOCK_BASENAME_V1, (0, 0))
                birth._require_exclusive_global_lock()
                _require_maintenance_session_v1(maintenance)
                if installation_frontier() != frontier:
                    raise RetentionError("retention_installation_changed")
                _require_empty_units_v1(account.record.name)

            require_exclusion()
            yield _RetentionExclusionContextV1(
                session=birth, account=account, layout=layout, frontier=frontier,
                _checker=require_exclusion)
    finally:
        active = False
