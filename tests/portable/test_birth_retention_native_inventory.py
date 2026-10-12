"""Native owner assembly under an existing, authenticated Birth session.

This exercises native schemas, signatures and shared owner construction. Host
path custody and complete installed namespace coverage have separate oracles.
"""
import os
from pathlib import Path

import pytest

if os.name != 'posix':
    pytest.skip('native Linux/POSIX F6 owner composition', allow_module_level=True)

import executor_birth_prepared_root as prepared
from executor_birth_retention import RetentionError
from install.birth_retention_native_inventory import _compose_native_inventory_v1, _observed_paths_v1
from install.birth_retention_path_probe import PATH_KEYS
from loader import Catalog
from tests.portable.rm0008_2b.test_historical_context_session import historical, snapshot


def observed(tmp_path):
    paths = {name: str(tmp_path / 'stopped' / name) for name in PATH_KEYS}
    paths['protected_undo_key'] = str(tmp_path / 'stopped' / 'config' / 'admin.key')
    for group in (('history_backups', 'history_legacy', 'history_expanded_backups'),
                  ('PATH_TURNS', 'turn_feedback_turns'),
                  ('skill_admission_audit', 'skills_cli_audit'),
                  ('PATH_DURABLE_ARTIFACTS', 'lre_artifact_store')):
        for name in group:
            paths[name] = paths[group[0]]
    return paths


@pytest.mark.skipif(os.name != 'posix', reason='native held Birth session')
def test_native_owner_assembly_is_readonly_and_shares_exact_join_owners(tmp_path, monkeypatch):
    base, _, selector = historical(tmp_path, monkeypatch, 'target')
    paths = observed(tmp_path)
    with prepared.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            binding, = prepared.load_historical_context_verifiers_for_contexts_in_session_v1((selector,), session)
            before = snapshot(base)
            composition = _compose_native_inventory_v1(paths=paths, account_owner=(os.geteuid(), os.getegid()),
                birth_root=base / 'birth', session=session, context_bindings={selector: binding},
                catalog=Catalog(), reviews_root=tmp_path / 'reviews', require_exclusion=lambda: None)
            assert snapshot(base) == before
            assert not (tmp_path / 'stopped').exists()
            assert len(composition.owners) >= 30
            assert {'signed_contract_history', 'durable_workloads', 'birth_producer_receipts', 'birth_approvals',
                    'change_intents', 'change_rollback_blobs', 'promoter_review_audit', 'telos_filtered',
                    'turn_logs', 'turn_archives'} <= set(composition.owners)
            for name, shared in composition.owners.items():
                assert all(component.owners[name] is shared for component in composition.components
                           if name in component.owners)
            archive = composition.owners['turn_archives']
            assert archive.root == Path(paths['turn_archives'])


@pytest.mark.parametrize('fault', ['missing', 'foreign', 'relative', 'alias'])
def test_native_path_set_must_be_exact_and_shared_paths_reconciled(tmp_path, fault):
    paths = observed(tmp_path)
    if fault == 'missing': paths.pop('workloads')
    elif fault == 'foreign': paths['foreign'] = str(tmp_path)
    elif fault == 'relative': paths['workloads'] = 'workloads.sqlite'
    else: paths['history_legacy'] = str(tmp_path / 'different')
    with pytest.raises(RetentionError):
        _observed_paths_v1(paths)
