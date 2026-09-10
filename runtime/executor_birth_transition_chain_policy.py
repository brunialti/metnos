"""Authenticated ownership-chain selection policy for transition retries."""
from __future__ import annotations

from executor_birth_distribution_manifest import (
    installed_tree_hash_v1,
    is_verified_distribution,
)
from executor_birth_ownership_coordinator import (
    _abandonment_binds_record_v2,
    OwnershipCoordinatorError,
    OwnershipCoordinatorRecordV2,
    OwnershipCoordinatorStateV1,
    SuccessorClaimV1,
    _HEAD_PAYLOAD_HASH_DOMAIN_V2,
    _HEAD_SIGNATURE_HASH_DOMAIN_V2,
    _REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2,
    _digest,
    _framed_digest_v2,
)


_INITIAL_CHAIN_PHASES_V2 = frozenset({
    None,
    OwnershipCoordinatorStateV1.PREPARED,
    OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
    OwnershipCoordinatorStateV1.CERTIFICATE_READY,
})
_PARTIAL_INITIAL_CHAIN_PHASES_V2 = frozenset({
    OwnershipCoordinatorStateV1.CERTIFICATE_READY,
    OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED,
    OwnershipCoordinatorStateV1.BUILD_VERIFIED,
})
_PREDECESSOR_CHAIN_PHASES_V2 = frozenset({
    *_INITIAL_CHAIN_PHASES_V2,
    OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED,
    OwnershipCoordinatorStateV1.BUILD_VERIFIED,
})
_TARGET_CHAIN_PHASES_V2 = frozenset({
    OwnershipCoordinatorStateV1.BUILD_VERIFIED,
    OwnershipCoordinatorStateV1.HEAD_REQUIRED,
    OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED,
})


def _record_identity_binding_v2(required, head, record, tree_hash) -> bool:
    return (
        head.release_sequence == record.release_sequence
        and head.cutover_id == record.cutover_id
        and head.closed_build_id == record.closed_build_id
        and head.previous_head_id == record.previous_head_id
        and head.head_id == record.head_id == record.verified_chain_head_id
        and required.release_sequence == record.release_sequence
        and required.identity.closed_build_id == record.closed_build_id
        and required.previous_closed_build_id
        == record.previous_closed_build_id
        and _digest(required.encoded) == record.distribution_payload_hash
        and _digest(required.signature) == record.distribution_signature_hash
        and required.identity.boundary_inventory_hash
        == record.boundary_inventory_hash
        and required.identity.boundary_guard_version
        == record.boundary_guard_version
        and tree_hash == record.installed_tree_hash
    )


def _record_head_binding_v2(chain, head, record, frame) -> bool:
    return (
        _framed_digest_v2(
            _HEAD_PAYLOAD_HASH_DOMAIN_V2, head.encoded,
        ) == record.head_payload_hash
        and _framed_digest_v2(
            _HEAD_SIGNATURE_HASH_DOMAIN_V2, head.signature,
        ) == record.head_signature_hash
        and _framed_digest_v2(
            _REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, frame,
        ) == record.required_head_frame_hash
        and (
            head.previous_head_id is None
            or len(chain.heads) >= 2
            and chain.heads[-2].head_id == head.previous_head_id
        )
    )


def _record_state_admits_binding_v2(record, abandonment) -> bool:
    """A crossing that completed, or one the machine proved it never could.

    An abandoned crossing published its head, and its last record carries that
    head and the frame that required it - which is exactly what this binding
    reads. What it must never accept is a crossing that merely stopped, so the
    abandonment has to bind this exact record: the presence of some abandonment
    somewhere is not a permission.
    """
    if record.state is OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED:
        return True
    return (
        record.state is OwnershipCoordinatorStateV1.HEAD_REQUIRED
        and _abandonment_binds_record_v2(abandonment, record)
    )


def _required_chain_matches_record_v2(chain, record, abandonment) -> bool:
    """Bind a verified required chain to one admissible coordinator record."""
    from executor_birth_ownership_chain import (
        OwnershipHead, encode_required_head,
    )

    required, head = chain.required_distribution, chain.required_head
    if (
        type(record) is not OwnershipCoordinatorRecordV2
        or not _record_state_admits_binding_v2(record, abandonment)
        or type(head) is not OwnershipHead
        or not is_verified_distribution(required)
    ):
        return False
    try:
        tree_hash = installed_tree_hash_v1(required.files)
        frame = encode_required_head(head)
    except Exception:
        return False
    return _record_identity_binding_v2(
        required, head, record, tree_hash,
    ) and _record_head_binding_v2(
        chain, head, record, frame,
    )


