"""Census integration with native physical inventory; auth boundary is a fixture.

Native public authentication is exercised unchanged in the registered cluster.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from executor_birth_retention import RetentionError
import executor_birth_prepared_root as prepared
import executor_birth_ownership_chain as chains
from install.birth_retention_context_census import _historical_context_census_v1
from tests.portable.rm0008_2b.test_historical_context_session import historical


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    base, chain, selector = historical(tmp_path, monkeypatch, 'target')
    transition = chain.context_transitions[0]
    # This real fixture has one prepared public set. Model two contexts mapping
    # to it solely at the authenticated-chain/public-loader boundary.
    transition = replace(transition, previous_set_id=transition.set_id)
    chain = replace(chain, context_transitions=(transition,))
    monkeypatch.setattr(chains, 'inspect_ownership_chain_state_v1', lambda: chain)
    observed = []
    def load(context, session):
        session._require_exclusive_global_lock()
        observed.append(context)
        return SimpleNamespace(required_head_id=chain.required_head.head_id,
            public_set=SimpleNamespace(set_id=transition.set_id,
                material=SimpleNamespace(pin=SimpleNamespace(admission_context_id=context))))
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_in_session_v1', load)
    with prepared.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            yield session, chain, observed


def census(session):
    return _historical_context_census_v1(session=session, require_exclusion=lambda: None)


def test_complete_includes_initial_without_any_review_or_receipt(scenario):
    session, chain, observed = scenario
    result = census(session)
    transition = chain.context_transitions[0]
    assert set(result) == {transition.previous_admission_context_id, transition.prepared_admission_context_id}
    assert observed == sorted(result)
    with pytest.raises(TypeError):
        result['extra'] = None


@pytest.mark.parametrize('mode', ['extra', 'missing', 'duplicate'])
def test_physical_coverage_rejects_unaccounted_sets(scenario, monkeypatch, mode):
    session, _, _ = scenario
    names = session.inventory(('authority-sets',))
    replacement = {'extra': names + ('f'*64,), 'missing': (), 'duplicate': names + names}[mode]
    monkeypatch.setattr(type(session), 'inventory', lambda self, path: replacement)
    with pytest.raises(RetentionError, match='unaccounted historical authority set'):
        census(session)


def test_namespace_change_after_authentication_is_rejected(scenario, monkeypatch):
    session, _, _ = scenario
    names = session.inventory(('authority-sets',))
    readings = iter((names, names + ('f'*64,)))
    monkeypatch.setattr(type(session), 'inventory', lambda self, path: next(readings))
    with pytest.raises(RetentionError, match='historical context namespace'):
        census(session)


@pytest.mark.parametrize('field', ['head', 'set', 'context'])
def test_wrong_public_binding_is_rejected(scenario, monkeypatch, field):
    session, _, _ = scenario
    original = prepared.load_historical_context_verifiers_in_session_v1
    def changed(*args):
        binding = original(*args)
        if field == 'head': binding.required_head_id = 'sha256:'+'f'*64
        elif field == 'set': binding.public_set.set_id = 'f'*64
        else: binding.public_set.material.pin.admission_context_id = 'sha256:'+'f'*64
        return binding
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_in_session_v1', changed)
    with pytest.raises(RetentionError, match='historical context binding'):
        census(session)


def test_exclusion_loss_rejected_before_public_reads(scenario):
    session, _, observed = scenario
    def lost(): raise RetentionError('retention_exclusion_lost')
    with pytest.raises(RetentionError, match='retention_exclusion_lost'):
        _historical_context_census_v1(session=session, require_exclusion=lost)
    assert not observed


def test_chain_change_after_public_reads_rejected(scenario, monkeypatch):
    session, chain, _ = scenario
    reads = iter((chain, replace(chain, context_transitions=())))
    monkeypatch.setattr(chains, 'inspect_ownership_chain_state_v1', lambda: next(reads))
    with pytest.raises(prepared.PreparedRootError, match='birth_context_selection_changed'):
        census(session)


def test_context_budget_checked_before_reads(scenario, monkeypatch):
    session, _, observed = scenario
    monkeypatch.setattr('install.birth_retention_context_census._MAX_CONTEXTS', 1)
    with pytest.raises(RetentionError, match='historical context budget'):
        census(session)
    assert not observed


def test_distinct_native_public_sets_are_censused_without_loader_substitution(tmp_path, monkeypatch):
    import shutil
    from tests.portable.rm0008_2b.test_group8_public_history import (
        _prepared, _read_public, _chain_boundary_fixture,
    )
    from tests.portable.rm0008_2b import support

    initial_base = _prepared(tmp_path / 'initial', monkeypatch)
    initial = _read_public()
    target_base = _prepared(tmp_path / 'target', monkeypatch)
    target = _read_public()
    assert initial.set_id != target.set_id
    initial_dir = support.installed_set(initial_base)
    target_dir = support.installed_set(target_base)
    shutil.copytree(initial_dir, target_dir.parent / initial.set_id)
    for public_key in (support.installed_author_store(initial_base) / 'public').iterdir():
        shutil.copy2(public_key, support.installed_author_store(target_base) / 'public' / public_key.name)
    chain = _chain_boundary_fixture(target_base, monkeypatch,
        previous_set_id=initial.set_id,
        previous_admission_context_id=initial.material.pin.admission_context_id,
        previous_context_epoch=initial.material.pin.context_epoch)
    # The fixed marker selects the initial predecessor; the authenticated
    # transition selects the target. Only the chain boundary remains a fixture.
    shutil.copyfile(support.installed_marker(initial_base), support.installed_marker(target_base))
    with prepared.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            result = census(session)
            assert {b.public_set.set_id for b in result.values()} == {initial.set_id, target.set_id}
            assert result[initial.material.pin.admission_context_id].binding_kind == 'initial_predecessor'
            assert result[target.material.pin.admission_context_id].binding_kind == 'transition_target'
            assert all(b.required_head_id == chain.required_head.head_id for b in result.values())
