"""Authoritative maintenance barrier for irreversible contract cutover.

The barrier is deliberately independent from installer UI and contract-store
filesystem code.  Every caller holds the central lifecycle lock, proves all
known ingress/publisher units stopped, and retains that lock through its first
verified store-only catalog load.
"""
from __future__ import annotations

from contextlib import contextmanager
import sys


class ContractCutoverGuardError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


_QUIESCENT_STATES = frozenset({"inactive", "failed"})


def prove_stack_stopped(reconciler) -> dict:
    """Prove that every catalog consumer/writer and browser ingress is idle."""
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1

    observations: list[dict[str, object]] = []
    for scope, unit in MAINTENANCE_TARGETS_V1:
        state = reconciler.systemctl.show(unit, scope)
        load_state = str(state.get("LoadState") or "")
        active_state = str(state.get("ActiveState") or "")
        try:
            main_pid = int(state.get("MainPID") or 0)
        except (TypeError, ValueError):
            main_pid = -1
        if load_state != "loaded" or state.get("ManagerError"):
            raise ContractCutoverGuardError(
                "quiescence_unknown", f"cannot inspect {scope} unit {unit}",
            )
        if active_state not in _QUIESCENT_STATES or main_pid != 0:
            raise ContractCutoverGuardError(
                "cutover_blocked",
                f"{scope} unit {unit} is {active_state or load_state}",
            )
        observations.append({
            "scope": scope,
            "unit": unit,
            "load_state": load_state,
            "active_state": active_state,
            "main_pid": main_pid,
        })
    try:
        browser = reconciler.require_quiescent()
    except Exception as exc:
        raise ContractCutoverGuardError("cutover_blocked", str(exc)) from exc
    accepted = {
        "inactive_http_and_inactive_sidecar",
        "inactive_http_and_sidecar_broker",
    }
    if not isinstance(browser, dict) or browser.get("source") not in accepted:
        raise ContractCutoverGuardError(
            "cutover_blocked",
            "HTTP ingress is reachable or browser work is not quiescent",
        )
    return {"source": browser["source"], "units": observations}


class _MaintenanceProofV1:
    """Preserve the legacy boolean guard and expose fresh canonical evidence."""

    __slots__ = ("_reconciler",)

    def __init__(self, reconciler) -> None:
        self._reconciler = reconciler

    def observe(self) -> dict:
        return prove_stack_stopped(self._reconciler)

    def __call__(self) -> bool:
        self.observe()
        return True


@contextmanager
def contract_cutover_guard():
    """Hold lifecycle exclusion while a stopped-stack proof remains valid."""
    if sys.platform != "linux":
        raise ContractCutoverGuardError(
            "cutover_platform_unsupported",
            "the managed Metnos server and its cutover require Linux/systemd",
        )
    from stack_reconcile import StackReconciler, catalog_reconcile_lock

    # Fixed order shared with publishers: global catalog admission first,
    # lifecycle/service exclusion second. This waits for an in-flight commit
    # to finish before services are stopped and prevents every later authoring
    # or publication write until the first store-only load has succeeded.
    guard = catalog_reconcile_lock(wait_s=2)
    try:
        guard.__enter__()
    except Exception as exc:
        raise ContractCutoverGuardError(
            "cutover_lock_unavailable", str(exc),
        ) from exc
    try:
        reconciler = StackReconciler(default_write_report=False)
        proof = _MaintenanceProofV1(reconciler)
        evidence = proof.observe()
        yield proof, evidence
    finally:
        guard.__exit__(None, None, None)


def _verify_store_only_catalog_locked() -> dict[str, int]:
    """Authenticate all bindings and perform the first cold loader pass."""
    from contract_store import ContractRetirement, current_contract
    from loader import invalidate_catalog_cache, load_catalog
    from manifest_inventory import ManifestStatus, inventory_manifests
    from sign import list_trusted_publics

    structural = inventory_manifests()
    if structural.problems:
        detail = "; ".join(
            f"{problem.code}:{problem.path}"
            for problem in structural.problems[:12]
        )
        raise ContractCutoverGuardError("store_inventory_invalid", detail)
    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ContractCutoverGuardError("trusted_keys_missing")
    expected: dict[str, tuple[str, str]] = {}
    retired = 0
    for ref in structural.manifests:
        revision = current_contract(ref, trusted_publics=trusted)
        if isinstance(revision, ContractRetirement):
            retired += 1
            continue
        if ref.status is not ManifestStatus.ADMITTED:
            continue
        name = revision.parsed.get("name")
        if not isinstance(name, str) or not name or name in expected:
            raise ContractCutoverGuardError(
                "store_name_invalid", str(ref.contract_id),
            )
        expected[name] = (
            ref.contract_id.storage_key,
            str(revision.generation_id),
        )
    invalidate_catalog_cache()
    catalog = load_catalog(verify=True)
    fatal = [
        (path, reason)
        for path, reason in catalog.rejected
        if not reason.startswith("archived by executor_aging")
        and not reason.startswith("contract_retired:")
    ]
    if fatal:
        raise ContractCutoverGuardError(
            "store_catalog_rejected",
            "; ".join(f"{path}:{reason}" for path, reason in fatal[:12]),
        )
    missing = []
    for name, (storage_key, generation) in expected.items():
        executor = catalog.get(name)
        if executor is not None and executor.generation_id == generation:
            continue
        intentionally_archived = any(
            reason.startswith("archived by executor_aging")
            and storage_key in path and generation in path
            for path, reason in catalog.rejected
        )
        if not intentionally_archived:
            missing.append(name)
    if missing:
        raise ContractCutoverGuardError(
            "store_catalog_incomplete", ", ".join(sorted(missing)[:20]),
        )
    return {
        "bindings": len(structural.manifests),
        "loaded": len(expected) - sum(
            1 for name in expected if catalog.get(name) is None
        ),
        "retired": retired,
    }


def verify_store_only_catalog() -> dict[str, int]:
    """Verify one stable store snapshot, independently of its caller."""
    from contract_store import catalog_admission_lock

    with catalog_admission_lock():
        return _verify_store_only_catalog_locked()


__all__ = [
    "ContractCutoverGuardError",
    "contract_cutover_guard",
    "prove_stack_stopped",
    "verify_store_only_catalog",
]