def _target_edge_binding_v2(
    chain, distribution, required, claim, phase, head, tree_hash,
) -> bool:
    return (
        claim.release_sequence == phase.release_sequence
        == distribution.release_sequence == head.release_sequence
        and claim.closed_build_id == phase.closed_build_id
        == distribution.identity.closed_build_id == head.closed_build_id
        and claim.previous_head_id == phase.previous_head_id
        == head.previous_head_id
        and claim.request_id == phase.request_id
        and claim.claim_id == phase.successor_claim_id
        and head.cutover_id == phase.cutover_id
        and required.previous_closed_build_id
        == phase.previous_closed_build_id
        and _digest(required.encoded) == phase.distribution_payload_hash
        and _digest(required.signature) == phase.distribution_signature_hash
        and required.identity.boundary_inventory_hash
        == phase.boundary_inventory_hash
        and required.identity.boundary_guard_version
        == phase.boundary_guard_version
        and tree_hash == phase.installed_tree_hash
        and (
            head.previous_head_id is None
            or len(chain.heads) >= 2
            and chain.heads[-2].head_id == head.previous_head_id
        )
    )


def _target_head_binding_v2(head, phase, frame) -> bool:
    return (
        head.head_id == phase.head_id == phase.verified_chain_head_id
        and _framed_digest_v2(
            _HEAD_PAYLOAD_HASH_DOMAIN_V2, head.encoded,
        ) == phase.head_payload_hash
        and _framed_digest_v2(
            _HEAD_SIGNATURE_HASH_DOMAIN_V2, head.signature,
        ) == phase.head_signature_hash
        and _framed_digest_v2(
            _REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, frame,
        ) == phase.required_head_frame_hash
    )


def _required_chain_matches_target_v2(chain, observed) -> bool:
    """Bind a verified required chain to the target edge at the CAS seam."""
    from executor_birth_ownership_chain import (
        OwnershipHead, encode_required_head,
    )

    distribution, claim, phase = (
        observed.distribution, observed.claim, observed.phase,
    )
    required, head = chain.required_distribution, chain.required_head
    if (
        type(claim) is not SuccessorClaimV1
        or type(phase) is not OwnershipCoordinatorRecordV2
        or type(head) is not OwnershipHead
        or not is_verified_distribution(required)
        or required != distribution
    ):
        return False
    try:
        tree_hash = installed_tree_hash_v1(required.files)
    except Exception:
        return False
    if not _target_edge_binding_v2(
        chain, distribution, required, claim, phase, head, tree_hash,
    ):
        return False
    if phase.state is OwnershipCoordinatorStateV1.BUILD_VERIFIED:
        return phase.head_id is None and phase.verified_chain_head_id is None
    try:
        frame = encode_required_head(head)
    except Exception:
        return False
    return _target_head_binding_v2(head, phase, frame)


def _transition_chain_authority_source_v2(observed) -> str:
    """Validate the crash-phase matrix and select historical or required trust."""
    from executor_birth_ownership_chain import (
        VerifiedOwnershipChain, _InitialOwnershipChainStateV1,
    )

    distribution, phase, chain = (
        observed.distribution, observed.phase, observed.chain,
    )
    state = None if phase is None else phase.state
    if observed.partial:
        if (
            chain is None
            and distribution.release_sequence == 1
            and state in _PARTIAL_INITIAL_CHAIN_PHASES_V2
        ):
            return "historical"
    elif type(chain) is _InitialOwnershipChainStateV1:
        if (
            distribution.release_sequence == 1
            and state in _INITIAL_CHAIN_PHASES_V2
        ):
            return "historical"
    elif type(chain) is VerifiedOwnershipChain:
        if (
            state in _TARGET_CHAIN_PHASES_V2
            and _required_chain_matches_target_v2(chain, observed)
        ):
            return "required"
        if (
            distribution.release_sequence > 1
            and state in _PREDECESSOR_CHAIN_PHASES_V2
            and _required_chain_matches_record_v2(
                chain, observed.predecessor, observed.predecessor_abandonment,
            )
        ):
            return "required"
    raise OwnershipCoordinatorError(
        "birth_ownership_recovery_required", "chain phase",
    )
