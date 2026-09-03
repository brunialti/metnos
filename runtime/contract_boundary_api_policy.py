"""Immutable API, module and source-owner boundary facts."""
from __future__ import annotations

from dataclasses import dataclass

from contract_boundary_policy_types import (
    ContractBoundaryPolicyError,
    closed_names_v1 as _closed_names_v1,
    require_text_v1 as _require_text_v1,
)


@dataclass(frozen=True, slots=True)
class BoundaryApiOwnerV1:
    owner: str
    apis: tuple[tuple[str, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        _require_text_v1(self.owner, field="api_owner")
        if type(self.apis) is not tuple or not self.apis:
            raise ContractBoundaryPolicyError("boundary_policy_invalid:apis")
        names: list[str] = []
        for row in self.apis:
            if type(row) is not tuple or len(row) != 2:
                raise ContractBoundaryPolicyError("boundary_policy_invalid:api_row")
            name, capabilities = row
            names.append(name)
            closed = _closed_names_v1(capabilities, field="capabilities")
            if closed != tuple(sorted(closed)):
                raise ContractBoundaryPolicyError(
                    "boundary_policy_invalid:capability_order"
                )
        _closed_names_v1(tuple(names), field="api_names")


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


BOUNDARY_API_OWNERS_V1 = (
    BoundaryApiOwnerV1('executor_birth', (
        ('birth_executor', ('birth',)),
    )),
    BoundaryApiOwnerV1('executor_birth_intent', (
        ('submit_builtin_generation_birth', ('birth',)),
        ('submit_change_extend_birth', ('birth',)),
        ('submit_change_rollback_birth', ('birth',)),
        ('submit_installer_birth', ('birth',)),
        ('submit_promote_birth', ('birth',)),
        ('submit_promoter_rollback_birth', ('birth',)),
        ('submit_skills_birth', ('birth',)),
        ('submit_stack_reconcile_birth', ('birth',)),
        ('submit_synth_producer_birth', ('birth',)),
    )),
    BoundaryApiOwnerV1('executor_birth_operational', (
        ('birth_executor', ('birth',)),
    )),
    BoundaryApiOwnerV1('executor_birth_synth', (
        ('submit_synth_multistage', ('birth',)),
        ('submit_synth_specialize', ('birth',)),
        ('submit_synth_approve', ('birth',)),
    )),
    BoundaryApiOwnerV1('contract_store', (
        ('verify_manifest_source', ('authoring_read', 'authoring_verify')),
        ('prepare_technical_draft', ('authoring_read', 'authoring_verify')),
        ('read_binding', ('verified_store_read',)),
        ('current_revision_id', ('verified_store_read',)),
        ('current_contract', ('verified_store_read',)),
        ('current_manifest', ('verified_store_read',)),
        ('diagnose_store', ('verified_store_read',)),
        ('publish_localization', ('publish_localization',)),
        ('publish_technical_update', ('publish_technical',)),
        ('publish_signed_source', ('publish_bootstrap',)),
        ('retire', ('retire',)),
        ('reactivate_technical_update', ('reactivate',)),
        ('rollback', ('rollback',)),
        ('activate_store', ('legacy_bootstrap',)),
        ('acquire_current_reattestation_snapshot', ('verified_store_read',)),
        ('persist_current_reattestation_receipt', ('store_write',)),
        ('read_current_birth_receipt', ('verified_store_read',)),
    )),
    BoundaryApiOwnerV1('sign', (
        ('sign_executor', ('sign',)),
        ('verify_executor', ('authoring_read', 'authoring_verify')),
        ('publish_executor', ('publish_technical',)),
        ('publish_authoring_update', ('publish_technical',)),
        ('retire_executor_contract', ('retire',)),
        ('reactivate_executor_contract', ('reactivate',)),
        ('rollback_executor_contract', ('rollback',)),
    )),
    BoundaryApiOwnerV1('loader', (
        ('load_catalog', ('live_artifact_read',)),
    )),
    BoundaryApiOwnerV1('invocations', (
        ('load_executor_artifact', ('live_artifact_read',)),
    )),
    BoundaryApiOwnerV1('i18n_migrate_manifests', (
        ('prepare_contract_store_shadow', ('legacy_bootstrap',)),
        ('activate_prepared_contract_store', ('legacy_bootstrap',)),
    )),
    BoundaryApiOwnerV1('contract_cutover_guard', (
        ('contract_cutover_guard', ('cutover_guard',)),
        ('verify_store_only_catalog', ('live_artifact_read', 'verified_store_read')),
    )),
    BoundaryApiOwnerV1('manifest_inventory', (
        ('inventory_authoring_manifests', ('authoring_read',)),
        ('inventory_manifests', ('authoring_read', 'verified_store_read')),
        ('inventory_store_manifests', ('verified_store_read',)),
    )),
    BoundaryApiOwnerV1('executor_birth_authoring', (
        ('read_manifest_ref_versioned', ('authoring_versioned_read',)),
    )),
    BoundaryApiOwnerV1('executor_birth_ownership_chain', (
        ('_InitialOwnershipChainStateV1', ('store_write',)),
        ('_append_pair', ('store_write',)),
        ('_inspect_ownership_chain_state_core_v1', ('store_write',)),
        ('_mint_initial_ownership_chain_state_v1', ('store_write',)),
        ('_replace_required_pointer', ('store_write',)),
        ('_required_head_lock', ('store_write',)),
        ('_update_required_head_locked', ('store_write',)),
        ('append_authenticated_build', ('store_write',)),
        ('append_cutover', ('store_write',)),
        ('append_head', ('store_write',)),
        ('initialize', ('store_write',)),
        ('update_required_head', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_ownership_cutover', (
        ('_publish_no_replace', ('store_write',)),
        ('_sync_directory', ('store_write',)),
        ('_write_temporary', ('store_write',)),
        ('install_ownership_cutover_certificate', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_ownership_coordinator', (
        ('_ACTIVE_DEPLOYMENT_LOCK_LEASES_V1', ('store_write',)),
        ('_ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1', ('store_write',)),
        ('_DEPLOYMENT_LOCK_FORK_GUARD', ('store_write',)),
        ('_DeploymentLockLeaseV1', ('store_write',)),
        ('_OPEN_DEPLOYMENT_LOCK_FDS_V1', ('store_write',)),
        ('_append_coordinator_record_v1', ('store_write',)),
        ('_deployment_lock_at_v1', ('store_write',)),
        ('_deployment_lock_for_test_v1', ('store_write',)),
        ('_deployment_lock_v1', ('store_write',)),
        ('_publish_certificate_with_prerequisite_v1', ('store_write',)),
        ('_publish_control_no_replace_v2', ('store_write',)),
        ('_reserve_transition_edge_core_v2', ('store_write',)),
        ('_reserve_transition_edge_locked_for_test_v2', ('store_write',)),
        ('_reserve_transition_edge_locked_v2', ('store_write',)),
        ('_LockedOwnershipCoordinatorGraphSnapshotV2', ('store_write',)),
        ('_require_locked_coordinator_graph_snapshot_v2', ('store_write',)),
        ('_require_locked_coordinator_graph_issued_v2', ('store_write',)),
        ('_resolve_locked_coordinator_graph_issued_v2', ('store_write',)),
        ('_resolve_ownership_coordinator_locked_v2', ('store_write',)),
        ('require_issued', ('store_write',)),
        ('resolve_issued', ('store_write',)),
        ('prepare_ownership_cutover_v1', ('cutover_guard',)),
    )),
    BoundaryApiOwnerV1('birth_ownership_authority_provisioner', (
        ('_discard_temporary', ('store_write',)),
        ('_load_or_create_pair', ('store_write',)),
        ('_publish_no_replace', ('store_write',)),
        ('_provision_ownership_authorities_at_v1', ('store_write',)),
        ('_provision_ownership_authorities_locked_v1', ('store_write',)),
        ('_provisioning_lock', ('store_write',)),
        ('_sync_directory', ('store_write',)),
        ('_write_exclusive', ('store_write',)),
        ('provision_root_ownership_authorities_v1', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_source_receiver', (
        ('<module>', ('store_write',)),
        ('_copy_source_file_v1', ('store_write',)),
        ('_create_private_directory_v1', ('store_write',)),
        ('_create_source_directories_v1', ('store_write',)),
        ('_ensure_child_directory_v1', ('store_write',)),
        ('_open_received_tree_at_v1', ('store_write',)),
        ('_load_received_source_locked_core_v1', ('store_write',)),
        ('_load_received_source_with_product_session_v1', ('store_write',)),
        ('_load_received_source_with_test_session_v1', ('store_write',)),
        ('_receive_source_for_test_v1', ('store_write',)),
        ('_receive_source_locked_core_v1', ('store_write',)),
        ('_receive_source_v1', ('store_write',)),
        ('_receive_source_with_product_session_v1', ('store_write',)),
        ('_receive_source_with_test_session_v1', ('store_write',)),
        ('_remove_owned_tree_at_v1', ('store_write',)),
        ('_rename_no_replace_v1', ('store_write',)),
        ('_seal_temporary_directories_v1', ('store_write',)),
        ('_verify_received_tree_fd_v1', ('store_write',)),
        ('_write_all_v1', ('store_write',)),
        ('_write_descriptor_v1', ('store_write',)),
        ('copied_chunks', ('store_write',)),
        ('main', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_contract_convergence', (
        ('<module>', ('authoring_read', 'authoring_write', 'birth', 'store_write', 'verified_store_read')),
        ('_source_generation_has_historical_receipt', ('store_write', 'verified_store_read')),
        ('_candidate_for_transition', ('authoring_read', 'authoring_write')),
        ('converge', ('authoring_read', 'authoring_write', 'birth', 'store_write', 'verified_store_read')),
        ('main', ('authoring_read', 'authoring_write', 'birth', 'store_write', 'verified_store_read')),
    )),
    BoundaryApiOwnerV1('executor_birth_transition', (
        ('<module>', ('store_write',)),
        ('_provisioned_service_environment_v1', ('store_write',)),
        ('deploy_source_v1', ('store_write',)),
        ('main', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_host_provisioning', (
        ('_PosixHostEffectsV1._run', ('store_write',)),
        ('_PosixHostEffectsV1.apply_layout_step', ('store_write',)),
        ('_PosixHostEffectsV1.create_account', ('store_write',)),
        ('_PosixHostEffectsV1.create_primary_group', ('store_write',)),
        ('_LockedHostEffectsV1._deactivate_v1', ('store_write',)),
        ('_LockedHostEffectsV1.append_record', ('store_write',)),
        ('_LockedHostEffectsV1.apply_layout_step', ('store_write',)),
        ('_LockedHostEffectsV1.checkpoint', ('store_write',)),
        ('_LockedHostEffectsV1.create_account', ('store_write',)),
        ('_LockedHostEffectsV1.create_primary_group', ('store_write',)),
        ('_LockedHostEffectsV1.load_records', ('store_write',)),
        ('_LockedHostEffectsV1.observe_account', ('store_write',)),
        ('_LockedHostEffectsV1.observe_layout', ('store_write',)),
        ('_LockedHostEffectsV1.observe_primary_group', ('store_write',)),
        ('bind_locked_host_effects_v1', ('store_write',)),
        ('PosixJournalStoreV1._recover_linked_pending', ('store_write',)),
        ('PosixJournalStoreV1._rewrite_staging', ('store_write',)),
        ('PosixJournalStoreV1._stage_record', ('store_write',)),
        ('PosixJournalStoreV1.append_record', ('store_write',)),
        ('_ensure_bootstrap_root_v1', ('store_write',)),
        ('_write_all_v1', ('store_write',)),
        ('locked_host_effects_v1', ('store_write',)),
        ('open_journal_lock_v1', ('store_write',)),
        ('provision_executor_birth_host_v1', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_systemd', (
        ('_install_group6_administrative_for_test_v1', ('store_write',)),
        ('_install_locked_core_v1', ('store_write',)),
        ('_install_signed_isolated_systemd_for_test_v1', ('store_write',)),
        ('_open_parent_v1', ('store_write',)),
        ('_publish_administrative_tree_v1', ('store_write',)),
        ('_publish_isolated_units_for_test_v1', ('store_write',)),
        ('install_group6_administrative_v1', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_admin_preflight', (
        ('_publish_preflight_attestation_core_v1', ('store_write',)),
        ('_publish_preflight_attestation_for_test_v1', ('store_write',)),
        ('_publish_preflight_attestation_v1', ('store_write',)),
        ('_write_all_exact_v1', ('store_write',)),
    )),
)

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
    BoundaryModuleOwnerV1('executor_birth_systemd', ('install.executor_birth_systemd',)),
    BoundaryModuleOwnerV1('executor_birth_admin_preflight', ('executor_birth_admin_preflight', 'runtime.executor_birth_admin_preflight')),
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
    BoundarySourceOwnerV1('install/birth_ownership_authority_provisioner.py', 'birth_ownership_authority_provisioner'),
    BoundarySourceOwnerV1('install/executor_birth_source_receiver.py', 'executor_birth_source_receiver'),
    BoundarySourceOwnerV1('install/executor_birth_contract_convergence.py', 'executor_birth_contract_convergence'),
    BoundarySourceOwnerV1('install/executor_birth_transition.py', 'executor_birth_transition'),
    BoundarySourceOwnerV1('install/executor_birth_host_capability.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_host_journal_posix.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_host_posix.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_host_provisioning.py', 'executor_birth_host_provisioning'),
    BoundarySourceOwnerV1('install/executor_birth_systemd.py', 'executor_birth_systemd'),
    BoundarySourceOwnerV1('runtime/executor_birth_admin_preflight.py', 'executor_birth_admin_preflight'),
)

def _validate_catalog_v1(
    api_rows: tuple[BoundaryApiOwnerV1, ...],
    module_rows: tuple[BoundaryModuleOwnerV1, ...],
    source_rows: tuple[BoundarySourceOwnerV1, ...],
) -> None:
    api_owners = tuple(row.owner for row in api_rows)
    module_owners = tuple(row.owner for row in module_rows)
    source_paths = tuple(row.path for row in source_rows)
    module_aliases = tuple(name for row in module_rows for name in row.module_names)
    _closed_names_v1(api_owners, field="api_owners")
    _closed_names_v1(module_owners, field="module_owners")
    _closed_names_v1(source_paths, field="source_paths")
    _closed_names_v1(module_aliases, field="module_aliases")
    if api_owners != module_owners:
        raise ContractBoundaryPolicyError("boundary_policy_invalid:owner_precedence")
    known_owners = frozenset(module_owners)
    if any(row.owner not in known_owners for row in source_rows):
        raise ContractBoundaryPolicyError(
            "boundary_policy_invalid:source_owner_reference"
        )


_validate_catalog_v1(
    BOUNDARY_API_OWNERS_V1,
    BOUNDARY_MODULE_OWNERS_V1,
    BOUNDARY_SOURCE_OWNERS_V1,
)

__all__ = [
    "BOUNDARY_API_OWNERS_V1", "BOUNDARY_MODULE_OWNERS_V1",
    "BOUNDARY_SOURCE_OWNERS_V1", "BoundaryApiOwnerV1",
    "BoundaryModuleOwnerV1", "BoundarySourceOwnerV1",
]
