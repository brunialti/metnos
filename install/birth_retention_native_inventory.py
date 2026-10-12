"""Assemble native owners from an already observed stopped installation.

This private constructor selects no paths from the administrative environment,
creates no application stores and grants no collection authority. The installed
coordinator still owns physical namespace coverage, holds and phase boundaries.
"""
from pathlib import Path
from types import MappingProxyType

from executor_birth_retention import RetentionError
from install.birth_retention_maintenance import OwnerObject
from install.birth_retention_inventory import _InventoryComposition
from install.birth_retention_path_probe import PATH_KEYS


class _OwnerInventory:
    """Expose one native owner without changing its physical projection."""
    def __init__(self, owner):
        self.owner = owner
        self.owners = MappingProxyType({owner.name: owner})

    def inventory(self):
        return self.owner.inventory()


class _RowsInventory(_OwnerInventory):
    def inventory(self):
        owner = self.owner
        first = owner.scan()
        result = []
        for row in first:
            state = owner.state_of(row)
            result.append(OwnerObject(row.identity, row.version, state.state,
                state.created_at, state.eligible_after, roots=state.roots))
        if first != owner.scan():
            raise RetentionError('retention_owner_changed', 'native SQLite projection')
        return tuple(result)


def _observed_paths_v1(paths):
    if set(paths) != PATH_KEYS:
        raise RetentionError('retention_inventory_incomplete', 'native path set')
    result = {}
    for name, value in paths.items():
        if (type(value) is not str or '\0' in value or not Path(value).is_absolute()
                or str(Path(value)) != value):
            raise RetentionError('retention_inventory_incomplete', 'native absolute path')
        result[name] = Path(value)
    # These native consumers share an owner only when their observed paths do.
    for names in (('history_backups', 'history_legacy', 'history_expanded_backups'),
                  ('PATH_TURNS', 'turn_feedback_turns'),
                  ('skill_admission_audit', 'skills_cli_audit'),
                  ('PATH_DURABLE_ARTIFACTS', 'lre_artifact_store')):
        if len({result[name] for name in names}) != 1:
            raise RetentionError('retention_inventory_incomplete', 'distinct native owner paths: ' + ','.join(names))
    return MappingProxyType(result)


