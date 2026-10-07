"""Real held EX lock; historical chain fixture remains an explicit test boundary."""
from dataclasses import replace
import os

import pytest

import executor_birth_prepared_root as root
import executor_birth_ownership_chain as chain_module
from executor_birth_secure_fs import _SecureRootSession
from .test_group8_public_history import (
    _prepared, _chain_boundary_fixture, _initial_context_fixture, _rewrite,
)
from . import support

pytestmark = pytest.mark.skipif(os.name == 'nt', reason=support.POSIX_SCENARIO_ONLY_V1)


def historical(tmp_path, monkeypatch, kind):
    if kind == 'initial':
        base, public, chain = _initial_context_fixture(tmp_path, monkeypatch)
        selector = public.material.pin.admission_context_id
    else:
        base = _prepared(tmp_path, monkeypatch)
        chain = _chain_boundary_fixture(base, monkeypatch)
        selector = chain.context_transitions[0].prepared_admission_context_id
    return base, chain, selector


def snapshot(base):
    return {str(p.relative_to(base)): (p.stat().st_mtime_ns, p.read_bytes())
            for p in base.rglob('*') if p.is_file()}


def deny_new_authority(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('historical in-session read reopened, relocked, or entered mutating/private authority')
    monkeypatch.setattr(root, 'open_prepared_root_session_v1', forbidden)
    for name in ('global_lock', 'create_file_exclusive', 'create_directory_exclusive',
                 'rename_no_replace', 'dispose_transaction_object'):
        monkeypatch.setattr(_SecureRootSession, name, forbidden)
    import executor_birth_keystore as keystore
    monkeypatch.setattr(keystore, '_load_birth_keystore_in_session', forbidden)
    original = _SecureRootSession.read_file
    def public_only(session, components, **kwargs):
        assert 'private' not in components and 'keystore.json' not in components
        return original(session, components, **kwargs)
    monkeypatch.setattr(_SecureRootSession, 'read_file', public_only)


@pytest.mark.parametrize('kind', ['target', 'initial'])
def test_held_exclusive_session_reads_exact_public_history_without_effects(tmp_path, monkeypatch, kind):
    base, chain, selector = historical(tmp_path, monkeypatch, kind)
    expected = root.load_historical_context_verifiers_v1(selector)
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            assert session._holds_global_exclusive()
            before = snapshot(base)
            deny_new_authority(monkeypatch)
            result = root.load_historical_context_verifiers_in_session_v1(selector, session)
            assert result == expected
            assert result.required_head_id == chain.required_head.head_id
            assert result.binding_kind == ('transition_target' if kind == 'target' else 'initial_predecessor')
            assert session._holds_global_exclusive()
            assert snapshot(base) == before


@pytest.mark.parametrize('kind', ['target', 'initial'])
@pytest.mark.parametrize('change', ['head', 'transitions', 'records'])
def test_held_session_rejects_historical_frontier_drift(tmp_path, monkeypatch, kind, change):
    _base, chain, selector = historical(tmp_path, monkeypatch, kind)
    after = {'head': replace(chain, heads=(replace(chain.required_head, head_id='sha256:'+'f'*64),)),
             'transitions': replace(chain, context_transitions=()),
             'records': replace(chain, authenticated_records=chain.authenticated_records + (b'changed',))}[change]
    observations = iter((chain, after))
    monkeypatch.setattr(chain_module, 'inspect_ownership_chain_state_v1', lambda: next(observations))
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            deny_new_authority(monkeypatch)
            with pytest.raises(root.PreparedRootError, match='birth_context_selection_changed'):
                root.load_historical_context_verifiers_in_session_v1(selector, session)


@pytest.mark.parametrize('kind', ['target', 'initial'])
def test_held_session_rejects_tampered_historical_set(tmp_path, monkeypatch, kind):
    base, _chain, selector = historical(tmp_path, monkeypatch, kind)
    path = support.installed_set(base) / 'set.json'
    _rewrite(path, lambda document: document.update(prepared_context_epoch='sha256:'+'e'*64))
    from executor_birth_prepared_set import PreparedSetError
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            deny_new_authority(monkeypatch)
            with pytest.raises((PreparedSetError, root.PreparedRootError)):
                root.load_historical_context_verifiers_in_session_v1(selector, session)


def test_session_without_lock_rejected_before_context_observation(tmp_path, monkeypatch):
    _base, _chain, selector = historical(tmp_path, monkeypatch, 'target')
    with root.open_prepared_root_session_v1() as session:
        deny_new_authority(monkeypatch)
        monkeypatch.setattr(chain_module, 'inspect_ownership_chain_state_v1',
                            lambda: pytest.fail('unlocked context observed'))
        with pytest.raises(root.PreparedRootError, match='birth_context_lock_required'):
            root.load_historical_context_verifiers_in_session_v1(selector, session)
