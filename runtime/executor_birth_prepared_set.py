"""Reading back a prepared authority set, under its own barrier (group 3).

Group 2 leaves an inactive set on disk.  Group 3 may not take the marker at
its word: it reopens author root, set, registries and context material, and
compares every digest with what the set declares before anything is activated.
``prepared_not_active`` never becomes ``active`` by being read.

The reader receives an already open read session.  It opens nothing itself,
so the authority to reach the filesystem stays where it was granted.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

MARKER_BASENAME_V1 = "prepared-v1.json"
AUTHOR_STORE_BASENAME_V1 = "author-root-v1"
AUTHORITY_SETS_BASENAME_V1 = "authority-sets"
SET_DOCUMENT_BASENAME_V1 = "set.json"
CONTEXT_CONTAINER_BASENAME_V1 = "context"
CONTEXT_MATERIAL_BASENAME_V1 = "material-v1.json"
SET_ID_DIGEST_DOMAIN_V1 = b"metnos.executor-birth.authority-set/v1\0"
PREPARED_STATE_V1 = "prepared_not_active"
MAXIMUM_DOCUMENT_BYTES_V1 = 1024 * 1024
_PREPARED_SET_SEAL_V1 = object()
_PREPARED_SET_BINDING_DOMAIN_V1 = (
    b"metnos.executor-birth.prepared-set-readback/v1\0"
)
_PREPARED_SET_BINDING_FIELDS_V1 = (
    "set_id",
    "state",
    "author_active_key_id",
    "author_verifier_key_ids",
    "admission_active_key_id",
    "producer_keys",
    "prepared_admission_context_id",
    "prepared_context_epoch",
    "context_material_sha256",
    "set_json_sha256",
    "provisioning_transaction_id",
    "provisioner_build_id",
)
_PREPARED_AUTHORITY_SET_DIGEST_DOMAIN_V2 = (
    b"metnos.executor-birth.prepared-authority-set/v2\0"
)
_PREPARED_AUTHORITY_SET_SEAL_V2 = object()
_PREPARED_AUTHORITY_SET_FIELDS_V2 = (
    "transaction_id", "provisioner_build_id", "request_id",
    "closed_build_id", "distribution_payload_hash",
    "distribution_signature_hash", "previous_set_id", "target_set_id",
    "target_admission_context_id", "target_context_epoch",
    "target_context_material_sha256", "target_set_json_sha256",
    "source_inventory_hash", "material_plan_sha256",
    "verified_checkpoint_sha256",
)

MARKER_FIELDS_V1 = frozenset({
    "schema_version", "state", "set_id", "authority_set", "author_store",
    "author_store_public_inventory_sha256", "set_json_sha256",
    "context_material_sha256", "provisioner_build_id", "transaction_id",
})

SET_FIELDS_V1 = frozenset({
    "schema_version", "state", "provisioning_transaction_id",
    "provisioner_build_id", "author_active_key_id", "author_verifier_key_ids",
    "admission_active_key_id", "admission_verifier_key_ids", "producer_keys",
    "approval_authority_sha256", "semantic_authority_sha256",
    "sandbox_registry_sha256",
    "semantic_public_key_ids", "approval_input_sha256", "semantic_input_sha256",
    "producer_catalog_sha256", "context_source_inventory_sha256",
    "prepared_admission_context_id", "prepared_context_epoch",
    "context_material_sha256", "set_id",
})


class PreparedSetError(RuntimeError):
    """The prepared set cannot be trusted; it is never repaired here."""

    def __init__(self, code: str, cause: BaseException | None = None) -> None:
        self.code = code
        self._internal_cause = cause
        super().__init__(code)
        self.__suppress_context__ = True

    @property
    def __cause__(self) -> None:
        return None

    @__cause__.setter
    def __cause__(self, value: BaseException | None) -> None:
        if value is not None and self._internal_cause is None:
            self._internal_cause = value


def _prepared_authority_digest_v2(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
    )


def _prepared_authority_hex_v2(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class PreparedAuthoritySetV2:
    """Public identities of one exact, verified and still staged V2 set."""

    transaction_id: str
    provisioner_build_id: str
    request_id: str
    closed_build_id: str
    distribution_payload_hash: str
    distribution_signature_hash: str
    previous_set_id: str
    target_set_id: str
    target_admission_context_id: str
    target_context_epoch: str
    target_context_material_sha256: str
    target_set_json_sha256: str
    source_inventory_hash: str
    material_plan_sha256: str
    verified_checkpoint_sha256: str
    _artifact_binding: bytes = field(repr=False)
    _seal: object = field(repr=False)

    def __post_init__(self) -> None:
        if (
            self._seal is not _PREPARED_AUTHORITY_SET_SEAL_V2
            or not _prepared_authority_hex_v2(self.transaction_id, 32)
            or not isinstance(self.provisioner_build_id, str)
            or not self.provisioner_build_id
            or any(not _prepared_authority_digest_v2(value) for value in (
                self.request_id, self.closed_build_id,
                self.distribution_payload_hash,
                self.distribution_signature_hash,
                self.target_admission_context_id, self.target_context_epoch,
                self.source_inventory_hash,
            ))
            or any(not _prepared_authority_hex_v2(value, 64) for value in (
                self.previous_set_id, self.target_set_id,
                self.target_context_material_sha256,
                self.target_set_json_sha256, self.material_plan_sha256,
                self.verified_checkpoint_sha256,
            ))
            or self._artifact_binding
            != _prepared_authority_set_binding_v2(self)
        ):
            raise PreparedSetError("birth_provisioning_transaction_conflict")


def _prepared_authority_set_binding_v2(
    value: PreparedAuthoritySetV2 | Mapping[str, object],
) -> bytes:
    document = {
        field_name: (
            value[field_name]
            if isinstance(value, Mapping)
            else getattr(value, field_name)
        )
        for field_name in _PREPARED_AUTHORITY_SET_FIELDS_V2
    }
    try:
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PreparedSetError(
            "birth_provisioning_transaction_conflict", exc,
        ) from None
    return hashlib.sha256(
        _PREPARED_AUTHORITY_SET_DIGEST_DOMAIN_V2 + encoded,
    ).digest()


def is_prepared_authority_set_v2(value: object) -> bool:
    """Recognize only a result minted after complete staged read-back."""
    if (
        not isinstance(value, PreparedAuthoritySetV2)
        or value._seal is not _PREPARED_AUTHORITY_SET_SEAL_V2
    ):
        return False
    try:
        return value._artifact_binding == _prepared_authority_set_binding_v2(
            value,
        )
    except PreparedSetError:
        return False


@dataclass(frozen=True, slots=True)
class PreparedSetV1:
    """What one verified, still inactive authority set declares."""

    set_id: str
    state: str
    author_active_key_id: str
    author_verifier_key_ids: tuple[str, ...]
    admission_active_key_id: str
    producer_keys: Mapping[str, Mapping[str, object]]
    prepared_admission_context_id: str
    prepared_context_epoch: str
    context_material_sha256: str
    set_json_sha256: str
    provisioning_transaction_id: str
    provisioner_build_id: str
    _artifact_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _PREPARED_SET_SEAL_V1
            or self.state != PREPARED_STATE_V1
            or self._artifact_binding != _prepared_set_artifact_binding_v1({
                field: getattr(self, field)
                for field in _PREPARED_SET_BINDING_FIELDS_V1
            })
        ):
            raise PreparedSetError("birth_prepared_set_invalid")
        object.__setattr__(
            self, "producer_keys", MappingProxyType(dict(self.producer_keys))
        )


def _decode(raw: bytes) -> dict[str, object]:
    """Read one canonical document and refuse a non-canonical encoding."""
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparedSetError("birth_prepared_set_invalid", exc) from None
    if not isinstance(value, dict) or json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") != raw:
        raise PreparedSetError("birth_prepared_set_invalid")
    return value


def _read_integrity_document_v1(session, components: tuple[str, ...]) -> bytes:
    from executor_birth_secure_fs import BirthSecureFSError, _BirthObjectRole

    try:
        return session.read_file(
            components,
            maximum=MAXIMUM_DOCUMENT_BYTES_V1,
            role=_BirthObjectRole.birth_integrity_only,
        )
    except BirthSecureFSError as exc:
        raise PreparedSetError("birth_prepared_set_unavailable", exc) from None


def load_authority_set_v1(
    session,
    set_id: str,
    *,
    expected_set_json_sha256: str | None = None,
    expected_transaction_id: str | None = None,
    expected_context_material_sha256: str | None = None,
    expected_author_inventory_sha256: str | None = None,
) -> PreparedSetV1:
    """Read back one exact immutable set without consulting the V1 marker."""
    from executor_birth_keystore import (
        BirthKeyStoreError, _load_birth_keystore_in_session, raw_public_key,
    )
    from executor_birth_secure_fs import BirthSecureFSError

    if (
        not isinstance(set_id, str)
        or len(set_id) != 64
        or any(character not in "0123456789abcdef" for character in set_id)
    ):
        raise PreparedSetError("birth_prepared_set_invalid")
    location = (AUTHORITY_SETS_BASENAME_V1, set_id)
    payload = _read_integrity_document_v1(
        session, location + (SET_DOCUMENT_BASENAME_V1,),
    )
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    if (
        expected_set_json_sha256 is not None
        and payload_sha256 != expected_set_json_sha256
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")
    document = _decode(payload)
    if set(document) != SET_FIELDS_V1 or document["schema_version"] != 1:
        raise PreparedSetError("birth_prepared_set_invalid")
    if document["set_id"] != set_id or document["state"] != "complete":
        raise PreparedSetError("birth_prepared_set_mismatch")
    unsigned = dict(document)
    unsigned.pop("set_id")
    calculated_set_id = hashlib.sha256(
        SET_ID_DIGEST_DOMAIN_V1 + json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if calculated_set_id != set_id:
        raise PreparedSetError("birth_prepared_set_mismatch")
    if (
        expected_transaction_id is not None
        and document["provisioning_transaction_id"] != expected_transaction_id
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")

    try:
        author = _load_birth_keystore_in_session(
            (AUTHOR_STORE_BASENAME_V1,), session,
        )
        admission = _load_birth_keystore_in_session(
            location + ("admission",), session,
        )
    except (BirthKeyStoreError, BirthSecureFSError) as exc:
        raise PreparedSetError("birth_prepared_set_unavailable", exc) from None
    if (
        author.active_key_id != document["author_active_key_id"]
        or sorted(author.verifier_keys) != document["author_verifier_key_ids"]
        or admission.active_key_id != document["admission_active_key_id"]
        or sorted(admission.verifier_keys)
        != document["admission_verifier_key_ids"]
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")
    author_inventory_sha256 = _public_inventory_sha256_v1({
        key_id: raw_public_key(key)
        for key_id, key in author.verifier_keys.items()
    })
    if (
        expected_author_inventory_sha256 is not None
        and author_inventory_sha256 != expected_author_inventory_sha256
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")

    material = _read_integrity_document_v1(
        session,
        location + (CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1)
    )
    if hashlib.sha256(material).hexdigest() != document["context_material_sha256"]:
        raise PreparedSetError("birth_prepared_set_mismatch")
    if (
        expected_context_material_sha256 is not None
        and expected_context_material_sha256
        != document["context_material_sha256"]
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")
    context = _decode(material)
    if (
        context.get("prepared_admission_context_id")
        != document["prepared_admission_context_id"]
        or context.get("prepared_context_epoch")
        != document["prepared_context_epoch"]
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")

    from executor_birth_sandbox_registry_v1 import (
        SANDBOX_CONTAINER_BASENAME_V1, SANDBOX_REGISTRY_BASENAME_V1,
    )

    for registry, digest in (
        (("approval", "authority.json"), "approval_authority_sha256"),
        (("semantic", "authority.json"), "semantic_authority_sha256"),
        (
            (SANDBOX_CONTAINER_BASENAME_V1, SANDBOX_REGISTRY_BASENAME_V1),
            "sandbox_registry_sha256",
        ),
    ):
        if hashlib.sha256(
            _read_integrity_document_v1(session, location + registry)
        ).hexdigest() != document[digest]:
            raise PreparedSetError("birth_prepared_set_mismatch")

    prepared_values = dict(
        set_id=document["set_id"],
        state=PREPARED_STATE_V1,
        author_active_key_id=document["author_active_key_id"],
        author_verifier_key_ids=tuple(document["author_verifier_key_ids"]),
        admission_active_key_id=document["admission_active_key_id"],
        producer_keys=document["producer_keys"],
        prepared_admission_context_id=document["prepared_admission_context_id"],
        prepared_context_epoch=document["prepared_context_epoch"],
        context_material_sha256=document["context_material_sha256"],
        set_json_sha256=payload_sha256,
        provisioning_transaction_id=document["provisioning_transaction_id"],
        provisioner_build_id=document["provisioner_build_id"],
    )
    return PreparedSetV1(
        **prepared_values,
        _artifact_binding=_prepared_set_artifact_binding_v1(prepared_values),
        _seal=_PREPARED_SET_SEAL_V1,
    )


def load_prepared_set_v1(session) -> PreparedSetV1:
    """Reopen the exact set selected by the historical V1 marker."""
    marker = _decode(
        _read_integrity_document_v1(session, (MARKER_BASENAME_V1,)),
    )
    if (
        set(marker) != MARKER_FIELDS_V1
        or marker["schema_version"] != 1
        or marker["state"] != PREPARED_STATE_V1
        or marker["author_store"] != AUTHOR_STORE_BASENAME_V1
    ):
        raise PreparedSetError("birth_prepared_set_invalid")
    location = (AUTHORITY_SETS_BASENAME_V1, marker["set_id"])
    if marker["authority_set"] != "/".join(location):
        raise PreparedSetError("birth_prepared_set_invalid")
    return load_authority_set_v1(
        session,
        marker["set_id"],
        expected_set_json_sha256=marker["set_json_sha256"],
        expected_transaction_id=marker["transaction_id"],
        expected_context_material_sha256=marker["context_material_sha256"],
        expected_author_inventory_sha256=(
            marker["author_store_public_inventory_sha256"]
        ),
    )


def is_prepared_set_v1(value: object) -> bool:
    """Recognize only a set emitted after the complete read-back above."""
    if (
        not isinstance(value, PreparedSetV1)
        or value._seal is not _PREPARED_SET_SEAL_V1
    ):
        return False
    try:
        expected = _prepared_set_artifact_binding_v1({
            field: getattr(value, field)
            for field in _PREPARED_SET_BINDING_FIELDS_V1
        })
    except PreparedSetError:
        return False
    return value._artifact_binding == expected


def _prepared_set_artifact_binding_v1(values: Mapping[str, object]) -> bytes:
    """Bind the complete result so copied seals cannot bless altered fields."""
    if set(values) != set(_PREPARED_SET_BINDING_FIELDS_V1):
        raise PreparedSetError("birth_prepared_set_invalid")

    def plain(item: object) -> object:
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [plain(child) for child in item]
        return item

    try:
        encoded = json.dumps(
            plain(values),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PreparedSetError("birth_prepared_set_invalid", exc) from None
    return hashlib.sha256(
        _PREPARED_SET_BINDING_DOMAIN_V1 + encoded,
    ).digest()


AUTHOR_STORE_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.author-store-public-inventory/v1\0"
)


def _public_inventory_sha256_v1(publics: Mapping[str, bytes]) -> str:
    """The same ring digest the provisioner recorded, rebuilt from the store."""
    return hashlib.sha256(
        AUTHOR_STORE_DIGEST_DOMAIN_V1 + json.dumps(
            [
                {
                    "key_id": key_id,
                    "sha256": hashlib.sha256(publics[key_id]).hexdigest(),
                }
                for key_id in sorted(publics)
            ],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def read_document_v1(session, components: tuple[str, ...]) -> bytes:
    """Read one integrity document of the layout through a held session."""
    from executor_birth_secure_fs import BirthSecureFSError, _BirthObjectRole

    try:
        return session.read_file(
            components,
            maximum=MAXIMUM_DOCUMENT_BYTES_V1,
            role=_BirthObjectRole.birth_integrity_only,
        )
    except BirthSecureFSError as exc:
        raise PreparedSetError("birth_prepared_set_unavailable", exc) from None


def authority_registry_v1(session, base: tuple[str, ...]) -> dict[str, object]:
    """The public identities of one set, read back from its own stores.

    Only public material appears here: identifiers, public bytes, scopes and
    states.  A private key never reaches the registry, the context material or
    ``set.json`` (section 9.2).  The installer builds this to describe what it
    prepared; the runtime rebuilds it to check that description, so there is
    one implementation and not two.
    """
    from executor_birth_approval_authority import _load_approval_authority_in_session
    from executor_birth_keystore import _load_birth_keystore_in_session, raw_public_key
    from executor_birth_secure_fs import BirthSecureFSError

    def store(components: tuple[str, ...]) -> dict[str, object]:
        loaded = _load_birth_keystore_in_session(components, session)
        return {
            "active_key_id": loaded.active_key_id,
            "verifier_key_ids": sorted(loaded.verifier_keys),
            "public_keys": {
                key_id: raw_public_key(key).hex()
                for key_id, key in sorted(loaded.verifier_keys.items())
            },
        }

    try:
        producer_names = sorted(session.inventory(base + ("producers",)))
    except BirthSecureFSError as exc:
        raise PreparedSetError("birth_prepared_set_unavailable", exc) from None
    producers = {
        name: store(base + ("producers", name)) for name in producer_names
    }
    approval = _load_approval_authority_in_session(
        base + ("approval", "authority.json"), session,
    )
    semantic = _decode(read_document_v1(session, base + ("semantic", "authority.json")))
    return {
        "admission": store(base + ("admission",)),
        "producers": producers,
        "approval": {
            "revision": approval.revision,
            "keys": {
                key_id: raw_public_key(key).hex()
                for key_id, key in sorted(approval.keys.items())
            },
            "actors": {
                actor: {
                    "key_ids": sorted(entry["key_ids"]),
                    "scopes": sorted(entry["scopes"]),
                }
                for actor, entry in sorted(approval.actors.items())
            },
        },
        "semantic": {
            key_id: spec["status"]
            for key_id, spec in sorted(semantic["verifiers"].items())
        },
    }
