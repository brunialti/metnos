"""Native Birth subgraphs compose without reissuing consent or dropping edges.

This exercises real custody, signed admissions and decisions, epoch transitions
and SQLite consumption. It does not attest complete installed F6 coverage.
"""
import os

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_inventory import _InventoryComposition
from install.birth_retention_epoch_inventory import _EpochInventory
from install.birth_retention_epoch_sqlite import _EpochOwner
from install.birth_retention_semantic_inventory import _SemanticEvidenceInventory
from install.birth_retention_maintenance import plan
from test_birth_retention_admission_links import joined, linked, review, history
from test_birth_retention_epoch_inventory import open_epoch

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX Birth custody')


@pytest.fixture
def composed(joined, tmp_path):
    epochs = _EpochInventory(epochs=_EpochOwner(path=tmp_path / 'epochs.sqlite',
        require_exclusion=lambda: None, owner=None), signed_store=joined.history.owner)
    semantic = _SemanticEvidenceInventory(reviews=joined.linked.review.owner,
        birth_root=joined.linked.base / 'birth', contexts=(joined.linked.subject.admission_context_id,),
        session=joined.linked.session, owner=None, require_exclusion=lambda: None)
    components = (joined.inventory, epochs, semantic)
    owners = frozenset(name for component in components for name in component.owners)
    inventory = _InventoryComposition(components=components, required_owners=owners,
                                      require_exclusion=lambda: None)
    return inventory, epochs, semantic


def test_native_shared_signed_history_preserves_all_approval_and_epoch_edges(composed, joined, tmp_path):
    inventory, epochs, _ = composed
    open_epoch(epochs, joined.history, archived=True)
    before = {path: path.read_bytes() for path in joined.paths}
    objects = inventory.inventory()
    by_id = {obj.identity: obj for obj in objects}
    assert len(by_id) == len(objects)
    approval = next(obj for obj in objects if obj.identity.owner == joined.linked.approvals.name)
    receipts = [obj for obj in objects if obj.identity.node_type == 'admission_receipt']
    epoch = next(obj for obj in objects if obj.identity.owner == epochs.epochs.name)
    generation = by_id[joined.history.owner.generation_identity(joined.history.contract, joined.history.generation)]
    assert len(receipts) == 2
    assert all(approval.identity in receipt.references and receipt.identity in approval.references for receipt in receipts)
    assert epoch.identity in generation.references and generation.identity in epoch.references
    assert approval.state is NodeState.OPEN and RootKind.OPEN_AUDIT in approval.roots
    assert epoch.state is NodeState.CLOSED
    assert all(target in by_id for obj in objects for target in obj.references)
    assert plan(objects, required_owners=frozenset(inventory.owners),
        observed_owners=frozenset(inventory.owners), observed_roots=frozenset(RootKind),
        holds=frozenset(), graph_path=tmp_path / 'graph.sqlite', run_id='sha256:' + 'e' * 64,
        observed_at='2035-01-01T00:00:00Z') == ()
    assert before == {path: path.read_bytes() for path in joined.paths}


def test_missing_native_generation_blocks_whole_composition(composed, joined):
    inventory, epochs, _ = composed
    open_epoch(epochs, joined.history, archived=True, generation='sha256:' + 'f' * 64)
    with pytest.raises(RetentionError, match='epoch generation absent'):
        inventory.inventory()


def test_historical_approval_corruption_blocks_whole_composition(composed, joined):
    joined.linked.update(decision_signature='00' * 64)
    with pytest.raises(RetentionError, match='historical approval authentication'):
        composed[0].inventory()


def test_empty_semantic_owner_is_still_observed(composed):
    inventory, _, semantic = composed
    assert semantic.evidence
    result = inventory.inventory()
    assert all(owner.name in inventory.owners for owner in semantic.evidence.values())
    assert {obj.identity.owner for obj in result} < set(inventory.owners)


def test_shared_owner_substitution_blocks_whole_composition(composed, joined, monkeypatch):
    inventory, epochs, _ = composed
    monkeypatch.setattr(epochs, 'owners', {**epochs.owners,
        joined.history.owner.name: object()})
    with pytest.raises(RetentionError, match='component owner map changed'):
        inventory.inventory()
