"""Authoritative maintenance barrier for irreversible contract cutover.

The barrier is deliberately independent from installer UI and contract-store
filesystem code.  Every caller holds the central lifecycle lock, proves all
known ingress/publisher units stopped, and retains that lock through its first
verified store-only catalog load.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import sys
import threading


class ContractCutoverGuardError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


_QUIESCENT_STATES = frozenset({"inactive", "failed"})
_TRANSITION_LOAD_STATES_V1 = frozenset({"loaded", "masked"})
_MAINTENANCE_SESSION_SEAL_V1 = object()
_MAINTENANCE_SESSION_GUARD_V1 = threading.Lock()
_ACTIVE_MAINTENANCE_SESSIONS_V1: dict[object, object] = {}


def _prove_stack_stopped_v1(reconciler, *, load_states: frozenset[str]) -> dict:
    """Prove every catalog consumer and browser ingress remains idle."""
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
        if load_state not in load_states or state.get("ManagerError"):
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


def prove_stack_stopped(reconciler) -> dict:
    """Prove the complete pre-transition unit catalog is loaded and idle."""
    return _prove_stack_stopped_v1(
        reconciler, load_states=frozenset({"loaded"}),
    )


def _prove_transition_stack_stopped_v1(reconciler) -> dict:
    """Accept only named quiescent load states while topology is replaced."""
    return _prove_stack_stopped_v1(
        reconciler, load_states=_TRANSITION_LOAD_STATES_V1,
    )


class _MaintenanceProofV1:
    """Preserve the legacy boolean guard and expose fresh canonical evidence."""

    __slots__ = (
        "_reconciler", "_token", "_owner_process", "_active", "_seal",
        "_transition_evidence",
    )

    def __init__(self, reconciler, token: object, seal: object) -> None:
        if seal is not _MAINTENANCE_SESSION_SEAL_V1:
            raise ContractCutoverGuardError("cutover_session_invalid")
        self._reconciler = reconciler
        self._token = token
        self._owner_process = os.getpid()
        self._active = True
        self._seal = seal
        self._transition_evidence = None

    def __copy__(self):
        raise TypeError("maintenance sessions cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("maintenance sessions cannot be copied")

    def __reduce__(self):
        raise TypeError("maintenance sessions cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("maintenance sessions cannot be serialized")

    def observe(self) -> dict:
        if self._transition_evidence is not None:
            return _prove_transition_stack_stopped_v1(self._reconciler)
        return prove_stack_stopped(self._reconciler)

    def __call__(self) -> bool:
        self.observe()
        return True


def _require_maintenance_session_v1(session: object) -> None:
    """Require the exact live proof yielded while lifecycle exclusion is held."""
    if type(session) is not _MaintenanceProofV1:
        raise ContractCutoverGuardError("cutover_session_invalid")
    with _MAINTENANCE_SESSION_GUARD_V1:
        registered = _ACTIVE_MAINTENANCE_SESSIONS_V1.get(session._token)
    if (
        session._seal is not _MAINTENANCE_SESSION_SEAL_V1
        or registered is not session
        or not session._active
        or session._owner_process != os.getpid()
    ):
        raise ContractCutoverGuardError("cutover_session_invalid")
    session.observe()


def _begin_topology_transition_v1(
    session: object, expected_evidence: bytes,
) -> None:
    """Keep the same live lock while admitted unit load states change."""
    from executor_birth_ownership_preflight import canonical_maintenance_proof

    if (
        type(session) is not _MaintenanceProofV1
        or type(expected_evidence) is not bytes
    ):
        raise ContractCutoverGuardError("cutover_session_invalid")
    _require_maintenance_session_v1(session)
    observed = session.observe()
    current = canonical_maintenance_proof(
        source=observed["source"], units=observed["units"],
    )
    if current != expected_evidence:
        raise ContractCutoverGuardError("cutover_session_invalid")
    session._transition_evidence = expected_evidence
    _require_maintenance_session_v1(session)


def _maintenance_evidence_under_transition_v1(session: object) -> bytes:
    """Return the initial proof only after fresh transition quiescence."""
    from executor_birth_ownership_preflight import canonical_maintenance_proof

    _require_maintenance_session_v1(session)
    if type(session) is not _MaintenanceProofV1:
        raise ContractCutoverGuardError("cutover_session_invalid")
    if session._transition_evidence is not None:
        return session._transition_evidence
    observed = session.observe()
    return canonical_maintenance_proof(
        source=observed["source"], units=observed["units"],
    )


@contextmanager
def _contract_cutover_guard_core_v1(reconciler):
    """Hold lifecycle exclusion for one already bound service observer."""
    if sys.platform != "linux":
        raise ContractCutoverGuardError(
            "cutover_platform_unsupported",
            "the managed Metnos server and its cutover require Linux/systemd",
        )
    from stack_reconcile import catalog_reconcile_lock

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
        token = object()
        proof = _MaintenanceProofV1(
            reconciler, token, _MAINTENANCE_SESSION_SEAL_V1,
        )
        with _MAINTENANCE_SESSION_GUARD_V1:
            _ACTIVE_MAINTENANCE_SESSIONS_V1[token] = proof
        try:
            evidence = proof.observe()
            yield proof, evidence
        finally:
            with _MAINTENANCE_SESSION_GUARD_V1:
                proof._active = False
                _ACTIVE_MAINTENANCE_SESSIONS_V1.pop(token, None)
    finally:
        guard.__exit__(None, None, None)


@contextmanager
def contract_cutover_guard():
    """Hold lifecycle exclusion using the process's ordinary service identity."""
    from stack_reconcile import StackReconciler

    with _contract_cutover_guard_core_v1(
        StackReconciler(default_write_report=False),
    ) as boundary:
        yield boundary


@contextmanager
def _contract_cutover_guard_for_service_user_v1(service_user: str):
    """Bind user-scope observations to the verified deployment account."""
    if (
        type(service_user) is not str or not service_user
        or service_user != service_user.strip()
        or any(character in service_user for character in "\x00\r\n")
    ):
        raise ContractCutoverGuardError("service_user_invalid")
    from stack_reconcile import StackReconciler, Systemctl

    systemctl = Systemctl(service_user=service_user)
    try:
        systemctl._service_uid()
    except Exception as exc:
        raise ContractCutoverGuardError("service_user_invalid") from exc
    reconciler = StackReconciler(
        systemctl=systemctl, default_write_report=False,
    )
    with _contract_cutover_guard_core_v1(reconciler) as boundary:
        yield boundary


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
