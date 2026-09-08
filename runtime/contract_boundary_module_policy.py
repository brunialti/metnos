"""Immutable module aliases and source-owner boundary facts."""
from __future__ import annotations

from dataclasses import dataclass

from contract_boundary_policy_types import (
    closed_names_v1 as _closed_names_v1,
    require_text_v1 as _require_text_v1,
)


@dataclass(frozen=True, slots=True)
class BoundaryModuleOwnerV1:
    owner: str
    module_names: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text_v1(self.owner, field="module_owner")
        _closed_names_v1(self.module_names, field="module_names")


@dataclass(frozen=True, slots=True)
class BoundarySourceOwnerV1:
    path: str
    owner: str

    def __post_init__(self) -> None:
        _require_text_v1(self.path, field="source_path")
        _require_text_v1(self.owner, field="source_owner")


BOUNDARY_MODULE_OWNERS_V1 = (
    BoundaryModuleOwnerV1('executor_birth', ('executor_birth', 'runtime.executor_birth')),
    BoundaryModuleOwnerV1('executor_birth_intent', ('executor_birth_intent', 'runtime.executor_birth_intent')),
    BoundaryModuleOwnerV1('executor_birth_operational', ('executor_birth_operational', 'runtime.executor_birth_operational')),
    BoundaryModuleOwnerV1('executor_birth_synth', ('executor_birth_synth', 'runtime.executor_birth_synth')),
    BoundaryModuleOwnerV1('contract_store', ('contract_store', 'runtime.contract_store')),
    BoundaryModuleOwnerV1('sign', ('runtime.sign', 'sign')),
    BoundaryModuleOwnerV1('loader', ('loader', 'runtime.loader')),
    BoundaryModuleOwnerV1('invocations', ('invocations', 'runtime.invocations')),
    BoundaryModuleOwnerV1('i18n_migrate_manifests', ('admin.i18n_migrate_manifests', 'runtime.admin.i18n_migrate_manifests')),
    BoundaryModuleOwnerV1('contract_cutover_guard', ('contract_cutover_guard', 'runtime.contract_cutover_guard')),
    BoundaryModuleOwnerV1('manifest_inventory', ('manifest_inventory', 'runtime.manifest_inventory')),
    BoundaryModuleOwnerV1('executor_birth_authoring', ('executor_birth_authoring', 'runtime.executor_birth_authoring')),
    BoundaryModuleOwnerV1('executor_birth_ownership_chain', ('executor_birth_ownership_chain', 'runtime.executor_birth_ownership_chain')),
    BoundaryModuleOwnerV1('executor_birth_ownership_cutover', ('executor_birth_ownership_cutover', 'runtime.executor_birth_ownership_cutover')),
    BoundaryModuleOwnerV1('executor_birth_ownership_coordinator', ('executor_birth_ownership_coordinator', 'runtime.executor_birth_ownership_coordinator')),
    BoundaryModuleOwnerV1('executor_birth_transition_authority', (
        'executor_birth_transition_chain_policy',
        'executor_birth_transition_gate',
        'executor_birth_transition_receipts',
        'runtime.executor_birth_transition_chain_policy',
        'runtime.executor_birth_transition_gate',
        'runtime.executor_birth_transition_receipts',
    )),
    BoundaryModuleOwnerV1('birth_ownership_authority_provisioner', ('install.birth_ownership_authority_provisioner',)),
    BoundaryModuleOwnerV1('executor_birth_source_receiver', ('install.executor_birth_source_receiver',)),
    BoundaryModuleOwnerV1('executor_birth_contract_convergence', ('install.executor_birth_contract_convergence',)),
    BoundaryModuleOwnerV1('executor_birth_transition', ('install.executor_birth_transition',)),
    BoundaryModuleOwnerV1('executor_birth_host_provisioning', (
        'install.executor_birth_host_capability',
        'install.executor_birth_host_journal_posix',
        'install.executor_birth_host_posix',
        'install.executor_birth_host_provisioning',
    )),
    BoundaryModuleOwnerV1('executor_birth_posix_foundation', (
        'install.executor_birth_append_journal_posix',
        'install.executor_birth_posix_directory',
    )),
    BoundaryModuleOwnerV1('executor_birth_legacy_state_adoption', (
        'install.executor_birth_legacy_state_adoption',
        'install.executor_birth_legacy_state_effect_posix',
        'install.executor_birth_legacy_state_inspection',
        'install.executor_birth_legacy_state_journal_posix',
        'install.executor_birth_legacy_state_posix',
    )),
    BoundaryModuleOwnerV1('executor_birth_systemd', ('install.executor_birth_systemd',)),
    BoundaryModuleOwnerV1('executor_birth_admin_preflight', ('executor_birth_admin_preflight', 'runtime.executor_birth_admin_preflight')),
    BoundaryModuleOwnerV1('executor_birth_preflight_attestation_store', ('executor_birth_preflight_attestation_store', 'runtime.executor_birth_preflight_attestation_store')),
    BoundaryModuleOwnerV1('executor_birth_preflight_store_authority', ('executor_birth_preflight_store_authority', 'runtime.executor_birth_preflight_store_authority')),
)

