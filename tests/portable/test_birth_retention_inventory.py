"""Bounded composition, including unchanged native LRE shared-owner graphs."""
from dataclasses import replace
from types import SimpleNamespace
import os

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_inventory import _InventoryComposition
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_workloads import _WorkloadInventory
from test_birth_retention_artifacts import native


def owner(name):
    return SimpleNamespace(name=name, require_exclusion=lambda: None)


def obj(name, local='1', **changes):
    item = OwnerObject(ObjectIdentity(name, 'file:///fixture', 'job', local),
                       'sha256:' + 'a' * 64, NodeState.OPEN,
                       '2020-01-01T00:00:00Z', None)
    return replace(item, **changes)


def component(owners, objects):
    return SimpleNamespace(owners={o.name: o for o in owners}, inventory=lambda: tuple(objects))


def compose(*components, required=None, require_exclusion=lambda: None):
    names = required if required is not None else {name for c in components for name in c.owners}
    return _InventoryComposition(components=components, required_owners=names,
                                 require_exclusion=require_exclusion)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX artifact owners")
def test_native_workload_and_blob_subgraphs_share_real_owner_and_edges(native):
    native.create('owner-a')
    joined = _WorkloadInventory(workloads=native.jobs, artifact_root=native.root)
    blobs = joined.file_owners[0]
    physical = SimpleNamespace(owners={native.jobs.name: native.jobs, blobs.name: blobs},
                               inventory=blobs.inventory)
    result = compose(joined, physical).inventory()
    assert result == joined.inventory()
    assert any(obj.references for obj in result)
    assert native.store.get_workload('owner-a', 'same-local-id') is not None


def test_shared_projection_merges_only_edges_and_keeps_empty_owner_observed():
    a, b, empty = owner('a'), owner('b'), owner('empty')
    target, source = obj('b'), obj('a')
    first = component([a, empty], [replace(source, references=(target.identity,))])
    second = component([a, b], [source, target])
    joined = compose(first, second)
    assert set(joined.owners) == {'a', 'b', 'empty'}
    assert next(o for o in joined.inventory() if o.identity == source.identity).references == (target.identity,)
    with pytest.raises(TypeError):
        joined.owners['bad'] = a


@pytest.mark.parametrize('required', [set(), {'a', 'missing'}, {'foreign'}])
def test_required_owner_scope_is_exact(required):
    with pytest.raises(RetentionError):
        compose(component([owner('a')], []), required=required)


def test_duplicate_component_and_distinct_instances_of_same_owner_rejected():
    item = component([owner('a')], [])
    with pytest.raises(RetentionError):
        compose(item, item)
    with pytest.raises(RetentionError):
        compose(item, component([owner('a')], []))


@pytest.mark.parametrize('fault', ['foreign', 'duplicate', 'unresolved', 'missing_shared', 'version', 'state', 'window', 'store', 'roots'])
def test_incomplete_or_conflicting_native_projections_rejected(fault):
    a, b = owner('a'), owner('b')
    source = obj('a')
    first = component([a], [source])
    second = component([a, b], [source, obj('b')])
    if fault == 'foreign':
        first.inventory = lambda: (obj('other'),)
    elif fault == 'duplicate':
        first.inventory = lambda: (source, source)
    elif fault == 'unresolved':
        first.inventory = lambda: (replace(source, references=(obj('missing').identity,)),)
    elif fault == 'missing_shared':
        second.inventory = lambda: (obj('b'),)
    else:
        changed = {'version': {'version': 'sha256:' + 'b' * 64},
                   'state': {'state': NodeState.CLOSED, 'eligible_after': '2021-01-01T00:00:00Z'},
                   'window': {'eligible_after': '2021-01-01T00:00:00Z'},
                   'store': {'identity': replace(source.identity, store='file:///other')},
                   'roots': {'roots': (RootKind.OPEN_AUDIT,)}}[fault]
        second.inventory = lambda: (replace(source, **changed), obj('b'))
    with pytest.raises(RetentionError):
        compose(first, second).inventory()


@pytest.mark.parametrize('fault', ['owner_map', 'owner_name', 'object', 'masked_edge'])
def test_second_read_detects_changes_even_when_other_component_masks_edge(fault):
    a, b = owner('a'), owner('b')
    target = obj('b')
    source = obj('a', references=(target.identity,))
    first = component([a, b], [source, target])
    second = component([a, b], [source, target])
    calls = 0
    def changing():
        nonlocal calls
        calls += 1
        if calls == 2:
            if fault == 'owner_map':
                first.owners['a'] = owner('a')
            elif fault == 'owner_name':
                a.name = 'changed'
            elif fault == 'object':
                return (replace(source, version='sha256:' + 'c' * 64), target)
            else:
                return (replace(source, references=()), target)
        return source, target
    first.inventory = changing
    with pytest.raises(RetentionError):
        compose(first, second).inventory()


def test_exclusion_rechecked_and_native_failure_propagated():
    a = owner('a')
    calls = 0
    def guard():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RetentionError('retention_exclusion_lost', 'fixture')
    with pytest.raises(RetentionError, match='fixture'):
        compose(component([a], []), require_exclusion=guard).inventory()
    broken = component([owner('b')], [])
    def unreadable():
        raise OSError('native store unreadable')
    broken.inventory = unreadable
    with pytest.raises(OSError, match='native store unreadable'):
        compose(broken).inventory()


@pytest.mark.parametrize('budget', ['_MAX_OBJECTS', '_MAX_REFERENCES', '_SECONDS'])
def test_composition_resource_bound_is_explicit(monkeypatch, budget):
    import install.birth_retention_inventory as module
    monkeypatch.setattr(module, budget, -1)
    with pytest.raises(RetentionError, match='composition budget'):
        compose(component([owner('a')], [obj('a')])).inventory()
