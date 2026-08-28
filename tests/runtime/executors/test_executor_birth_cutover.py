from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from executor_birth_cutover import (
    BirthCutoverError, CurrentGeneration, cutover_current_generations,
    prepare_current_receipt_proof,
)
from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus


def _item(name: str, generation: str | None = None) -> CurrentGeneration:
    relative = f"{name}/manifest.toml"
    cid = ContractId(ManifestOrigin.EXPLICIT, relative)
    ref = ManifestRef(
        cid, ManifestOrigin.EXPLICIT, ManifestStatus.ADMITTED, Path("/source"),
        Path("/source") / relative, relative, (Path("/source") / name,),
    )
    return CurrentGeneration(ref, generation or ("sha256:" + name[0] * 64))


def _receipt(item: CurrentGeneration, *, kind: str = "reattestation") -> bytes:
    return f"{item.identity[0]}\n{item.generation_id}\n{kind}".encode()


def _verify(encoded: bytes):
    contract, generation, kind = encoded.decode().splitlines()
    return SimpleNamespace(
        contract_id=contract, generation_id=generation, kind=kind,
        revision_class="reattestation" if kind == "reattestation" else "code_revision",
    )


class Rig:
    def __init__(self, items, receipts=None):
        self.items = list(items)
        self.receipts = dict(receipts or {})
        self.reattested = []
        self.closed = []
        self.proofs = 0

    @contextmanager
    def guard(self):
        def proof():
            self.proofs += 1
            return True
        yield proof, {"stopped": True}

    def enumerate(self):
        return tuple(self.items)

    def read(self, item):
        return self.receipts.get(item.identity)

    def reattest(self, item):
        encoded = _receipt(item)
        self.receipts[item.identity] = encoded
        self.reattested.append(item.identity)
        return encoded

    def close(self, proof):
        self.closed.append(proof)
        return True

    def run(self):
        return cutover_current_generations(
            maintenance_guard=self.guard, enumerate_current=self.enumerate,
            read_receipt=self.read, reattest_via_birth=self.reattest,
            verify_receipt=_verify, close_legacy_owners=self.close,
        )


def test_zero_current_generations_closes_with_empty_complete_proof():
    rig = Rig([])
    result = rig.run()
    assert (result.current_count, result.already_receipted, result.reattested) == (0, 0, 0)
    assert result.proof.identities == ()
    assert len(rig.closed) == 1


def test_one_current_generation_is_reattested_durably_before_close():
    item = _item("alpha")
    rig = Rig([item])
    result = rig.run()
    assert result.reattested == 1
    assert rig.reattested == [item.identity]
    assert result.proof.identities == (item.identity,)
    assert len(rig.closed) == 1


def test_receipt_preparation_never_closes_legacy_owners():
    item = _item("alpha")
    rig = Rig([item])
    result = prepare_current_receipt_proof(
        prove_quiescent=lambda: True,
        enumerate_current=rig.enumerate, read_receipt=rig.read,
        reattest_via_birth=rig.reattest, verify_receipt=_verify,
    )
    assert result.proof.identities == (item.identity,)
    assert result.legacy_owners_closed is False
    assert rig.closed == []


def test_many_current_generations_include_already_receipted_exactly_once():
    items = [_item("charlie"), _item("alpha"), _item("bravo")]
    rig = Rig(items, {items[1].identity: _receipt(items[1], kind="admission")})
    result = rig.run()
    assert (result.current_count, result.already_receipted, result.reattested) == (3, 1, 2)
    assert result.proof.identities == tuple(sorted(item.identity for item in items))
    assert rig.reattested == [items[2].identity, items[0].identity]


def test_mid_census_failure_preserves_legacy_owners_and_resume_is_idempotent():
    items = [_item("alpha"), _item("bravo"), _item("charlie")]
    rig = Rig(items)
    successful = rig.reattest
    calls = 0
    def fail_second(item):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected")
        return successful(item)
    rig.reattest = fail_second
    with pytest.raises(BirthCutoverError, match="birth_cutover_reattestation_failed"):
        rig.run()
    assert rig.closed == []
    assert set(rig.receipts) == {items[0].identity}

    rig.reattest = successful
    resumed = rig.run()
    assert (resumed.already_receipted, resumed.reattested) == (1, 2)
    assert len(rig.closed) == 1
    again = rig.run()
    assert (again.already_receipted, again.reattested) == (3, 0)
    assert len(rig.closed) == 2


def test_changed_final_census_never_closes_legacy_owners():
    first, added = _item("alpha"), _item("bravo")
    rig = Rig([first])
    calls = 0
    def enumerate_changed():
        nonlocal calls
        calls += 1
        return (first,) if calls == 1 else (first, added)
    rig.enumerate = enumerate_changed
    with pytest.raises(BirthCutoverError, match="birth_cutover_inventory_changed"):
        rig.run()
    assert rig.closed == []


def test_returned_but_not_durable_receipt_never_closes():
    item = _item("alpha")
    rig = Rig([item])
    rig.reattest = lambda current: _receipt(current)
    with pytest.raises(BirthCutoverError, match="birth_cutover_receipt_not_durable"):
        rig.run()
    assert rig.closed == []


def test_missing_receipt_cannot_be_filled_by_an_ordinary_admission():
    item = _item("alpha")
    rig = Rig([item])
    def wrong_kind(current):
        encoded = _receipt(current, kind="admission")
        rig.receipts[current.identity] = encoded
        return encoded
    rig.reattest = wrong_kind
    with pytest.raises(BirthCutoverError, match="birth_cutover_receipt_binding_invalid"):
        rig.run()
    assert rig.closed == []


def test_wrong_or_non_reattestation_receipt_fails_closed():
    item = _item("alpha")
    wrong = _item("bravo")
    rig = Rig([item], {item.identity: _receipt(wrong)})
    with pytest.raises(BirthCutoverError, match="birth_cutover_receipt_binding_invalid"):
        rig.run()
    assert rig.closed == []
    rig = Rig([item], {item.identity: _receipt(item, kind="reattestation")})
    def bad_verify(encoded):
        value = _verify(encoded)
        value.revision_class = "equivalent_republish"
        return value
    with pytest.raises(BirthCutoverError, match="birth_cutover_receipt_binding_invalid"):
        cutover_current_generations(
            maintenance_guard=rig.guard, enumerate_current=rig.enumerate,
            read_receipt=rig.read, reattest_via_birth=rig.reattest,
            verify_receipt=bad_verify, close_legacy_owners=rig.close,
        )


def test_failed_quiescence_or_close_is_reported_without_success():
    rig = Rig([])
    @contextmanager
    def false_guard():
        yield (lambda: False), {}
    with pytest.raises(BirthCutoverError, match="birth_cutover_not_quiescent"):
        cutover_current_generations(
            maintenance_guard=false_guard, enumerate_current=rig.enumerate,
            read_receipt=rig.read, reattest_via_birth=rig.reattest,
            verify_receipt=_verify, close_legacy_owners=rig.close,
        )
    rig.close = lambda proof: False
    with pytest.raises(BirthCutoverError, match="birth_cutover_legacy_close_failed"):
        rig.run()
