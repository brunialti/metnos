"""Immutable API, module and source-owner boundary facts."""
from __future__ import annotations
from dataclasses import dataclass
from contract_boundary_module_policy import (
    BOUNDARY_MODULE_OWNERS_V1, BOUNDARY_SOURCE_OWNERS_V1,
    BoundaryModuleOwnerV1, BoundarySourceOwnerV1,
)
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
        ('_load_catalog_for_cutover_audit_v1', ('live_artifact_read',)),
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
    BoundaryApiOwnerV1('executor_birth_transition_authority', (
        ('_build_staged_current_receipts_v2', ('store_write',)),
        ('_require_transition_current_enumerator_v2', ('store_write',)),
        ('_transition_chain_authority_source_v2', ('store_write',)),
        ('_transition_current_enumerator_v2', ('store_write',)),
        ('_transition_gate_snapshot_locked_v2', ('store_write',)),
        ('_transition_inventory_under_maintenance_v2', (
            'live_artifact_read', 'store_write', 'verified_store_read',
        )),
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
        ('HostJournalEffectsV1.append_record', ('store_write',)),
        ('bind_locked_host_effects_v1', ('store_write',)),
        ('PosixJournalStoreV1.append_record', ('store_write',)),
        ('_ensure_bootstrap_root_v1', ('store_write',)),
        ('locked_host_effects_v1', ('store_write',)),
        ('open_journal_lock_v1', ('store_write',)),
        ('provision_executor_birth_host_v1', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_posix_foundation', (
        ('BoundPosixAppendJournalV1._create_stage_v1', ('store_write',)),
        ('BoundPosixAppendJournalV1._promote_v1', ('store_write',)),
        ('BoundPosixAppendJournalV1._recover_linked_v1', ('store_write',)),
        ('BoundPosixAppendJournalV1._rewrite_v1', ('store_write',)),
        ('BoundPosixAppendJournalV1._stage_v1', ('store_write',)),
        ('BoundPosixAppendJournalV1._unlink_pending_v1', ('store_write',)),
        ('BoundPosixAppendJournalV1.append_exact', ('store_write',)),
        ('_write_all_v1', ('store_write',)),
        ('open_journal_lock_v1', ('store_write',)),
        ('remove_acl_v1', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_legacy_state_adoption', (
        ('_LegacyStateEffectsV1._change_owner_v1', ('store_write',)),
        ('_LegacyStateEffectsV1.adopt_authoring', ('store_write',)),
        ('_LockedLegacyStateEffectsV1.adopt_authoring', ('store_write',)),
        ('_LockedLegacyStateEffectsV1.append_record', ('store_write',)),
        ('LegacyStateJournalStoreV1.append_record', ('store_write',)),
        ('_complete_legacy_state_ready_v1', ('store_write',)),
        ('prepare_legacy_state_authoring_v1', ('store_write',)),
        ('_inspect_terminal_legacy_state_v1', ('live_artifact_read', 'verified_store_read')),
        ('inspect_ready_legacy_state_live_v1', ('live_artifact_read', 'verified_store_read')),
        ('inspect_terminal_legacy_state_history_v1', ('verified_store_read',)),
        ('locked_legacy_state_effects_v1', ('store_write',)),
        ('open_legacy_journal_lock_v1', ('store_write',)),
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
        ('_attest_operational_preflight_v1', ('live_artifact_read', 'verified_store_read')),
        ('_preflight_attestation_bytes_v1', ('verified_store_read',)),
        ('main', ('live_artifact_read', 'verified_store_read')),
    )),
    BoundaryApiOwnerV1('executor_birth_preflight_attestation_store', (
        ('_publish_preflight_attestation_core_v1', ('store_write',)),
        ('_publish_preflight_attestation_for_test_v1', ('store_write',)),
        ('_publish_preflight_attestation_v1', ('store_write',)),
    )),
    BoundaryApiOwnerV1('executor_birth_preflight_store_authority', (
        ('bind_store_mutation_port_v1', ('store_write',)),
    )),
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
