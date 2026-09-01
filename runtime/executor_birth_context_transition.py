"""Canonical identity for one RM-0008 authority-context transition.

The signed ownership certificate authenticates ``transition_id``.  This
module gives that identifier one exact byte representation and binds it to
the complete current-generation inventory.  Persistence and selection remain
separate so neither operation can reinterpret the record.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from executor_birth_cutover import CurrentInventoryV1


TRANSITION_ID_DOMAIN_V1 = b"metnos.executor-birth.context-transition-id/v1\0"
CURRENT_INVENTORY_DOMAIN_V1 = b"metnos.executor-birth.current-inventory/v1\0"
MAX_CONTEXT_TRANSITION_BYTES_V1 = 64 * 1024

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_FIELDS_V1 = frozenset({
    "schema_version",
    "transition_id",
    "request_id",
    "closed_build_id",
    "previous_cutover_id",
    "previous_set_id",
    "previous_admission_context_id",
    "previous_context_epoch",
    "set_id",
    "prepared_admission_context_id",
    "prepared_context_epoch",
    "context_material_sha256",
    "set_json_sha256",
    "current_inventory_hash",
})


class ContextTransitionError(RuntimeError):
    """The transition record is malformed or disagrees with its evidence."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ContextTransitionV1:
    transition_id: str
    request_id: str
    closed_build_id: str
    previous_cutover_id: str | None
    previous_set_id: str
    previous_admission_context_id: str
    previous_context_epoch: str
    set_id: str
    prepared_admission_context_id: str
    prepared_context_epoch: str
    context_material_sha256: str
    set_json_sha256: str
    current_inventory_hash: str
    encoded: bytes


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ContextTransitionError(
            "birth_context_transition_invalid", "json",
        ) from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ContextTransitionError(
                "birth_context_transition_invalid", "duplicate key",
            )
        value[key] = item
    return value


