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
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

MARKER_BASENAME_V1 = "prepared-v1.json"
AUTHOR_STORE_BASENAME_V1 = "author-root-v1"
AUTHORITY_SETS_BASENAME_V1 = "authority-sets"
SET_DOCUMENT_BASENAME_V1 = "set.json"
CONTEXT_CONTAINER_BASENAME_V1 = "context"
CONTEXT_MATERIAL_BASENAME_V1 = "material-v1.json"
PREPARED_STATE_V1 = "prepared_not_active"
MAXIMUM_DOCUMENT_BYTES_V1 = 1024 * 1024

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
    provisioner_build_id: str

    def __post_init__(self) -> None:
        if self.state != PREPARED_STATE_V1:
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


def load_prepared_set_v1(session) -> PreparedSetV1:
    """Reopen everything the marker names and compare it with the set.

    A coherent marker does not make an incoherent set authoritative, so the
    marker is only the starting point: what decides is the agreement between
    the bytes on disk and what the set itself declares.
    """
    from executor_birth_keystore import (
        BirthKeyStoreError, _load_birth_keystore_in_session, raw_public_key,
    )
    from executor_birth_secure_fs import BirthSecureFSError, _BirthObjectRole

    def read(components: tuple[str, ...]) -> bytes:
        try:
            return session.read_file(
                components,
                maximum=MAXIMUM_DOCUMENT_BYTES_V1,
                role=_BirthObjectRole.birth_integrity_only,
            )
        except BirthSecureFSError as exc:
            raise PreparedSetError("birth_prepared_set_unavailable", exc) from None

    marker = _decode(read((MARKER_BASENAME_V1,)))
    if set(marker) != MARKER_FIELDS_V1 or marker["schema_version"] != 1:
        raise PreparedSetError("birth_prepared_set_invalid")
    if marker["state"] != PREPARED_STATE_V1:
        raise PreparedSetError("birth_prepared_set_invalid")
    if marker["author_store"] != AUTHOR_STORE_BASENAME_V1:
        raise PreparedSetError("birth_prepared_set_invalid")
    location = (AUTHORITY_SETS_BASENAME_V1, marker["set_id"])
    if marker["authority_set"] != "/".join(location):
        raise PreparedSetError("birth_prepared_set_invalid")

    payload = read(location + (SET_DOCUMENT_BASENAME_V1,))
    if hashlib.sha256(payload).hexdigest() != marker["set_json_sha256"]:
        raise PreparedSetError("birth_prepared_set_mismatch")
    document = _decode(payload)
    if set(document) != SET_FIELDS_V1 or document["schema_version"] != 1:
        raise PreparedSetError("birth_prepared_set_invalid")
    if document["set_id"] != marker["set_id"] or document["state"] != "complete":
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
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")
    if _public_inventory_sha256_v1({
        key_id: raw_public_key(key)
        for key_id, key in author.verifier_keys.items()
    }) != marker["author_store_public_inventory_sha256"]:
        raise PreparedSetError("birth_prepared_set_mismatch")

    material = read(
        location + (CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1)
    )
    if hashlib.sha256(material).hexdigest() != document["context_material_sha256"]:
        raise PreparedSetError("birth_prepared_set_mismatch")
    if marker["context_material_sha256"] != document["context_material_sha256"]:
        raise PreparedSetError("birth_prepared_set_mismatch")
    context = _decode(material)
    if (
        context.get("prepared_admission_context_id")
        != document["prepared_admission_context_id"]
        or context.get("prepared_context_epoch")
        != document["prepared_context_epoch"]
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")

    for registry, digest in (
        (("approval", "authority.json"), "approval_authority_sha256"),
        (("semantic", "authority.json"), "semantic_authority_sha256"),
    ):
        if hashlib.sha256(
            read(location + registry)
        ).hexdigest() != document[digest]:
            raise PreparedSetError("birth_prepared_set_mismatch")

    return PreparedSetV1(
        set_id=document["set_id"],
        state=PREPARED_STATE_V1,
        author_active_key_id=document["author_active_key_id"],
        author_verifier_key_ids=tuple(document["author_verifier_key_ids"]),
        admission_active_key_id=document["admission_active_key_id"],
        producer_keys=document["producer_keys"],
        prepared_admission_context_id=document["prepared_admission_context_id"],
        prepared_context_epoch=document["prepared_context_epoch"],
        context_material_sha256=document["context_material_sha256"],
        provisioner_build_id=document["provisioner_build_id"],
    )


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