BOUNDARY_SOURCE_OWNERS_V1 = (
    BoundarySourceOwnerV1('runtime/executor_birth.py', 'executor_birth'),
    BoundarySourceOwnerV1('runtime/executor_birth_intent.py', 'executor_birth_intent'),
    BoundarySourceOwnerV1('runtime/executor_birth_operational.py', 'executor_birth_operational'),
    BoundarySourceOwnerV1('runtime/contract_store.py', 'contract_store'),
    BoundarySourceOwnerV1('runtime/sign.py', 'sign'),
    BoundarySourceOwnerV1('runtime/loader.py', 'loader'),
    BoundarySourceOwnerV1('runtime/invocations.py', 'invocations'),
    BoundarySourceOwnerV1('runtime/admin/i18n_migrate_manifests.py', 'i18n_migrate_manifests'),
    BoundarySourceOwnerV1('runtime/contract_cutover_guard.py', 'contract_cutover_guard'),
    BoundarySourceOwnerV1('runtime/manifest_inventory.py', 'manifest_inventory'),
    BoundarySourceOwnerV1('runtime/executor_birth_authoring.py', 'executor_birth_authoring'),
    BoundarySourceOwnerV1('runtime/executor_birth_ownership_chain.py', 'executor_birth_ownership_chain'),
    BoundarySourceOwnerV1('runtime/executor_birth_ownership_coordinator.py', 'executor_birth_ownership_coordinator'),
    BoundarySourceOwnerV1('runtime/executor_birth_transition_chain_policy.py', 'executor_birth_transition_authority'),
    BoundarySourceOwnerV1('runtime/executor_birth_transition_gate.py', 'executor_birth_transition_authority'),
    BoundarySourceOwnerV1('runtime/executor_birth_transition_receipts.py', 'executor_birth_transition_authority'),
    BoundarySourceOwnerV1('install/birth_ownership_authority_provisioner.py', 'birth_ownership_authority_provisioner'),
    BoundarySourceOwnerV1('install/executor_birth_source_receiver.py', 'executor_birth_source_receiver'),
    BoundarySourceOwnerV1('install/executor_birth_contract_convergence.py', 'executor_birth_contract_convergence'),
    BoundarySourceOwnerV1('install/executor_birth_transition.py', 'executor_birth_transition'),
    BoundarySourceOwnerV1('install/executor_birth_host_capability.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_host_journal_posix.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_host_posix.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_host_provisioning.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_append_journal_posix.py', 'executor_birth_posix_foundation'),
    BoundarySourceOwnerV1('install/executor_birth_posix_directory.py', 'executor_birth_posix_foundation'),
    BoundarySourceOwnerV1('install/executor_birth_legacy_state_adoption.py', 'executor_birth_legacy_state_adoption'),
    BoundarySourceOwnerV1('install/executor_birth_legacy_state_effect_posix.py', 'executor_birth_legacy_state_adoption'),
    BoundarySourceOwnerV1('install/executor_birth_legacy_state_inspection.py', 'executor_birth_legacy_state_adoption'),
    BoundarySourceOwnerV1('install/executor_birth_legacy_state_journal_posix.py', 'executor_birth_legacy_state_adoption'),
    BoundarySourceOwnerV1('install/executor_birth_legacy_state_posix.py', 'executor_birth_legacy_state_adoption'),
    BoundarySourceOwnerV1('install/executor_birth_systemd.py', 'executor_birth_systemd'),
    BoundarySourceOwnerV1('runtime/executor_birth_admin_preflight.py', 'executor_birth_admin_preflight'),
    BoundarySourceOwnerV1('runtime/executor_birth_preflight_attestation_store.py', 'executor_birth_preflight_attestation_store'),
    BoundarySourceOwnerV1('runtime/executor_birth_preflight_store_authority.py', 'executor_birth_preflight_store_authority'),
)


__all__ = [
    "BOUNDARY_MODULE_OWNERS_V1", "BOUNDARY_SOURCE_OWNERS_V1",
    "BoundaryModuleOwnerV1", "BoundarySourceOwnerV1",
]
