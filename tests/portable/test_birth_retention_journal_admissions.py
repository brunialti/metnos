"""Actual dispatch receipts, native signed history and journal collection."""
from dataclasses import asdict
import os

import pytest

from executor_birth_feedback import dispatch_identifier_reference, make_execution_receipt
from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_inventory import _InventoryComposition
from install.birth_retention_journals import _JournalAdmissionInventory
from install.birth_retention_maintenance import plan
from manifest_inventory import ContractId, ManifestOrigin
from test_birth_retention_artifacts import RUN, OBSERVED, collection
from test_birth_retention_journals import native, record, DAY
from test_birth_retention_signed_store import history, D

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX custody')


@pytest.fixture
def joined(native, history):
    return _JournalAdmissionInventory(journals=native.inventory, signed_store=history.owner,
                                      require_exclusion=lambda: None)


def execution(history, turn, **changes):
    args = dict(request_id=D, turn_id=dispatch_identifier_reference('turn', turn),
        reduced_query_ref=D, arguments={'text': 'è🙂'}, reduced_output={'ok': True},
        contract_id=history.contract, executor_name='historical', candidate_id=D,
        generation_id=history.generation, dispatched_at='2020-01-01T00:00:00Z',
        completed_at='2020-01-01T00:00:01Z')
    args.update(changes)
    value = record(turn)
    value['steps'][0].update(chosen_tool='historical',
                            execution_receipt=asdict(make_execution_receipt(**args)))
    return value


def select(joined, tmp_path):
    return plan(joined.inventory(), observed_owners=frozenset(joined.owners),
        required_owners=frozenset(joined.owners), observed_roots=frozenset(RootKind),
        holds=frozenset(), graph_path=tmp_path / 'graph.sqlite', run_id=RUN,
        observed_at=OBSERVED)


def test_expired_log_is_collectible_while_native_signed_history_is_retained(joined, native, history, tmp_path):
    native.turns.append(execution(history, 'expired'))
    before = {path: path.read_bytes() for path in history.receipts}
    objects = {obj.identity: obj for obj in joined.inventory()}
    turn = objects[native.turns.owner.identity(DAY, 'expired')]
    generation = objects[history.owner.generation_identity(history.contract, history.generation)]
    assert generation.identity in turn.references
    assert turn.identity not in generation.references
    selected = select(joined, tmp_path)
    assert [obj.identity for obj in selected] == [turn.identity]
    _, public, factory = collection(tmp_path, selected, joined.owners)
    assert factory().resume(joined.owners, public_keys=public,
                            max_objects=10, max_seconds=20)['remaining'] == 0
    factory().finish(joined.owners, public_keys=public, verify_recovery=joined.inventory)
    assert native.turns.load('expired') is None
    assert before == {path: path.read_bytes() for path in history.receipts}
    assert history.owner.inventory()


def test_archive_and_live_copies_keep_the_exact_same_generation(joined, native, history):
    value = execution(history, 'copied')
    native.archives.create([value])
    native.turns.append(value)
    generation = history.owner.generation_identity(history.contract, history.generation)
    objects = joined.inventory()
    copies = [obj for obj in objects if obj.identity.owner in {'turn_logs', 'turn_archives'}]
    assert len(copies) == 2 and all(generation in obj.references for obj in copies)
    assert all(set(obj.references) <= {other.identity for other in objects} for obj in objects)


@pytest.mark.parametrize('fault', ['generation', 'candidate', 'contract'])
def test_self_validating_receipt_without_exact_native_admission_refuses_everything(joined, native, history, fault):
    changes = dict(generation_id='sha256:' + 'f' * 64) if fault == 'generation' else (
        dict(candidate_id='sha256:' + 'f' * 64) if fault == 'candidate' else
        dict(contract_id=ContractId(ManifestOrigin.USER, history.contract.relative_manifest)))
    native.turns.append(execution(history, 'unbound', **changes))
    with pytest.raises(RetentionError, match='dispatch historical admission absent'):
        joined.inventory()


def test_corrupt_historical_signature_refuses_the_whole_inventory(joined, native, history):
    native.turns.append(execution(history, 'bound'))
    history.write(history.gen / 'manifest.toml.sig', b'x' * 64)
    with pytest.raises(RetentionError):
        joined.inventory()


def test_missing_execution_receipt_remains_a_native_open_audit(joined, native, history, tmp_path):
    value = execution(history, 'incomplete')
    value['steps'][0]['execution_receipt'] = None
    native.turns.append(value)
    turn = next(obj for obj in joined.inventory() if obj.identity.owner == 'turn_logs')
    assert RootKind.OPEN_AUDIT in turn.roots
    assert not select(joined, tmp_path)


def test_common_journal_and_signed_owners_compose_without_losing_edges(joined, native, history):
    native.turns.append(execution(history, 'shared'))
    inventory = _InventoryComposition(components=(native.inventory, joined),
        required_owners=frozenset(joined.owners), require_exclusion=lambda: None)
    objects = inventory.inventory()
    turn = next(obj for obj in objects if obj.identity.owner == 'turn_logs')
    assert history.owner.generation_identity(history.contract, history.generation) in turn.references


def test_owner_substitution_refuses_before_scan(joined, native, monkeypatch):
    monkeypatch.setattr(native.inventory, 'owners', {**native.inventory.owners,
        'turn_logs': object()})
    with pytest.raises(RetentionError, match='journal admission owner map'):
        joined.inventory()
