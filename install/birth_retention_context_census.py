"""Closed authority-set census; no caller-selected historical subset.

This resolves public bindings only. It neither closes semantic evidence nor
selects Producer declarations.
"""
from types import MappingProxyType
import time

from executor_birth_retention import RetentionError

_MAX_CONTEXTS = 4096
_MAX_SECONDS = 15


def _historical_context_census_v1(*, session, require_exclusion):
    from executor_birth_ownership_chain import VerifiedOwnershipChain, inspect_ownership_chain_state_v1
    from executor_birth_prepared_root import (
        load_historical_context_verifiers_for_contexts_in_session_v1,
        _require_historical_frontier_unchanged_v1,
    )

    require_exclusion()
    session._require_exclusive_global_lock()
    deadline = time.monotonic() + _MAX_SECONDS
    chain = inspect_ownership_chain_state_v1()
    if type(chain) is not VerifiedOwnershipChain or not chain.context_transitions:
        raise RetentionError('retention_inventory_incomplete', 'historical context chain missing')
    transitions = chain.context_transitions
    if len(transitions) >= _MAX_CONTEXTS:
        raise RetentionError('retention_inventory_incomplete', 'historical context budget')
    first = transitions[0]
    expected = {first.previous_admission_context_id: first.previous_set_id}
    for transition in transitions:
        context, set_id = transition.prepared_admission_context_id, transition.set_id
        if context in expected and expected[context] != set_id:
            raise RetentionError('retention_inventory_incomplete', 'ambiguous historical context')
        expected[context] = set_id
    names = session.inventory(('authority-sets',))
    if set(names) != set(expected.values()) or len(names) != len(set(names)):
        raise RetentionError('retention_inventory_incomplete', 'unaccounted historical authority set')
    bindings = {}
    contexts = tuple(sorted(expected))
    authenticated = load_historical_context_verifiers_for_contexts_in_session_v1(contexts, session)
    if len(authenticated) != len(contexts):
        raise RetentionError('retention_inventory_incomplete', 'historical context batch')
    for context, binding in zip(contexts, authenticated):
        set_id = expected[context]
        require_exclusion()
        if time.monotonic() > deadline:
            raise RetentionError('retention_inventory_incomplete', 'historical context budget')
        if (binding.required_head_id != chain.required_head.head_id
                or binding.public_set.set_id != set_id
                or binding.public_set.material.pin.admission_context_id != context):
            raise RetentionError('retention_owner_changed', 'historical context binding')
        bindings[context] = binding
    _require_historical_frontier_unchanged_v1(chain)
    if names != session.inventory(('authority-sets',)):
        raise RetentionError('retention_owner_changed', 'historical context namespace')
    require_exclusion()
    session._require_exclusive_global_lock()
    if time.monotonic() > deadline:
        raise RetentionError('retention_inventory_incomplete', 'historical context budget')
    return MappingProxyType(bindings)