def _compose_native_inventory_v1(*, paths, account_owner, birth_root, session,
        context_bindings, catalog, reviews_root,
        require_exclusion, public_sources=()):
    """Reuse exact shared owner instances across Birth, journals and LRE.

    Archive location comes from the installed native path probe. Public historical sources stay inert inputs
    of the existing native Producer authenticator.
    """
    from contract_store import STORE_RELATIVE
    from durable_runtime_registry import runtime_registry_for_catalog
    from durable_workloads.artifacts import ArtifactWorkspacePaths
    from executor_birth_feedback import _QUEUE_SCHEMA
    from install.birth_retention_admission_links import _ApprovalAdmissionInventory
    from install.birth_retention_birth_sqlite import _BirthBundleOwner, _review_state
    from install.birth_retention_change_rollback import _ChangeRollbackBlobs, _ChangeRollbackInventory
    from install.birth_retention_epoch_inventory import _EpochInventory
    from install.birth_retention_epoch_sqlite import _EpochOwner
    from install.birth_retention_feedback import _FeedbackOwner
    from install.birth_retention_history import _HistoryBackupOwner
    from install.birth_retention_introvertiva import _IntrovertivaOwner
    from install.birth_retention_jobs_sqlite import _WorkloadOwner, _scheduler_owners
    from install.birth_retention_journals import _JournalAdmissionInventory, _JournalInventory
    from install.birth_retention_lre_inventory import _LreInventory
    from install.birth_retention_producer_links import _ProducerAdmissionInventory
    from install.birth_retention_promoter import _PromoterOwner, _PromoterBlobs, _PromoterInventory
    from install.birth_retention_protected_undo import _ProtectedUndoOwner
    from install.birth_retention_review_links import _ReviewApprovalInventory
    from install.birth_retention_reviews import _ReviewOwner
    from install.birth_retention_semantic_inventory import _SemanticEvidenceInventory
    from install.birth_retention_signed_store import _SignedStoreOwner
    from install.birth_retention_source_authority import (
        _SourceAuthorityInventory, _SourceGrantOwner, _SourceSnapshots,
    )
    from install.birth_retention_sqlite import _SQLiteOwner, _operational_sqlite_owners
    from install.birth_retention_synth import _SynthProposalOwner
    from install.birth_retention_synth_archive import _SynthArchiveOwner, _SynthSourcesInventory
    from install.birth_retention_synth_candidates import _PersistentSynthOwner
    from install.birth_retention_telos import _TelosDecisions, _TelosProposalOwner
    from install.birth_retention_telos_markers import _TelosMarkerOwner
    from install.birth_retention_turn_archives import _TurnArchiveOwner
    from install.birth_retention_turns import _TurnLogOwner
    from install.birth_retention_undo import _UndoOwner

    p = _observed_paths_v1(paths)
    require_exclusion()
    session._require_exclusive_global_lock()
    if Path(birth_root) != Path(session._root_path):
        raise RetentionError('retention_owner_invalid', 'native Birth root')
    if (type(account_owner) is not tuple or len(account_owner) != 2
            or any(type(value) is not int or value < 0 for value in account_owner)):
        raise RetentionError('retention_owner_invalid', 'native service owner')
    for value in (reviews_root,):
        if not isinstance(value, Path) or not value.is_absolute():
            raise RetentionError('retention_inventory_incomplete', 'native journal path')
    author_keys, admission_keys = _historical_keys_v1(context_bindings)
    common = dict(require_exclusion=require_exclusion, owner=account_owner)
    signed = _SignedStoreOwner(root=p['PATH_USER_STATE'] / STORE_RELATIVE,
        admission_keys=admission_keys, author_keys=author_keys, **common)
    birth = p['PATH_USER_STATE'] / 'birth'
    producers = _BirthBundleOwner('producer_receipts', path=birth / 'producer_receipts.sqlite', **common)
    approvals = _BirthBundleOwner('approvals', path=birth / 'approvals.sqlite', **common)
    failures = _SQLiteOwner(name='birth_failure_reviews', path=birth / 'failure_reviews.sqlite',
        schema=_QUEUE_SCHEMA, table='executor_failure_review_queue', primary_key='job_id',
        node_type='feedback', state=_review_state, **common)
    reviews = _ReviewOwner(root=reviews_root, require_exclusion=require_exclusion, owner=(0, 0))
    review_links = _ReviewApprovalInventory(reviews=reviews, approvals=approvals,
        session=session, require_exclusion=require_exclusion, context_bindings=context_bindings)
    components = [
        _ProducerAdmissionInventory(producers=producers, signed_store=signed,
            session=session, require_exclusion=require_exclusion, public_sources=public_sources,
            context_bindings=context_bindings),
        _ApprovalAdmissionInventory(review_links=review_links, signed_store=signed,
            require_exclusion=require_exclusion),
        _EpochInventory(epochs=_EpochOwner(path=birth / 'executor_epochs.sqlite', **common),
            signed_store=signed),
        _SemanticEvidenceInventory(reviews=reviews, birth_root=birth_root,
            contexts=tuple(context_bindings), context_bindings=context_bindings,
            session=session, **common),
    ]

    proposals, intents, approval_registry = _operational_sqlite_owners(
        require_exclusion, account_owner, proposals_state_path=p['proposals_state'],
        change_intents_path=p['DB_CHANGE_INTENTS'], approval_registry_path=p['approvals'])
    components.extend(_RowsInventory(item) for item in (proposals, approval_registry))
    components.append(_ChangeRollbackInventory(intents=intents,
        blobs=_ChangeRollbackBlobs(root=p['PATH_USER_DATA'] / 'rollback_blobs', intents=intents)))
    # The proposal/promoter projection already includes this exact intent owner.
    synth = _SynthSourcesInventory(
        synth=_SynthProposalOwner(root=p['promoter_proposals'], intents=intents),
        archive=_SynthArchiveOwner(root=p['promoter_archive'], intents=intents))
    promoter = _PromoterOwner(path=p['promoter_state'], **common)
    components.extend((
        _PromoterInventory(promoter=promoter, blobs=_PromoterBlobs(root=p['promoter_blobs'], promoter=promoter),
            synth=synth, signed_store=signed, reviews=reviews),
        _OwnerInventory(_PersistentSynthOwner(proposals=p['persistent_synth'], **common)),
        _OwnerInventory(_IntrovertivaOwner(root=p['introvertiva'], **common)),
        _TelosProposalOwner(paths=tuple(p[name] for name in
            ('telos_recomposed', 'telos_rescored', 'telos_proposals')),
            decisions=_TelosDecisions(path=p['telos_decisions'], **common),
            markers=_TelosMarkerOwner(root=p['telos_acceptance_markers'], **common)),
    ))

    undo = _UndoOwner(path=p['undo'], **common)
    journals = _JournalInventory(turns=_TurnLogOwner(root=p['PATH_TURNS'], **common),
        archives=_TurnArchiveOwner(root=p['turn_archives'], **common),
        feedback=_FeedbackOwner(reviews=failures, path=p['turn_feedback'], **common),
        protected_undo=_ProtectedUndoOwner(root=p['protected_undo_blobs'], journal=undo,
            key_path=p['protected_undo_key']),
        history=_HistoryBackupOwner(root=p['history_backups'], journal=undo))
    components.append(_JournalAdmissionInventory(journals=journals, signed_store=signed,
        require_exclusion=require_exclusion))

    registry = runtime_registry_for_catalog(catalog)
    artifacts = ArtifactWorkspacePaths(p['lre_artifact_store'])
    workloads = _WorkloadOwner(path=p['workloads'], resolve_workspaces=registry.temporary_workspaces,
        artifact_workspace=artifacts.temporary_workspace, **common)
    components.append(_LreInventory(workloads=workloads, artifact_root=p['lre_artifact_store'],
        image_root=p['PATH_INDEX_IMAGE']))
    components.append(_SourceAuthorityInventory(
        grants=_SourceGrantOwner(path=p['lre_source_authority'], **common),
        snapshots=_SourceSnapshots(root=p['lre_source_authority'].parent / 'source_snapshots', **common)))
    components.extend(_RowsInventory(item) for item in _scheduler_owners(path=p['scheduler'], **common))
    components.extend(_audit_components_v1(p, common))
    require_exclusion()
    return _InventoryComposition(components=tuple(components),
        required_owners=frozenset(name for component in components for name in component.owners),
        require_exclusion=require_exclusion)