def _digest(value: object, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ContextTransitionError("birth_context_transition_invalid", field)
    return value


def _hex_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _HEX_SHA256_RE.fullmatch(value) is None:
        raise ContextTransitionError("birth_context_transition_invalid", field)
    return value


def _inventory_values(
    inventory: CurrentInventoryV1,
) -> list[dict[str, str]]:
    if not isinstance(inventory, CurrentInventoryV1):
        raise ContextTransitionError(
            "birth_context_transition_inventory_invalid", "inventory type",
        )
    try:
        verified = CurrentInventoryV1(inventory.identities)
    except Exception as exc:
        raise ContextTransitionError(
            "birth_context_transition_inventory_invalid", "inventory",
        ) from exc
    return [
        {"contract_id": contract_id, "generation_id": generation_id}
        for contract_id, generation_id in verified.identities
    ]


def current_inventory_hash_v1(inventory: CurrentInventoryV1) -> str:
    """Bind the ordered unique current identities, never their receipt count."""
    encoded = _canonical(_inventory_values(inventory))
    return "sha256:" + hashlib.sha256(
        CURRENT_INVENTORY_DOMAIN_V1 + encoded,
    ).hexdigest()


def _transition_id(value: Mapping[str, object]) -> str:
    unsigned = {
        key: item for key, item in value.items() if key != "transition_id"
    }
    return "sha256:" + hashlib.sha256(
        TRANSITION_ID_DOMAIN_V1 + _canonical(unsigned),
    ).hexdigest()


def issue_context_transition_v1(
    *,
    request_id: str,
    closed_build_id: str,
    previous_cutover_id: str | None,
    previous_set_id: str,
    previous_admission_context_id: str,
    previous_context_epoch: str,
    set_id: str,
    prepared_admission_context_id: str,
    prepared_context_epoch: str,
    context_material_sha256: str,
    set_json_sha256: str,
    current_inventory: CurrentInventoryV1,
) -> tuple[bytes, ContextTransitionV1]:
    """Create and immediately verify the exact canonical transition bytes."""
    inventory_hash = current_inventory_hash_v1(current_inventory)
    value: dict[str, object] = {
        "schema_version": 1,
        "transition_id": None,
        "request_id": request_id,
        "closed_build_id": closed_build_id,
        "previous_cutover_id": previous_cutover_id,
        "previous_set_id": previous_set_id,
        "previous_admission_context_id": previous_admission_context_id,
        "previous_context_epoch": previous_context_epoch,
        "set_id": set_id,
        "prepared_admission_context_id": prepared_admission_context_id,
        "prepared_context_epoch": prepared_context_epoch,
        "context_material_sha256": context_material_sha256,
        "set_json_sha256": set_json_sha256,
        "current_inventory_hash": inventory_hash,
    }
    # Validate every caller-provided field through the single decoder rather
    # than maintaining a second construction-time interpretation.
    value["transition_id"] = _transition_id(value)
    encoded = _canonical(value)
    return encoded, verify_context_transition_v1(
        encoded,
        expected_inventory=current_inventory,
    )


def verify_context_transition_v1(
    encoded: bytes,
    *,
    expected_transition_id: str | None = None,
    expected_inventory: CurrentInventoryV1 | None = None,
) -> ContextTransitionV1:
    """Decode canonical bytes and verify all intrinsic and supplied bindings."""
    if (
        not isinstance(encoded, bytes)
        or not encoded
        or len(encoded) > MAX_CONTEXT_TRANSITION_BYTES_V1
    ):
        raise ContextTransitionError(
            "birth_context_transition_invalid", "record size",
        )
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except ContextTransitionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextTransitionError(
            "birth_context_transition_invalid", "json",
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != _RECORD_FIELDS_V1
        or value.get("schema_version") != 1
        or _canonical(value) != encoded
    ):
        raise ContextTransitionError(
            "birth_context_transition_invalid", "record schema",
        )

    transition_id = _digest(value.get("transition_id"), "transition_id")
    request_id = _digest(value.get("request_id"), "request_id")
    closed_build_id = _digest(value.get("closed_build_id"), "closed_build_id")
    previous_cutover_id = _digest(
        value.get("previous_cutover_id"), "previous_cutover_id", nullable=True,
    )
    previous_set_id = _hex_sha256(value.get("previous_set_id"), "previous_set_id")
    previous_context_id = _digest(
        value.get("previous_admission_context_id"),
        "previous_admission_context_id",
    )
    previous_epoch = _digest(
        value.get("previous_context_epoch"), "previous_context_epoch",
    )
    set_id = _hex_sha256(value.get("set_id"), "set_id")
    prepared_context_id = _digest(
        value.get("prepared_admission_context_id"),
        "prepared_admission_context_id",
    )
    prepared_epoch = _digest(
        value.get("prepared_context_epoch"), "prepared_context_epoch",
    )
    material_hash = _hex_sha256(
        value.get("context_material_sha256"), "context_material_sha256",
    )
    set_hash = _hex_sha256(value.get("set_json_sha256"), "set_json_sha256")
    inventory_hash = _digest(
        value.get("current_inventory_hash"), "current_inventory_hash",
    )

    calculated = _transition_id(value)
    if transition_id != calculated:
        raise ContextTransitionError(
            "birth_context_transition_invalid", "transition_id",
        )
    if expected_transition_id is not None:
        _digest(expected_transition_id, "expected_transition_id")
        if transition_id != expected_transition_id:
            raise ContextTransitionError(
                "birth_context_transition_binding_invalid", "transition_id",
            )
    if (
        expected_inventory is not None
        and inventory_hash != current_inventory_hash_v1(expected_inventory)
    ):
        raise ContextTransitionError(
            "birth_context_transition_binding_invalid", "current inventory",
        )

    assert isinstance(transition_id, str)
    assert isinstance(request_id, str)
    assert isinstance(closed_build_id, str)
    assert previous_cutover_id is None or isinstance(previous_cutover_id, str)
    assert isinstance(previous_context_id, str)
    assert isinstance(previous_epoch, str)
    assert isinstance(prepared_context_id, str)
    assert isinstance(prepared_epoch, str)
    assert isinstance(inventory_hash, str)
    return ContextTransitionV1(
        transition_id=transition_id,
        request_id=request_id,
        closed_build_id=closed_build_id,
        previous_cutover_id=previous_cutover_id,
        previous_set_id=previous_set_id,
        previous_admission_context_id=previous_context_id,
        previous_context_epoch=previous_epoch,
        set_id=set_id,
        prepared_admission_context_id=prepared_context_id,
        prepared_context_epoch=prepared_epoch,
        context_material_sha256=material_hash,
        set_json_sha256=set_hash,
        current_inventory_hash=inventory_hash,
        encoded=bytes(encoded),
    )


def context_transition_basename_v1(transition_id: str) -> str:
    """Return the content-addressed basename for a verified identifier."""
    value = _digest(transition_id, "transition_id")
    assert isinstance(value, str)
    return value.removeprefix("sha256:") + ".json"


__all__ = [
    "CURRENT_INVENTORY_DOMAIN_V1",
    "MAX_CONTEXT_TRANSITION_BYTES_V1",
    "TRANSITION_ID_DOMAIN_V1",
    "ContextTransitionError",
    "ContextTransitionV1",
    "context_transition_basename_v1",
    "current_inventory_hash_v1",
    "issue_context_transition_v1",
    "verify_context_transition_v1",
]
