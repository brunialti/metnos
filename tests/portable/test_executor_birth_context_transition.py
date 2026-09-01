"""Portable adversarial checks for the F4 context-transition identity."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from executor_birth_context_transition import (
    CURRENT_INVENTORY_DOMAIN_V1,
    TRANSITION_ID_DOMAIN_V1,
    ContextTransitionError,
    context_transition_basename_v1,
    current_inventory_hash_v1,
    issue_context_transition_v1,
    verify_context_transition_v1,
)
from executor_birth_cutover import CurrentInventoryV1, CurrentReceiptProof


def D(character: str) -> str:
    return "sha256:" + character * 64


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _proof(*names: str) -> CurrentReceiptProof:
    identities = tuple(sorted(
        (f"explicit:{name}/manifest.toml", D(name[0])) for name in names
    ))
    return CurrentReceiptProof(
        identities,
        {identity: D(chr(ord("f") - offset))
         for offset, identity in enumerate(identities)},
    )


def _inventory(*names: str) -> CurrentInventoryV1:
    return _proof(*names).inventory


def _issue(inventory: CurrentInventoryV1 | None = None):
    return issue_context_transition_v1(
        request_id=D("1"),
        closed_build_id=D("2"),
        previous_cutover_id=None,
        previous_set_id="3" * 64,
        previous_admission_context_id=D("4"),
        previous_context_epoch=D("5"),
        set_id="6" * 64,
        prepared_admission_context_id=D("7"),
        prepared_context_epoch=D("8"),
        context_material_sha256="9" * 64,
        set_json_sha256="a" * 64,
        current_inventory=inventory or _inventory("alpha"),
    )


@pytest.mark.parametrize("names", [(), ("alpha",), ("charlie", "alpha", "bravo")])
def test_zero_one_many_inventory_and_transition_are_independently_reproducible(names):
    inventory_snapshot = _inventory(*names)
    encoded, transition = _issue(inventory_snapshot)
    inventory = [
        {"contract_id": contract_id, "generation_id": generation_id}
        for contract_id, generation_id in inventory_snapshot.identities
    ]
    expected_inventory = "sha256:" + hashlib.sha256(
        CURRENT_INVENTORY_DOMAIN_V1 + _canonical(inventory),
    ).hexdigest()
    value = json.loads(encoded)
    unsigned = {key: item for key, item in value.items() if key != "transition_id"}
    expected_transition = "sha256:" + hashlib.sha256(
        TRANSITION_ID_DOMAIN_V1 + _canonical(unsigned),
    ).hexdigest()

    assert transition.current_inventory_hash == expected_inventory
    assert current_inventory_hash_v1(inventory_snapshot) == expected_inventory
    assert transition.transition_id == expected_transition
    assert transition.encoded == encoded == _canonical(value)
    assert verify_context_transition_v1(
        encoded,
        expected_transition_id=expected_transition,
        expected_inventory=inventory_snapshot,
    ) == transition
    assert context_transition_basename_v1(expected_transition) == (
        expected_transition.removeprefix("sha256:") + ".json"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(extra=True),
        lambda value: value.pop("set_id"),
        lambda value: value.update(schema_version=True),
        lambda value: value.update(set_id=D("6")),
        lambda value: value.update(context_material_sha256=D("9")),
        lambda value: value.update(previous_cutover_id=""),
    ],
)
def test_closed_schema_and_identifier_grammars_reject_alternate_forms(mutate):
    encoded, _transition = _issue()
    value = json.loads(encoded)
    mutate(value)
    with pytest.raises(ContextTransitionError, match="birth_context_transition_invalid"):
        verify_context_transition_v1(_canonical(value))


def test_duplicate_noncanonical_and_oversized_records_are_rejected():
    encoded, _transition = _issue()
    duplicate = encoded[:-1] + b',"schema_version":1}'
    for changed in (encoded + b"\n", duplicate, b"x" * (64 * 1024 + 1)):
        with pytest.raises(ContextTransitionError, match="birth_context_transition_invalid"):
            verify_context_transition_v1(changed)


def test_recomputed_record_still_refuses_wrong_external_bindings():
    encoded, transition = _issue(_inventory("alpha", "bravo"))
    value = json.loads(encoded)
    value["current_inventory_hash"] = current_inventory_hash_v1(
        _inventory("alpha"),
    )
    unsigned = {key: item for key, item in value.items() if key != "transition_id"}
    value["transition_id"] = "sha256:" + hashlib.sha256(
        TRANSITION_ID_DOMAIN_V1 + _canonical(unsigned),
    ).hexdigest()
    changed = _canonical(value)

    with pytest.raises(
        ContextTransitionError,
        match="birth_context_transition_binding_invalid",
    ):
        verify_context_transition_v1(
            changed,
            expected_inventory=_inventory("alpha", "bravo"),
        )
    with pytest.raises(
        ContextTransitionError,
        match="birth_context_transition_binding_invalid",
    ):
        verify_context_transition_v1(
            encoded,
            expected_transition_id=replace(
                transition,
                transition_id=D("f"),
            ).transition_id,
        )


def test_receipt_hashes_do_not_change_the_identity_inventory():
    proof = _proof("alpha", "bravo")
    changed = CurrentReceiptProof(
        proof.identities,
        {identity: D("0") for identity in proof.identities},
    )
    assert current_inventory_hash_v1(changed.inventory) == current_inventory_hash_v1(
        proof.inventory,
    )