def _historical_keys_v1(bindings):
    from executor_birth_keystore import raw_public_key
    from executor_birth_prepared_root import HistoricalContextVerifiersV1
    author_keys, admission_keys = {}, {}
    if not bindings:
        raise RetentionError('retention_inventory_incomplete', 'native historical bindings')
    for context, binding in bindings.items():
        if (type(binding) is not HistoricalContextVerifiersV1
                or binding.public_set.material.pin.admission_context_id != context):
            raise RetentionError('retention_owner_invalid', 'native historical binding')
        for selected, keys in ((author_keys, binding.public_set.author_verifier_keys),
                               (admission_keys, binding.public_set.admission_verifier_keys)):
            for key_id, key in keys.items():
                if key_id in selected and raw_public_key(selected[key_id]) != raw_public_key(key):
                    raise RetentionError('retention_owner_invalid', 'ambiguous historical key')
                selected[key_id] = key
    return MappingProxyType(author_keys), MappingProxyType(admission_keys)


def _audit_components_v1(paths, common):
    from install.birth_retention_affinity_audit import _AffinityAuditOwner
    from install.birth_retention_efficacy_audit import _EfficacyAuditOwner
    from install.birth_retention_evaluation_audit import _ProposalEvaluationAuditOwner, _SemanticVerificationAuditOwner
    from install.birth_retention_import_audit import _ImportAuditOwner
    from install.birth_retention_llm_cost import _LlmCostOwner
    from install.birth_retention_promoter_audit import _PromoterDailyAuditOwner
    from install.birth_retention_open_audit import _PromoterReviewAuditOwner, _TelosFilteredOwner
    from install.birth_retention_sites import _SitesAuditOwner
    audit = paths['synth_audit']
    owners = (
        _SitesAuditOwner(path=paths['sites_audit'], **common),
        _LlmCostOwner(path=paths['llm_cost'], **common),
        _ImportAuditOwner(path=paths['skill_admission_audit'], **common),
        _AffinityAuditOwner(path=audit / 'affinity_rejected.jsonl', **common),
        _EfficacyAuditOwner(path=audit / 'efficacy_demotions.jsonl', **common),
        _ProposalEvaluationAuditOwner(path=audit / 'proposal_evaluator.jsonl', **common),
        _SemanticVerificationAuditOwner(path=audit / 'verify_19700101T000000Z_00000000.jsonl', **common),
        _PromoterDailyAuditOwner(path=paths['promoter_audit'] / 'promoter_1970-01-01.jsonl', **common),
        _PromoterReviewAuditOwner(root=audit, **common),
        _TelosFilteredOwner(path=paths['PATH_USER_DATA'] / 'telos_filtered.jsonl', **common),
    )
    return tuple(_OwnerInventory(owner) for owner in owners)
