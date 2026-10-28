# SPDX-License-Identifier: MIT
"""Validate one semantic collection observation, without browser/model authority.

The broker owns IDs, snapshots and effects. The collection observer owns semantic
roles; exact spans make its interpretation inspectable without inventing site
vocabulary or treating a substring of an entity ID as an account/category.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

MAX_CONTROLS = 64
MAX_CONTEXTS = 8
MAX_RESPONSE_TOKENS = 1024
STATUSES = {'no_items': 'none', 'unmatched_items': 'related', 'matching_items': 'matching'}
CONTEXT_KINDS = {'partition', 'filter', 'presentation', 'unknown'}
ROLES = {'account', 'category', 'temporal', 'entity_id', 'quantity'}


class InvalidObservation(ValueError):
    def __init__(self, reason, *, phase='context_grounding', facets=None):
        super().__init__(reason)
        self.phase = phase
        # Closed values only: the shape of a refused answer, never its text.
        self.facets = dict(facets or {})


def _fail(reason, *, phase='context_grounding', facets=None):
    raise InvalidObservation(reason, phase=phase, facets=facets)


def span(request, evidence, *, roles):
    if not isinstance(evidence, dict) or evidence.get('role') not in roles:
        _fail('ungrounded_role')
    text = evidence.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 256:
        _fail('ungrounded_span')
    start = request.find(text)
    if start < 0:
        _fail('ungrounded_span')
    if start == request.rfind(text):
        # The exact unique quote is the authority, not model character counting.
        end = start + len(text)
    else:
        start, end = evidence.get('start'), evidence.get('end')
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(request) or request[start:end] != text):
            _fail('ambiguous_span')
    # A token joined by punctuation to an entity reference is not independent
    # evidence for a category (INV-B -> B, A/B -> B, account123 -> 123).
    def joined(left, right):
        left_kind, right_kind = unicodedata.category(left), unicodedata.category(right)
        word = lambda c, kind: c.isalnum() or c in '_-/' or kind[0] == 'M' or kind == 'Cf'
        return (right_kind[0] == 'M' or right_kind == 'Cf' or left_kind == 'Cf'
                or (word(left, left_kind) and word(right, right_kind)))
    if ((start and joined(request[start-1], request[start]))
            or (end < len(request) and joined(request[end-1], request[end]))):
        _fail('partial_entity_span')
    return dict(start=start, end=end, text=text, role=evidence['role'])


def scope(request, value):
    if not isinstance(value, dict) or value.get('kind') not in {'all', 'limit', 'single'}:
        _fail('invalid_scope')
    kind, evidence, count = value['kind'], value.get('evidence'), value.get('limit')
    if kind == 'all':
        if evidence is not None or count is not None:
            _fail('invalid_all_scope')
        return {'kind': 'all', 'evidence': None, 'limit': None}
    grounded = span(request, evidence, roles={'quantity'} if kind == 'limit' else {'entity_id'})
    if kind == 'limit':
        if type(count) is not int or not 1 <= count <= 100000:
            _fail('invalid_quantity')
        if grounded['text'].strip().isdecimal() and int(grounded['text'].strip()) != count:
            _fail('quantity_quote_mismatch')
    elif count is not None:
        _fail('invalid_single_scope')
    return {'kind': kind, 'evidence': grounded, 'limit': count}


def scene_key(*, url, text, candidates, context, fence):
    """A decision is reusable only for identical data, context and broker fence."""
    return hashlib.sha256(json.dumps([url, text, candidates, context, fence],
        ensure_ascii=True, sort_keys=True, default=str).encode()).hexdigest()



def option_owners(controls, option):
    """Observed selector membership, shared by model input and validation."""
    expanded = [c for c in controls if c.get('aria_expanded') == 'true']
    observed = {option.get('dom_id'), *(option.get('ancestor_ids') or ())}
    owners = [c['id'] for c in controls
              if set(c.get('control_targets') or ()).intersection(observed)]
    if owners:
        return owners
    return ([expanded[0]['id']] if len(expanded) == 1
            and option.get('role') in {'option', 'menuitem', 'menuitemradio'} else [])


def parse(request, payload, candidates, controls, choices, *, previous_scope=None):
    if not isinstance(payload, dict):
        _fail('invalid_response')
    try:
        request_scope = scope(request, payload.get('scope'))
    except InvalidObservation as exc:
        exc.phase = 'collection_scope'
        raise
    if previous_scope is not None and request_scope != previous_scope:
        _fail('changed_scope', phase='collection_scope')
    status = payload.get('content_status')
    if status not in STATUSES:
        _fail('invalid_content_status', phase='collection_content')
    by_id = {c['id']: c for c in candidates}
    context_ids = {c['id']: c for c in controls}
    option_ids = {c['id']: c for c in choices}
    if len(by_id) != len(candidates) or len(controls) > MAX_CONTEXTS:
        _fail('ambiguous_observation')
    contexts = payload.get('contexts')
    if not isinstance(contexts, list) or len(contexts) > MAX_CONTEXTS:
        _fail('incomplete_context_classification')
    # DOM hints are mandatory, but do not exhaust semantic controls: a plain
    # observed button may constrain the collection without any ARIA popup.
    # Options never become owners and no model ID creates a browser control.
    for check in contexts:
        if (not isinstance(check, dict) or type(check.get('id')) is not str
                or check['id'] not in by_id or check['id'] in option_ids
                or by_id[check['id']].get('role') in {'option', 'menuitemradio'}):
            _fail('unknown_context_control')
    if not context_ids.keys() <= {check['id'] for check in contexts}:
        _fail('incomplete_context_classification')
    context_ids.update((check['id'], by_id[check['id']]) for check in contexts)
    effective_controls = list(context_ids.values())
    seen, pending, requested, partitions, grounded_contexts = set(), [], {}, [], []
    roles = []
    for check in contexts:
        if not isinstance(check, dict) or check.get('id') not in context_ids or check['id'] in seen:
            _fail('unknown_context_control')
        seen.add(check['id'])
        control, kind = context_ids[check['id']], check.get('kind')
        if kind not in CONTEXT_KINDS or type(check.get('current_matches')) is not bool:
            _fail('invalid_context_classification')
        matches = check.get('matching_options')
        if not isinstance(matches, list) or any(type(i) is not str or i not in option_ids for i in matches):
            _fail('unknown_context_option')
        if len(set(matches)) != len(matches) or any(
                option_owners(effective_controls, option_ids[i]) != [control['id']] for i in matches):
            _fail('ambiguous_context_option_owner')
        evidence = check.get('requested')
        if evidence is not None:
            role_set = {'account', 'category'} if kind == 'partition' else {'account', 'category', 'temporal', 'entity_id'}
            if kind in {'presentation', 'unknown'}:
                _fail('ungrounded_context_kind')
            evidence = span(request, evidence, roles=role_set)
        grounded_contexts.append({**check, 'requested': evidence})
        requested[check['id']] = evidence
        if kind == 'partition':
            partitions.append({'id': check['id'], 'requested': evidence,
                               'options': [dict(option_ids[i]) for i in matches]})
        if evidence is None:
            # No request for this context means no filtering effect. In an all
            # collection a relevant partition instead needs bounded traversal.
            continue
        if check['current_matches']:
            continue
        if control.get('aria_expanded') != 'true':
            pending.append(control)
        elif len(matches) != 1:
            _fail('context_option_ambiguous' if matches else 'context_option_missing')
        else:
            pending.append(option_ids[matches[0]])
        roles.append(evidence['role'])
    choice, purpose = payload.get('next_control'), payload.get('purpose')
    if choice is None or choice == 'NONE':
        # No control to follow ends the observed branch, whatever purpose the
        # answer names (live run a6, ENI 22/10/2026: two such answers on the
        # page where a branch ends stopped the whole collection).
        choice, purpose = 'NONE', 'done'
    if (not isinstance(choice, str) or choice not in {*by_id, 'NONE'}
            or purpose not in {'navigate', 'expand_collection', 'detail', 'done'}
            or (choice == 'NONE') != (purpose == 'done')):
        _fail('invalid_route', phase='collection_route', facets={
            'route_choice': ('known' if choice in by_id else 'unknown' if isinstance(choice, str)
                             else 'other_type'),
            'route_purpose': (purpose if purpose in {'navigate', 'expand_collection', 'detail', 'done'}
                              else 'other')})
    if choice in context_ids or choice in option_ids:
        _fail('context_used_as_route', phase='collection_route')
    candidate = dict(by_id[choice]) if choice != 'NONE' else {}
    candidate.update(collection_observed=STATUSES[status] != 'none',
        proximity=STATUSES[status], collection_purpose=purpose,
        collection_continuation=purpose == 'expand_collection')
    return {'scope': request_scope, 'content_status': status, 'candidate': candidate,
            'contexts': grounded_contexts, 'partitions': partitions, 'requested': requested,
            'context_controls': effective_controls,
            'context': ({'candidate': pending[0], 'model_selected': True,
                         'confidence': .5, 'role': roles[0]} if pending else {'ready': True})}
