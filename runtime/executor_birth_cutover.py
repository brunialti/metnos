"""Transactional F4 ownership cutover for current Executor generations.

The coordinator owns no signing authority.  It runs under the independently
proved maintenance barrier, enumerates authenticated current generations,
asks the sealed Birth runtime to reattest only those missing a valid receipt,
and closes legacy owners only after a stable second census proves complete
receipt coverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Callable, ContextManager, Iterable, Mapping

from manifest_inventory import ManifestRef


class BirthCutoverError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class CurrentGeneration:
    ref: ManifestRef
    generation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ManifestRef):
            raise BirthCutoverError("birth_cutover_inventory_invalid")
        if not isinstance(self.generation_id, str) or re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.generation_id,
        ) is None:
            raise BirthCutoverError("birth_cutover_generation_invalid")

    @property
    def identity(self) -> tuple[str, str]:
        return self.ref.contract_id.value, self.generation_id


@dataclass(frozen=True, slots=True)
class CurrentInventoryV1:
    identities: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        try:
            identities = tuple(self.identities)
            ordered = tuple(sorted(identities))
            unique = len(identities) == len(set(identities))
        except (TypeError, ValueError) as exc:
            raise BirthCutoverError("birth_cutover_inventory_invalid") from exc
        if identities != ordered or not unique:
            raise BirthCutoverError("birth_cutover_inventory_invalid")
        for identity in identities:
            if not isinstance(identity, tuple) or len(identity) != 2:
                raise BirthCutoverError("birth_cutover_inventory_invalid")
            contract_id, generation_id = identity
            if (
                not isinstance(contract_id, str) or not contract_id or "\x00" in contract_id
                or re.fullmatch(r"sha256:[0-9a-f]{64}", generation_id) is None
            ):
                raise BirthCutoverError("birth_cutover_inventory_invalid")
        object.__setattr__(self, "identities", identities)


@dataclass(frozen=True, slots=True)
class CurrentReceiptProof:
    identities: tuple[tuple[str, str], ...]
    receipt_hashes: Mapping[tuple[str, str], str]

    def __post_init__(self) -> None:
        inventory = CurrentInventoryV1(self.identities)
        try:
            hashes = dict(self.receipt_hashes)
            matching_keys = set(hashes) == set(inventory.identities)
        except (TypeError, ValueError) as exc:
            raise BirthCutoverError(
                "birth_cutover_receipt_binding_invalid",
            ) from exc
        if not matching_keys:
            raise BirthCutoverError("birth_cutover_receipt_binding_invalid")
        for identity in inventory.identities:
            if (
                not isinstance(hashes[identity], str)
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}", hashes[identity],
                ) is None
            ):
                raise BirthCutoverError("birth_cutover_receipt_binding_invalid")
        object.__setattr__(self, "identities", inventory.identities)
        object.__setattr__(
            self, "receipt_hashes", MappingProxyType(dict(sorted(hashes.items()))),
        )

    @property
    def inventory(self) -> CurrentInventoryV1:
        return CurrentInventoryV1(self.identities)


@dataclass(frozen=True, slots=True)
class BirthCutoverReport:
    current_count: int
    already_receipted: int
    reattested: int
    proof: CurrentReceiptProof
    legacy_owners_closed: bool


def _digest(encoded: bytes) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _census(items: Iterable[CurrentGeneration]) -> tuple[CurrentGeneration, ...]:
    materialized = tuple(items)
    if any(not isinstance(item, CurrentGeneration) for item in materialized):
        raise BirthCutoverError("birth_cutover_inventory_invalid")
    result = tuple(sorted(materialized, key=lambda item: item.identity))
    identities = tuple(item.identity for item in result)
    if len(identities) != len(set(identities)):
        raise BirthCutoverError("birth_cutover_inventory_duplicate")
    return result


def freeze_current_inventory_v1(
    items: Iterable[CurrentGeneration],
) -> CurrentInventoryV1:
    """Freeze only authenticated current identities, before receipts exist."""
    return CurrentInventoryV1(tuple(item.identity for item in _census(items)))


def _verified_receipt(
    item: CurrentGeneration,
    encoded: bytes | None,
    verify_receipt: Callable[[bytes], object],
    *,
    require_reattestation: bool = False,
) -> bytes | None:
    if encoded is None:
        return None
    if not isinstance(encoded, bytes):
        raise BirthCutoverError("birth_cutover_receipt_invalid", item.identity[0])
    try:
        receipt = verify_receipt(encoded)
    except Exception as exc:
        raise BirthCutoverError(
            "birth_cutover_receipt_invalid", item.identity[0],
        ) from exc
    kind = getattr(getattr(receipt, "kind", None), "value", getattr(receipt, "kind", None))
    revision = getattr(
        getattr(receipt, "revision_class", None), "value",
        getattr(receipt, "revision_class", None),
    )
    if (
        getattr(receipt, "contract_id", None) != item.identity[0]
        or getattr(receipt, "generation_id", None) != item.generation_id
        or kind not in {"admission", "reattestation"}
        or (require_reattestation and kind != "reattestation")
        or (kind == "reattestation" and revision != "reattestation")
    ):
        raise BirthCutoverError("birth_cutover_receipt_binding_invalid", item.identity[0])
    return encoded


def cutover_current_generations(
    *,
    maintenance_guard: Callable[[], ContextManager[tuple[Callable[[], bool], object]]],
    enumerate_current: Callable[[], Iterable[CurrentGeneration]],
    read_receipt: Callable[[CurrentGeneration], bytes | None],
    reattest_via_birth: Callable[[CurrentGeneration], bytes],
    verify_receipt: Callable[[bytes], object],
    close_legacy_owners: Callable[[CurrentReceiptProof], bool],
) -> BirthCutoverReport:
    """Perform the non-partial F4 ownership transition under maintenance.

    Reattestation may durably add receipts before a later failure.  That is
    harmless and intentionally resumable; legacy owners are not closed until
    the final stable-census proof has been accepted atomically by their owner.
    """
    with maintenance_guard() as guarded:
        if not isinstance(guarded, tuple) or len(guarded) != 2 or not callable(guarded[0]):
            raise BirthCutoverError("birth_cutover_maintenance_invalid")
        prove_quiescent = guarded[0]
        prepared = prepare_current_receipt_proof(
            prove_quiescent=prove_quiescent,
            enumerate_current=enumerate_current,
            read_receipt=read_receipt,
            reattest_via_birth=reattest_via_birth,
            verify_receipt=verify_receipt,
        )
        proof = prepared.proof
        if close_legacy_owners(proof) is not True:
            raise BirthCutoverError("birth_cutover_legacy_close_failed")
        return BirthCutoverReport(
            prepared.current_count, prepared.already_receipted,
            prepared.reattested, proof, True,
        )


def prepare_current_receipt_proof(
    *, prove_quiescent: Callable[[], bool],
    enumerate_current: Callable[[], Iterable[CurrentGeneration]],
    read_receipt: Callable[[CurrentGeneration], bytes | None],
    reattest_via_birth: Callable[[CurrentGeneration], bytes],
    verify_receipt: Callable[[bytes], object],
) -> BirthCutoverReport:
    """Prepare and reread complete current-receipt proof without closing owners."""
    if any(not callable(value) for value in (
        prove_quiescent, enumerate_current, read_receipt,
        reattest_via_birth, verify_receipt,
    )):
        raise BirthCutoverError("birth_cutover_input_invalid")
    if prove_quiescent() is not True:
        raise BirthCutoverError("birth_cutover_not_quiescent")
    before = _census(enumerate_current())
    already = 0
    reattested = 0
    for item in before:
        if prove_quiescent() is not True:
            raise BirthCutoverError("birth_cutover_not_quiescent")
        encoded = _verified_receipt(item, read_receipt(item), verify_receipt)
        if encoded is None:
            try:
                encoded = reattest_via_birth(item)
            except Exception as exc:
                raise BirthCutoverError(
                    "birth_cutover_reattestation_failed", item.identity[0],
                ) from exc
            encoded = _verified_receipt(
                item, encoded, verify_receipt, require_reattestation=True,
            )
            if encoded is None:
                raise BirthCutoverError(
                    "birth_cutover_reattestation_missing", item.identity[0],
                )
            durable = _verified_receipt(
                item, read_receipt(item), verify_receipt,
                require_reattestation=True,
            )
            if durable != encoded:
                raise BirthCutoverError(
                    "birth_cutover_receipt_not_durable", item.identity[0],
                )
            reattested += 1
        else:
            already += 1

    if prove_quiescent() is not True:
        raise BirthCutoverError("birth_cutover_not_quiescent")
    after = _census(enumerate_current())
    if tuple(item.identity for item in after) != tuple(item.identity for item in before):
        raise BirthCutoverError("birth_cutover_inventory_changed")
    hashes: dict[tuple[str, str], str] = {}
    for item in after:
        encoded = _verified_receipt(item, read_receipt(item), verify_receipt)
        if encoded is None:
            raise BirthCutoverError("birth_cutover_receipt_missing", item.identity[0])
        hashes[item.identity] = _digest(encoded)
    proof = CurrentReceiptProof(
        tuple(item.identity for item in after),
        MappingProxyType(dict(sorted(hashes.items()))),
    )
    return BirthCutoverReport(len(after), already, reattested, proof, False)


def enumerate_authenticated_current_generations(
    *, trusted_publics: Iterable[object], store_root: Path | str | None = None,
) -> tuple[CurrentGeneration, ...]:
    """Production census: authenticate every binding and current revision."""
    from contract_store import ContractRetirement, VerifiedManifest, current_contract
    from manifest_inventory import inventory_store_manifests

    inventory = inventory_store_manifests(store_root=store_root)
    if inventory.problems:
        raise BirthCutoverError("birth_cutover_inventory_invalid")
    result: list[CurrentGeneration] = []
    for ref in inventory.manifests:
        revision = current_contract(
            ref, trusted_publics=tuple(trusted_publics), store_root=store_root,
        )
        if isinstance(revision, ContractRetirement):
            continue
        if not isinstance(revision, VerifiedManifest) or revision.generation_id is None:
            raise BirthCutoverError("birth_cutover_generation_invalid", ref.contract_id.value)
        result.append(CurrentGeneration(ref, revision.generation_id))
    return _census(result)


__all__ = [
    "BirthCutoverError", "BirthCutoverReport", "CurrentGeneration",
    "CurrentInventoryV1", "CurrentReceiptProof", "cutover_current_generations",
    "enumerate_authenticated_current_generations", "prepare_current_receipt_proof",
    "freeze_current_inventory_v1",
]
