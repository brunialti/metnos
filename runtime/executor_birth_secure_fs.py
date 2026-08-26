"""Handle-bound filesystem primitives for Executor Birth.

This module deliberately contains no provisioning policy and no key handling.
It supplies the small, closed set of low-level operations used by the Birth
loaders and, in later increments, by the installer-owned provisioner.
"""
from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import math
import os
import stat
import sys
import threading
import time
import unicodedata
import weakref
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator, Sequence


_MAX_COMPONENT_BYTES = 256
_MAX_RELATIVE_BYTES = 1024
_LOCK_BYTE = b"0"
_LOCK_DELAYS = (0.005, 0.010, 0.020, 0.040, 0.080, 0.100)


class BirthSecureFSError(RuntimeError):
    """Stable public failure without paths, ACLs or platform diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _ObjectIdentity:
    volume: str
    object_id: str


class _ObjectKind(str, Enum):
    """The two shapes the shared record may represent.

    A symbolic link, a reparse point, a hard link or any other type is refused
    before the record is built, so refusal is a fact that precedes the
    representation rather than a third value of it (section 16.3, R7).
    """

    regular_file = "regular_file"
    directory = "directory"


class _BirthObjectRole(str, Enum):
    birth_confidential = "birth_confidential"
    birth_integrity_only = "birth_integrity_only"
    historical_private = "historical_private"
    historical_public = "historical_public"


class _DisposalClass(str, Enum):
    complete_file = "complete_file"
    partial_pending_file = "partial_pending_file"
    empty_directory = "empty_directory"


class _BirthRolePatternV1(str, Enum):
    """Closed grammar of the provisioning layout (section 16.13.4).

    The declaration order is normative: the installer builds the productive
    catalogue with ``tuple(_BirthRolePatternV1)`` and no caller may omit,
    reorder or add a pattern.
    """

    birth_root = "birth_root"
    global_lock = "global_lock"
    transaction_root = "transaction_root"
    transaction_header = "transaction_header"
    transaction_header_pending = "transaction_header_pending"
    transaction_prepared = "transaction_prepared"
    transaction_checkpoints = "transaction_checkpoints"
    transaction_checkpoint = "transaction_checkpoint"
    transaction_checkpoint_pending = "transaction_checkpoint_pending"
    transaction_author_store = "transaction_author_store"
    transaction_authority_set = "transaction_authority_set"
    final_author_store = "final_author_store"
    authority_sets = "authority_sets"
    final_authority_set = "final_authority_set"
    final_prepared = "final_prepared"
    set_document = "set_document"
    admission_store = "admission_store"
    producers_container = "producers_container"
    producer_store = "producer_store"
    approval_container = "approval_container"
    approval_authority = "approval_authority"
    semantic_container = "semantic_container"
    semantic_authority = "semantic_authority"
    semantic_public_container = "semantic_public_container"
    semantic_public_key = "semantic_public_key"
    semantic_evidence_container = "semantic_evidence_container"
    semantic_evidence_record = "semantic_evidence_record"
    context_container = "context_container"
    context_material = "context_material"
    keystore_config = "keystore_config"
    keystore_lock = "keystore_lock"
    keystore_private_container = "keystore_private_container"
    keystore_private_key = "keystore_private_key"
    keystore_public_container = "keystore_public_container"
    keystore_public_key = "keystore_public_key"
    operator_input = "operator_input"
    operator_approval = "operator_approval"
    operator_semantic = "operator_semantic"
    operator_semantic_public = "operator_semantic_public"
    operator_semantic_public_key = "operator_semantic_public_key"
    payload_pending = "payload_pending"


class _BirthRoleBindingOriginV1(str, Enum):
    CATALOG = "catalog"
    OVERLAY_RESERVED = "overlay_reserved"
    OVERLAY_COMMITTED = "overlay_committed"


@dataclass(frozen=True, slots=True)
class _BirthRoleBindingV1:
    components: tuple[str, ...]
    kind: _ObjectKind
    role: _BirthObjectRole


@dataclass(frozen=True, slots=True)
class _ResolvedBirthRoleBindingV1:
    binding: _BirthRoleBindingV1
    origin: _BirthRoleBindingOriginV1


_TRANSACTION_PREFIX = ".birth-provisioning-v1.txn."
_HEADER_PENDING_PREFIX = ".transaction-v1.pending."
_CHECKPOINT_PENDING_PREFIX = ".checkpoint-pending-"
_PAYLOAD_PENDING_PREFIX = ".payload-pending-"
_KEY_PREFIX = "birth-ed25519-v1-sha256-"
_PRODUCER_PREFIX = "p-"
_CHECKPOINT_SEQUENCE_MAXIMUM = 8191
_NAME_DOMAIN = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_HEX_DOMAIN = frozenset("0123456789abcdef")

_FILE = _ObjectKind.regular_file
_DIRECTORY = _ObjectKind.directory
_INTEGRITY = _BirthObjectRole.birth_integrity_only
_CONFIDENTIAL = _BirthObjectRole.birth_confidential


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(char in _HEX_DOMAIN for char in value)


def _is_sequence_component(value: str) -> bool:
    if len(value) != 20 or not value.isdigit() or not value.isascii():
        return False
    return int(value) <= _CHECKPOINT_SEQUENCE_MAXIMUM


def _is_catalogued_name(value: str, suffix: str) -> bool:
    if not 1 <= len(value) <= 128 or not value.isascii():
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    if any(char not in _NAME_DOMAIN for char in value):
        return False
    return value.endswith(suffix) and len(value) > len(suffix)


def _transaction_id(components: tuple[str, ...]) -> str | None:
    """Return the nonce when ``components`` starts with a transaction root."""
    if not components or not components[0].startswith(_TRANSACTION_PREFIX):
        return None
    nonce = components[0][len(_TRANSACTION_PREFIX):]
    return nonce if _is_hex(nonce, 32) else None


def _authority_set_tail(
    components: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Strip the two admitted ``A`` anchors and return the remaining tail.

    ``A`` expands only to the final ``authority-sets/<sid>`` or to the staged
    ``<transaction>/authority-set``; every other prefix is outside the closed
    grammar.
    """
    if (
        len(components) >= 2
        and components[0] == "authority-sets"
        and _is_hex(components[1], 64)
    ):
        return components[2:]
    if (
        len(components) >= 2
        and _transaction_id(components[:1]) is not None
        and components[1] == "authority-set"
    ):
        return components[2:]
    return None


def _keystore_tail(components: tuple[str, ...]) -> tuple[str, ...] | None:
    """Strip one of the six admitted keystore anchors."""
    if components[:1] == ("author-root-v1",):
        return components[1:]
    if (
        len(components) >= 2
        and _transaction_id(components[:1]) is not None
        and components[1] == "author-root-v1"
    ):
        return components[2:]
    tail = _authority_set_tail(components)
    if tail is None:
        return None
    if tail[:1] == ("admission",):
        return tail[1:]
    if (
        len(tail) >= 2
        and tail[0] == "producers"
        and tail[1].startswith(_PRODUCER_PREFIX)
        and _is_hex(tail[1][len(_PRODUCER_PREFIX):], 64)
    ):
        return tail[2:]
    return None


def _keystore_row(
    components: tuple[str, ...], tail: tuple[str, ...],
) -> tuple[_ObjectKind, _BirthObjectRole] | None:
    if tail == ("keystore.json",) or tail == ("birth-keystore.lock",):
        return (_FILE, _CONFIDENTIAL)
    if tail == ("private",):
        return (_DIRECTORY, _CONFIDENTIAL)
    if tail == ("public",):
        return (_DIRECTORY, _INTEGRITY)
    if len(tail) == 2 and tail[0] in {"private", "public"}:
        suffix = ".key" if tail[0] == "private" else ".pub"
        name = tail[1]
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            if stem.startswith(_KEY_PREFIX) and _is_hex(
                stem[len(_KEY_PREFIX):], 64
            ):
                return (
                    (_FILE, _CONFIDENTIAL)
                    if tail[0] == "private"
                    else (_FILE, _INTEGRITY)
                )
    return None


def _authority_row(
    tail: tuple[str, ...],
) -> tuple[_ObjectKind, _BirthObjectRole] | None:
    if tail == ("set.json",):
        return (_FILE, _INTEGRITY)
    if tail == ("admission",):
        return (_DIRECTORY, _CONFIDENTIAL)
    if tail == ("producers",):
        return (_DIRECTORY, _INTEGRITY)
    if (
        len(tail) == 2
        and tail[0] == "producers"
        and tail[1].startswith(_PRODUCER_PREFIX)
        and _is_hex(tail[1][len(_PRODUCER_PREFIX):], 64)
    ):
        return (_DIRECTORY, _CONFIDENTIAL)
    if tail in {("approval",), ("semantic",), ("context",)}:
        return (_DIRECTORY, _INTEGRITY)
    if tail == ("approval", "authority.json"):
        return (_FILE, _INTEGRITY)
    if tail == ("semantic", "authority.json"):
        return (_FILE, _INTEGRITY)
    if tail in {("semantic", "public"), ("semantic", "evidence")}:
        return (_DIRECTORY, _INTEGRITY)
    if len(tail) == 3 and tail[0] == "semantic":
        if tail[1] == "public" and _is_catalogued_name(tail[2], ".pub"):
            return (_FILE, _INTEGRITY)
        if tail[1] == "evidence" and _is_catalogued_name(tail[2], ".json"):
            return (_FILE, _INTEGRITY)
    if tail == ("context", "material-v1.json"):
        return (_FILE, _INTEGRITY)
    return None


def _transaction_row(
    components: tuple[str, ...], nonce: str,
) -> tuple[_ObjectKind, _BirthObjectRole] | None:
    tail = components[1:]
    if not tail:
        return (_DIRECTORY, _INTEGRITY)
    if tail in {("transaction-v1.json",), ("prepared-v1.json",)}:
        return (_FILE, _INTEGRITY)
    if tail == (_HEADER_PENDING_PREFIX + nonce,):
        return (_FILE, _INTEGRITY)
    if tail == ("checkpoints-v1",):
        return (_DIRECTORY, _INTEGRITY)
    if len(tail) == 2 and tail[0] == "checkpoints-v1":
        name = tail[1]
        if name.endswith(".json") and _is_sequence_component(name[: -len(".json")]):
            return (_FILE, _INTEGRITY)
        if name.startswith(_CHECKPOINT_PENDING_PREFIX) and name.endswith(
            "-" + nonce
        ):
            middle = name[len(_CHECKPOINT_PENDING_PREFIX): -len("-" + nonce)]
            if _is_sequence_component(middle):
                return (_FILE, _INTEGRITY)
    if tail == ("author-root-v1",):
        return (_DIRECTORY, _CONFIDENTIAL)
    if tail == ("authority-set",):
        return (_DIRECTORY, _INTEGRITY)
    return None


def _matching_rows(
    components: tuple[str, ...],
) -> tuple[tuple[_BirthRolePatternV1, _ObjectKind, _BirthObjectRole], ...]:
    """Evaluate every row of the closed table over the whole sequence.

    Rows are evaluated independently so an overlap is visible to the caller:
    identical results coalesce, different ones are a contradiction.
    """
    P = _BirthRolePatternV1
    rows: list[tuple[_BirthRolePatternV1, _ObjectKind, _BirthObjectRole]] = []

    def add(pattern, kind, role) -> None:
        rows.append((pattern, kind, role))

    if not components:
        add(P.birth_root, _DIRECTORY, _INTEGRITY)
        return tuple(rows)
    if components == ("provisioning-v1.lock",):
        add(P.global_lock, _FILE, _INTEGRITY)
    if components == ("author-root-v1",):
        add(P.final_author_store, _DIRECTORY, _CONFIDENTIAL)
    if components == ("authority-sets",):
        add(P.authority_sets, _DIRECTORY, _INTEGRITY)
    if components == ("prepared-v1.json",):
        add(P.final_prepared, _FILE, _INTEGRITY)
    if components == ("operator-input-v1",):
        add(P.operator_input, _DIRECTORY, _INTEGRITY)
    if components == ("operator-input-v1", "approval-authority.json"):
        add(P.operator_approval, _FILE, _INTEGRITY)
    if components == ("operator-input-v1", "semantic-authority.json"):
        add(P.operator_semantic, _FILE, _INTEGRITY)
    if components == ("operator-input-v1", "semantic-public"):
        add(P.operator_semantic_public, _DIRECTORY, _INTEGRITY)
    if (
        len(components) == 3
        and components[:2] == ("operator-input-v1", "semantic-public")
        and _is_catalogued_name(components[2], ".pub")
    ):
        add(P.operator_semantic_public_key, _FILE, _INTEGRITY)
    if (
        len(components) == 2
        and components[0] == "authority-sets"
        and _is_hex(components[1], 64)
    ):
        add(P.final_authority_set, _DIRECTORY, _INTEGRITY)

    keystore_tail = _keystore_tail(components)
    if keystore_tail is not None:
        keystore = _keystore_row(components, keystore_tail)
        if keystore is not None:
            kind, role = keystore
            if keystore_tail == ("keystore.json",):
                add(P.keystore_config, kind, role)
            elif keystore_tail == ("birth-keystore.lock",):
                add(P.keystore_lock, kind, role)
            elif keystore_tail == ("private",):
                add(P.keystore_private_container, kind, role)
            elif keystore_tail == ("public",):
                add(P.keystore_public_container, kind, role)
            elif keystore_tail[0] == "private":
                add(P.keystore_private_key, kind, role)
            else:
                add(P.keystore_public_key, kind, role)

    authority_tail = _authority_set_tail(components)
    if authority_tail is not None:
        authority = _authority_row(authority_tail)
        if authority is not None:
            kind, role = authority
            head = authority_tail[0]
            if authority_tail == ("set.json",):
                add(P.set_document, kind, role)
            elif authority_tail == ("admission",):
                add(P.admission_store, kind, role)
            elif authority_tail == ("producers",):
                add(P.producers_container, kind, role)
            elif head == "producers":
                add(P.producer_store, kind, role)
            elif authority_tail == ("approval",):
                add(P.approval_container, kind, role)
            elif authority_tail == ("approval", "authority.json"):
                add(P.approval_authority, kind, role)
            elif authority_tail == ("semantic",):
                add(P.semantic_container, kind, role)
            elif authority_tail == ("semantic", "authority.json"):
                add(P.semantic_authority, kind, role)
            elif authority_tail == ("semantic", "public"):
                add(P.semantic_public_container, kind, role)
            elif authority_tail == ("semantic", "evidence"):
                add(P.semantic_evidence_container, kind, role)
            elif authority_tail[:2] == ("semantic", "public"):
                add(P.semantic_public_key, kind, role)
            elif authority_tail[:2] == ("semantic", "evidence"):
                add(P.semantic_evidence_record, kind, role)
            elif authority_tail == ("context",):
                add(P.context_container, kind, role)
            else:
                add(P.context_material, kind, role)

    nonce = _transaction_id(components)
    if nonce is not None:
        transaction = _transaction_row(components, nonce)
        if transaction is not None:
            kind, role = transaction
            tail = components[1:]
            if not tail:
                add(P.transaction_root, kind, role)
            elif tail == ("transaction-v1.json",):
                add(P.transaction_header, kind, role)
            elif tail == ("prepared-v1.json",):
                add(P.transaction_prepared, kind, role)
            elif tail == (_HEADER_PENDING_PREFIX + nonce,):
                add(P.transaction_header_pending, kind, role)
            elif tail == ("checkpoints-v1",):
                add(P.transaction_checkpoints, kind, role)
            elif tail == ("author-root-v1",):
                add(P.transaction_author_store, kind, role)
            elif tail == ("authority-set",):
                add(P.transaction_authority_set, kind, role)
            elif tail[1].endswith(".json"):
                add(P.transaction_checkpoint, kind, role)
            else:
                add(P.transaction_checkpoint_pending, kind, role)
    return tuple(rows)


def _pending_payload_parent(
    components: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Return the parent of an admitted payload pending name.

    The pending inherits the role of its parent, so the tail must already be
    classifiable under the same transaction and must not be another pending.
    """
    if len(components) < 3:
        return None
    nonce = _transaction_id(components)
    if nonce is None:
        return None
    name = components[-1]
    if not name.startswith(_PAYLOAD_PENDING_PREFIX) or not name.endswith(
        "-" + nonce
    ):
        return None
    middle = name[len(_PAYLOAD_PENDING_PREFIX): -len("-" + nonce)]
    if not _is_sequence_component(middle):
        return None
    return components[:-1]


@dataclass(frozen=True, slots=True)
class _BirthRoleCatalogV1:
    schema_version: int
    patterns: tuple[_BirthRolePatternV1, ...]
    exact_bindings: tuple[_BirthRoleBindingV1, ...]
    generation: int

    def __post_init__(self) -> None:
        if (
            self.schema_version != 1
            or type(self.generation) is not int
            or isinstance(self.generation, bool)
            or self.generation < 0
            or not isinstance(self.patterns, tuple)
            or not isinstance(self.exact_bindings, tuple)
        ):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        if any(
            not isinstance(item, _BirthRolePatternV1) for item in self.patterns
        ) or len(set(self.patterns)) != len(self.patterns):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        keys: list[tuple[tuple[str, ...], str, str]] = []
        for binding in self.exact_bindings:
            if (
                not isinstance(binding, _BirthRoleBindingV1)
                or not isinstance(binding.kind, _ObjectKind)
                or not isinstance(binding.role, _BirthObjectRole)
            ):
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
            _relative_components(binding.components)
            keys.append(
                (binding.components, binding.kind.value, binding.role.value)
            )
        # The three defects are distinguished: an order that is not canonical
        # is a malformed input, the same binding twice is an ambiguous
        # resolution, and the same components with a different kind or role
        # contradict each other (section 16.13.4).
        # A contradiction is reported before a merely unordered list: two
        # results for the same components is the graver defect and must not be
        # masked by the canonical-order check (section 16.13.4).
        by_components: dict[tuple[str, ...], set[tuple[str, str]]] = {}
        for components, kind, role in keys:
            by_components.setdefault(components, set()).add((kind, role))
        if any(len(values) > 1 for values in by_components.values()):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        if len(set(keys)) != len(keys):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        if list(sorted(keys)) != keys:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")

    def _resolve_binding_v1(
        self, components: tuple[str, ...],
    ) -> _BirthRoleBindingV1:
        """Resolve one canonical sequence through the closed table.

        Mode, owner, DACL, an isolated suffix and the JSON content never decide
        the role: they only verify a role that is already resolved.
        """
        components = _relative_components(components)
        enabled = frozenset(self.patterns)
        results = {
            (kind, role)
            for pattern, kind, role in _matching_rows(components)
            if pattern in enabled
        }
        if _BirthRolePatternV1.payload_pending in enabled:
            parent = _pending_payload_parent(components)
            if parent is not None:
                inherited = {
                    (kind, role)
                    for pattern, kind, role in _matching_rows(parent)
                    if pattern in enabled and kind is _ObjectKind.directory
                }
                if len(inherited) == 1:
                    results.add((_FILE, next(iter(inherited))[1]))
        exact = {
            (binding.kind, binding.role)
            for binding in self.exact_bindings
            if binding.components == components
        }
        if len(results) > 1 or len(exact) > 1:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        if results and exact and results != exact:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        resolved = exact or results
        if not resolved:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        kind, role = next(iter(resolved))
        return _BirthRoleBindingV1(components=components, kind=kind, role=role)


@dataclass(frozen=True, slots=True)
class _InventoryEntry:
    name: str
    identity: _ObjectIdentity
    kind: _ObjectKind
    role: _BirthObjectRole
    links: int
    size: int | None


@dataclass(frozen=True, slots=True)
class _DispositionResult:
    identity: _ObjectIdentity
    kind: _ObjectKind
    removed: bool = True


@dataclass(frozen=True, slots=True)
class _DisposalExpectation:
    components: tuple[str, ...]
    identity: _ObjectIdentity
    kind: _ObjectKind
    role: _BirthObjectRole
    disposal_class: _DisposalClass
    links: int
    expected_size: int | None
    maximum_partial_size: int | None
    content_sha256: str | None
    inventory: tuple[_InventoryEntry, ...] | None

    def __post_init__(self) -> None:
        if not isinstance(self.components, tuple):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        if not isinstance(self.identity, _ObjectIdentity):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        if not isinstance(self.kind, _ObjectKind) or not isinstance(
            self.role, _BirthObjectRole
        ) or not isinstance(self.disposal_class, _DisposalClass):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        if isinstance(self.links, bool) or not isinstance(self.links, int) or self.links < 1:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        digest = self.content_sha256
        if isinstance(digest, str) and len(digest) == 64:
            try:
                int(digest, 16)
            except ValueError:
                pass
            else:
                object.__setattr__(self, "content_sha256", "sha256:" + digest)
        # The admitted combinations are closed: an expectation that mixes two
        # classes is refused before the name is opened (section 16.13.2).
        if not self.components:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        _relative_components(self.components)
        if self.inventory is not None and (
            not isinstance(self.inventory, tuple)
            or any(
                not isinstance(item, _InventoryEntry) for item in self.inventory
            )
        ):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        if self.disposal_class is _DisposalClass.complete_file:
            valid = (
                self.kind is _ObjectKind.regular_file
                and self.links == 1
                and isinstance(self.expected_size, int)
                and not isinstance(self.expected_size, bool)
                and self.expected_size >= 0
                and self.maximum_partial_size is None
                and _is_canonical_digest(self.content_sha256)
                and self.inventory is None
            )
        elif self.disposal_class is _DisposalClass.partial_pending_file:
            valid = (
                self.kind is _ObjectKind.regular_file
                and self.links == 1
                and self.expected_size is None
                and isinstance(self.maximum_partial_size, int)
                and not isinstance(self.maximum_partial_size, bool)
                and self.maximum_partial_size >= 0
                and self.content_sha256 is None
                and self.inventory is None
            )
        else:
            valid = (
                self.kind is _ObjectKind.directory
                and self.links == (1 if os.name == "nt" else 2)
                and self.expected_size is None
                and self.maximum_partial_size is None
                and self.content_sha256 is None
                and self.inventory == ()
            )
        if not valid:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")


def _is_canonical_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    return _is_hex(value[len("sha256:"):], 64)


class _InventoryBudgetV1:
    __slots__ = ("_seen",)

    limit = 4096

    def __init__(self) -> None:
        self._seen: set[tuple[tuple[str, ...], _ObjectIdentity]] = set()

    def include(self, path: tuple[str, ...], identity: _ObjectIdentity) -> None:
        key = (path, identity)
        if key in self._seen:
            return
        if len(self._seen) >= self.limit:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        self._seen.add(key)


@dataclass(frozen=True, slots=True)
class _PlatformIdentity:
    posix_uid: int | None
    windows_service_sid: str | None

    def __post_init__(self) -> None:
        if self.posix_uid is not None and (
            isinstance(self.posix_uid, bool) or self.posix_uid < 0
        ):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        sid = self.windows_service_sid
        if sid is not None and (
            not isinstance(sid, str)
            or not sid.startswith("S-1-")
            or sid.casefold()
            in {"s-1-5-18", "s-1-5-32-544", "s-1-5-11"}
        ):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class _AuthenticatedRootDescriptor:
    handles: tuple[int, ...]
    root_path: str
    identity: _PlatformIdentity
    role_catalog: _BirthRoleCatalogV1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.handles, tuple)
            or not self.handles
            or any(isinstance(item, bool) or not isinstance(item, int) for item in self.handles)
            or not isinstance(self.root_path, str)
            or not self.root_path
            or not isinstance(self.identity, _PlatformIdentity)
            or not isinstance(self.role_catalog, _BirthRoleCatalogV1)
        ):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")


def _relative_components(value: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    total = 0
    result: list[str] = []
    for component in value:
        if not isinstance(component, str) or not component:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        if component != unicodedata.normalize("NFC", component):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        stem = component.split(".", 1)[0].casefold()
        reserved = stem in {"con", "prn", "aux", "nul"}
        reserved = reserved or (
            len(stem) == 4
            and stem[:3] in {"com", "lpt"}
            and stem[3] in "123456789"
        )
        if (
            component in {".", ".."}
            or "\0" in component
            or "/" in component
            or "\\" in component
            or ":" in component
            or "*" in component
            or "?" in component
            or component.endswith((".", " "))
            or any(ord(character) < 32 for character in component)
            or reserved
        ):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        try:
            encoded = component.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        if len(encoded) > _MAX_COMPONENT_BYTES:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        total += len(encoded) + (1 if result else 0)
        if total > _MAX_RELATIVE_BYTES:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        result.append(component)
    return tuple(result)


def _posix_snapshot(fd: int) -> tuple[int, ...]:
    value = os.fstat(fd)
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _posix_identity(fd: int) -> _ObjectIdentity:
    value = os.fstat(fd)
    return _ObjectIdentity(f"{value.st_dev:x}", f"{value.st_ino:x}")


def _posix_role(
    role: _BirthObjectRole | None, exact_private: bool | None
) -> _BirthObjectRole:
    if role is not None:
        if not isinstance(role, _BirthObjectRole):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        return role
    if exact_private is True:
        return _BirthObjectRole.historical_private
    if exact_private is False:
        return _BirthObjectRole.historical_public
    raise BirthSecureFSError("birth_provisioning_acl_unsafe")


def _posix_role_uid(role: _BirthObjectRole, expected_uid: int | None) -> int | None:
    if role is _BirthObjectRole.historical_public:
        return None
    if role is _BirthObjectRole.historical_private:
        return os.geteuid()
    return expected_uid


def _verify_posix_directory(
    fd: int,
    *,
    role: _BirthObjectRole | None = None,
    exact_private: bool | None = None,
    expected_uid: int | None,
) -> None:
    role = _posix_role(role, exact_private)
    value = os.fstat(fd)
    role_uid = _posix_role_uid(role, expected_uid)
    if not stat.S_ISDIR(value.st_mode):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    if role_uid is not None and value.st_uid != role_uid:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    mode = stat.S_IMODE(value.st_mode)
    expected_mode = {
        _BirthObjectRole.birth_confidential: 0o700,
        _BirthObjectRole.birth_integrity_only: 0o755,
        _BirthObjectRole.historical_private: 0o700,
    }.get(role)
    if (expected_mode is not None and mode != expected_mode) or (
        role is _BirthObjectRole.historical_public and mode & 0o022
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")


def _verify_posix_file(
    fd: int,
    *,
    role: _BirthObjectRole | None = None,
    exact_private: bool | None = None,
    expected_uid: int | None,
) -> None:
    role = _posix_role(role, exact_private)
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    role_uid = _posix_role_uid(role, expected_uid)
    if role_uid is not None and value.st_uid != role_uid:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    mode = stat.S_IMODE(value.st_mode)
    expected_mode = {
        _BirthObjectRole.birth_confidential: 0o600,
        _BirthObjectRole.birth_integrity_only: 0o644,
        _BirthObjectRole.historical_private: 0o600,
    }.get(role)
    if (expected_mode is not None and mode != expected_mode) or (
        role is _BirthObjectRole.historical_public and mode & 0o022
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")


def _open_posix_root(
    path: Path, *, exact_private: bool, expected_uid: int | None
) -> tuple[list[int], str]:
    raw = os.fspath(path)
    if "\0" in raw:
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    absolute = os.path.abspath(raw)
    drive, tail = os.path.splitdrive(absolute)
    if drive or not tail.startswith(os.sep):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    components = tuple(item for item in tail.split(os.sep) if item)
    if any(item in {".", ".."} for item in Path(raw).parts):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    opened: list[int] = []
    try:
        current = os.open(os.sep, flags)
        opened.append(current)
        for component in components:
            current = os.open(component, flags, dir_fd=current)
            opened.append(current)
        _verify_posix_directory(
            opened[-1], exact_private=exact_private, expected_uid=expected_uid
        )
        return opened, absolute
    except BirthSecureFSError:
        for fd in reversed(opened):
            os.close(fd)
        raise
    except OSError as exc:
        for fd in reversed(opened):
            os.close(fd)
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc


# The Windows constants and structures are defined on every platform so a
# local probe can decode a request buffer, an access mask or a status code
# without a Windows runner.  Only the library handles below are bound
# lazily, because ctypes.WinDLL exists on Windows alone.
from ctypes import wintypes


_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_READ_DATA = 0x0001
_FILE_LIST_DIRECTORY = 0x0001
_FILE_TRAVERSE = 0x0020
_FILE_READ_ATTRIBUTES = 0x0080
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_SYNCHRONIZE = 0x00100000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_FILE_FLAG_WRITE_THROUGH = 0x80000000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_PERSISTENT_ACLS = 0x00000008
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_ERROR_NO_MORE_FILES = 18
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33
_ERROR_NOT_SUPPORTED = 50
_ERROR_NOT_ALL_ASSIGNED = 1300
_ERROR_PRIVILEGE_NOT_HELD = 1314
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_ERROR_NOT_SAME_DEVICE = 17
_FILE_STANDARD_INFO_CLASS = 1
_FILE_RENAME_INFO_CLASS = 3
_FILE_DISPOSITION_INFO_EX_CLASS = 21
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_ID_INFO_CLASS = 18
_FILE_ID_EXTD_DIRECTORY_INFO_CLASS = 19
_FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS = 20
_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_FILE_DISPOSITION_FLAG_DELETE = 0x00000001
_FILE_DISPOSITION_FLAG_POSIX_SEMANTICS = 0x00000002
_FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE = 0x00000010
_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_SE_PRIVILEGE_ENABLED = 0x00000002
_TOKEN_USER_CLASS = 1
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SDDL_REVISION_1 = 1

class _FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", wintypes.BOOLEAN),
        ("Directory", wintypes.BOOLEAN),
    ]

class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

class _FILE_ID_128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", _FILE_ID_128)]

class _OVERLAPPED_UNION_OFFSET(ctypes.Structure):
    _fields_ = [("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD)]

class _OVERLAPPED_UNION(ctypes.Union):
    _fields_ = [("offset", _OVERLAPPED_UNION_OFFSET), ("Pointer", ctypes.c_void_p)]

class _OVERLAPPED(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("union", _OVERLAPPED_UNION),
        ("hEvent", wintypes.HANDLE),
    ]

class _FILE_RENAME_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", wintypes.BOOLEAN),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]

class _FILE_DISPOSITION_INFO_EX(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD)]

class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", _LUID_AND_ATTRIBUTES * 1),
    ]

class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

class _FILE_ID_EXTD_DIR_INFO(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.DWORD),
        ("FileIndex", wintypes.DWORD),
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("AllocationSize", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
        ("FileNameLength", wintypes.DWORD),
        ("EaSize", wintypes.DWORD),
        ("ReparsePointTag", wintypes.DWORD),
        ("FileId", _FILE_ID_128),
        ("FileName", wintypes.WCHAR * 1),
    ]

if os.name == "nt":  # pragma: no cover - bindings exercised by Windows CI
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _KERNEL32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _KERNEL32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _KERNEL32.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _KERNEL32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _KERNEL32.ReadFile.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.WriteFile.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    _KERNEL32.WriteFile.restype = wintypes.BOOL
    _KERNEL32.SetFilePointerEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    _KERNEL32.SetFilePointerEx.restype = wintypes.BOOL
    _KERNEL32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    _KERNEL32.FlushFileBuffers.restype = wintypes.BOOL
    _KERNEL32.LockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    )
    _KERNEL32.LockFileEx.restype = wintypes.BOOL
    _KERNEL32.UnlockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    )
    _KERNEL32.UnlockFileEx.restype = wintypes.BOOL
    _KERNEL32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _KERNEL32.SetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.CreateDirectoryW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    )
    _KERNEL32.CreateDirectoryW.restype = wintypes.BOOL
    _KERNEL32.GetVolumeInformationByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    _KERNEL32.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    _KERNEL32.GetCurrentProcess.argtypes = ()
    _KERNEL32.GetCurrentProcess.restype = wintypes.HANDLE
    _KERNEL32.LocalFree.argtypes = (ctypes.c_void_p,)
    _KERNEL32.LocalFree.restype = ctypes.c_void_p
    _KERNEL32.SetLastError.argtypes = (wintypes.DWORD,)
    _KERNEL32.SetLastError.restype = None
    _ADVAPI32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _ADVAPI32.OpenProcessToken.restype = wintypes.BOOL
    _ADVAPI32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _ADVAPI32.GetTokenInformation.restype = wintypes.BOOL
    _ADVAPI32.LookupPrivilegeValueW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(_LUID),
    )
    _ADVAPI32.LookupPrivilegeValueW.restype = wintypes.BOOL
    _ADVAPI32.AdjustTokenPrivileges.argtypes = (
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.POINTER(_TOKEN_PRIVILEGES),
        wintypes.DWORD,
        ctypes.POINTER(_TOKEN_PRIVILEGES),
        ctypes.POINTER(wintypes.DWORD),
    )
    _ADVAPI32.AdjustTokenPrivileges.restype = wintypes.BOOL
    _ADVAPI32.ConvertSidToStringSidW.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    _ADVAPI32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    )
    _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    )
    _ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    _ADVAPI32.GetSecurityDescriptorOwner.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    )
    _ADVAPI32.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    _ADVAPI32.GetSecurityDescriptorDacl.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    )
    _ADVAPI32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    _ADVAPI32.SetSecurityInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _ADVAPI32.SetSecurityInfo.restype = wintypes.DWORD
    _ADVAPI32.GetSecurityInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _ADVAPI32.GetSecurityInfo.restype = wintypes.DWORD


def _win_close(handle: int) -> None:
    if os.name == "nt" and handle not in {None, _INVALID_HANDLE_VALUE}:
        _KERNEL32.CloseHandle(handle)


def _win_error(operation: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, operation)


def _win_open_path(
    path: str,
    *,
    directory: bool,
    writable: bool = False,
    delete: bool = False,
    create: bool = False,
    security_attributes: object | None = None,
    security_write: bool = False,
    generic_read: bool = False,
) -> int:
    access = _FILE_READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE
    access |= _FILE_LIST_DIRECTORY | _FILE_TRAVERSE if directory else _FILE_READ_DATA
    if writable:
        access |= _GENERIC_READ | _GENERIC_WRITE
    elif generic_read:
        access |= _GENERIC_READ
    if delete:
        access |= _DELETE
    if security_write:
        access |= _WRITE_DAC | _WRITE_OWNER
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    if writable or delete:
        flags |= _FILE_FLAG_WRITE_THROUGH
    handle = _KERNEL32.CreateFileW(
        path,
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        security_attributes,
        _CREATE_NEW if create else _OPEN_EXISTING,
        flags,
        None,
    )
    if handle in {None, _INVALID_HANDLE_VALUE}:
        raise _win_error("CreateFileW")
    return handle


def _win_info(handle: int) -> tuple[_ObjectIdentity, int, int, bool, bool, int]:
    standard = _FILE_STANDARD_INFO()
    tagged = _FILE_ATTRIBUTE_TAG_INFO()
    identity = _FILE_ID_INFO()
    for info_class, target in (
        (_FILE_STANDARD_INFO_CLASS, standard),
        (_FILE_ATTRIBUTE_TAG_INFO_CLASS, tagged),
        (_FILE_ID_INFO_CLASS, identity),
    ):
        if not _KERNEL32.GetFileInformationByHandleEx(
            handle, info_class, ctypes.byref(target), ctypes.sizeof(target)
        ):
            raise _win_error("GetFileInformationByHandleEx")
    object_identity = _ObjectIdentity(
        f"{int(identity.VolumeSerialNumber):016x}",
        bytes(identity.FileId.Identifier).hex(),
    )
    return (
        object_identity,
        int(tagged.FileAttributes),
        int(standard.NumberOfLinks),
        bool(standard.DeletePending),
        bool(standard.Directory),
        int(standard.EndOfFile),
    )


def _win_final_path(handle: int) -> str:
    needed = _KERNEL32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not needed:
        raise _win_error("GetFinalPathNameByHandleW")
    buffer = ctypes.create_unicode_buffer(needed + 1)
    written = _KERNEL32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        raise _win_error("GetFinalPathNameByHandleW")
    return _win_normalize_comparison_path(buffer.value)


def _win_normalize_comparison_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value)).rstrip("\\/")


def _win_prefixes(path: str) -> tuple[str, ...]:
    absolute = os.path.abspath(path)
    drive, tail = os.path.splitdrive(absolute)
    if not drive or not tail.startswith(("\\", "/")):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    root = drive + "\\"
    result = [root]
    current = root
    for component in (item for item in tail.replace("/", "\\").split("\\") if item):
        current = os.path.join(current, component)
        result.append(current)
    return tuple(result)


def _verify_win_object(handle: int, expected_path: str, *, directory: bool) -> tuple:
    value = _win_info(handle)
    _, attributes, links, delete_pending, is_directory, size = value
    if (
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or delete_pending
        or is_directory != directory
        or (not directory and links != 1)
        or size < 0
    ):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    if _win_final_path(handle) != _win_normalize_comparison_path(expected_path):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    return value


_WINDOWS_LOCAL_ROOT_LIMIT = 260


def _require_local_canonical_windows_root(absolute: str) -> None:
    """Admit one local canonical drive path and refuse every other form.

    A network share, a verbatim prefix, a path beyond the classic limit or a
    form that differs from the canonical one is refused before any object is
    created.  Supporting those forms would mean proving remote persistent
    ACLs, verbatim traversal and case folding on every operation; refusing
    them keeps the surface smaller than the proof would be.
    """
    import ntpath

    # ntpath, not os.path: the rule is a property of the Windows form and must
    # stay decidable by the local probe on any platform.
    if absolute.startswith("\\\\") or len(absolute) > _WINDOWS_LOCAL_ROOT_LIMIT:
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")
    drive, remainder = ntpath.splitdrive(absolute)
    if (
        len(drive) != 2
        or drive[1] != ":"
        or not drive[0].isascii()
        or not drive[0].isalpha()
        or not remainder.startswith("\\")
        or ".." in remainder.split("\\")
        or ntpath.normpath(absolute) != absolute
    ):
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")


def _open_win_root(path: Path) -> tuple[list[int], str]:
    absolute = os.path.abspath(os.fspath(path))
    _require_local_canonical_windows_root(absolute)
    opened: list[int] = []
    try:
        for prefix in _win_prefixes(absolute):
            handle = _win_open_path(prefix, directory=True)
            opened.append(handle)
            _verify_win_object(handle, prefix, directory=True)
        return opened, absolute
    except BirthSecureFSError:
        for handle in reversed(opened):
            _win_close(handle)
        raise
    except OSError as exc:
        for handle in reversed(opened):
            _win_close(handle)
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc


def _win_require_supported_volume(handle: int) -> None:
    flags = wintypes.DWORD()
    filesystem = ctypes.create_unicode_buffer(32)
    if not _KERNEL32.GetVolumeInformationByHandleW(
        handle,
        None,
        0,
        None,
        None,
        ctypes.byref(flags),
        filesystem,
        len(filesystem),
    ):
        raise BirthSecureFSError(
            "birth_provisioning_atomic_install_unsupported"
        ) from _win_error("GetVolumeInformationByHandleW")
    if filesystem.value.casefold() != "ntfs" or not flags.value & _FILE_PERSISTENT_ACLS:
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")


def _windows_service_sid_for_current_process() -> str:
    """Return the real process-token SID for isolated Windows primitive tests."""
    if os.name != "nt":
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    token = wintypes.HANDLE()
    if not _ADVAPI32.OpenProcessToken(
        _KERNEL32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from _win_error(
            "OpenProcessToken"
        )
    try:
        required = wintypes.DWORD()
        _ADVAPI32.GetTokenInformation(
            token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(required)
        )
        if not required.value:
            raise _win_error("GetTokenInformation")
        buffer = ctypes.create_string_buffer(required.value)
        if not _ADVAPI32.GetTokenInformation(
            token,
            _TOKEN_USER_CLASS,
            buffer,
            len(buffer),
            ctypes.byref(required),
        ):
            raise _win_error("GetTokenInformation")
        token_user = _TOKEN_USER.from_buffer(buffer)
        encoded = wintypes.LPWSTR()
        if not _ADVAPI32.ConvertSidToStringSidW(
            token_user.Sid, ctypes.byref(encoded)
        ):
            raise _win_error("ConvertSidToStringSidW")
        try:
            return encoded.value
        finally:
            _KERNEL32.LocalFree(ctypes.cast(encoded, ctypes.c_void_p))
    except OSError as exc:
        raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
    finally:
        _win_close(token.value)


@contextlib.contextmanager
def _win_restore_privilege() -> Iterator[None]:
    token = wintypes.HANDLE()
    if not _ADVAPI32.OpenProcessToken(
        _KERNEL32.GetCurrentProcess(),
        _TOKEN_QUERY | _TOKEN_ADJUST_PRIVILEGES,
        ctypes.byref(token),
    ):
        raise BirthSecureFSError("birth_provisioning_elevation_required") from _win_error(
            "OpenProcessToken"
        )
    previous = _TOKEN_PRIVILEGES()
    previous_size = wintypes.DWORD(ctypes.sizeof(previous))
    try:
        luid = _LUID()
        if not _ADVAPI32.LookupPrivilegeValueW(
            None, "SeRestorePrivilege", ctypes.byref(luid)
        ):
            raise _win_error("LookupPrivilegeValueW")
        requested = _TOKEN_PRIVILEGES()
        requested.PrivilegeCount = 1
        requested.Privileges[0].Luid = luid
        requested.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
        _KERNEL32.SetLastError(0)
        if not _ADVAPI32.AdjustTokenPrivileges(
            token,
            False,
            ctypes.byref(requested),
            ctypes.sizeof(previous),
            ctypes.byref(previous),
            ctypes.byref(previous_size),
        ):
            raise _win_error("AdjustTokenPrivileges")
        if ctypes.get_last_error() == _ERROR_NOT_ALL_ASSIGNED:
            raise BirthSecureFSError("birth_provisioning_elevation_required")
    except BirthSecureFSError:
        _win_close(token.value)
        raise
    except OSError as exc:
        _win_close(token.value)
        raise BirthSecureFSError("birth_provisioning_elevation_required") from exc
    try:
        yield
    finally:
        if previous.PrivilegeCount:
            _KERNEL32.SetLastError(0)
            restored = _ADVAPI32.AdjustTokenPrivileges(
                token,
                False,
                ctypes.byref(previous),
                0,
                None,
                None,
            )
            restore_error = ctypes.get_last_error()
            if not restored or restore_error == _ERROR_NOT_ALL_ASSIGNED:
                failure = (
                    _win_error("AdjustTokenPrivileges(restore)")
                    if not restored
                    else OSError(restore_error, "AdjustTokenPrivileges(restore)")
                )
                _win_close(token.value)
                raise BirthSecureFSError(
                    "birth_provisioning_elevation_required"
                ) from failure
        _win_close(token.value)


def _win_sddl(
    profile: Literal["confidential", "integrity_only"],
    *,
    directory: bool,
    service_sid: str,
) -> str:
    if (
        not isinstance(service_sid, str)
        or not service_sid.startswith("S-1-")
        or service_sid.casefold()
        in {"s-1-5-18", "s-1-5-32-544", "s-1-5-11"}
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    service_mask = "0x001200a9" if directory else "0x00120089"
    aces = ["(A;;FA;;;SY)", "(A;;FA;;;BA)", f"(A;;{service_mask};;;{service_sid})"]
    if profile == "integrity_only":
        aces.append(f"(A;;{service_mask};;;AU)")
    elif profile != "confidential":
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    return "O:SYD:P" + "".join(aces)


@contextlib.contextmanager
def _win_security_attributes(
    profile: Literal["confidential", "integrity_only"],
    *,
    directory: bool,
    service_sid: str,
) -> Iterator[tuple[_SECURITY_ATTRIBUTES, int]]:
    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not _ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        _win_sddl(profile, directory=directory, service_sid=service_sid),
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(size),
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from _win_error(
            "ConvertStringSecurityDescriptorToSecurityDescriptorW"
        )
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
    )
    try:
        yield attributes, descriptor.value
    finally:
        _KERNEL32.LocalFree(descriptor)


def _win_descriptor_sddl(descriptor: int) -> str:
    encoded = wintypes.LPWSTR()
    length = wintypes.DWORD()
    information = _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
    if not _ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor,
        _SDDL_REVISION_1,
        information,
        ctypes.byref(encoded),
        ctypes.byref(length),
    ):
        raise _win_error("ConvertSecurityDescriptorToStringSecurityDescriptorW")
    try:
        return encoded.value
    finally:
        _KERNEL32.LocalFree(ctypes.cast(encoded, ctypes.c_void_p))


def _win_apply_and_verify_security(handle: int, expected_descriptor: int) -> None:
    owner = ctypes.c_void_p()
    owner_defaulted = wintypes.BOOL()
    dacl_present = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    dacl_defaulted = wintypes.BOOL()
    if not _ADVAPI32.GetSecurityDescriptorOwner(
        expected_descriptor, ctypes.byref(owner), ctypes.byref(owner_defaulted)
    ) or not _ADVAPI32.GetSecurityDescriptorDacl(
        expected_descriptor,
        ctypes.byref(dacl_present),
        ctypes.byref(dacl),
        ctypes.byref(dacl_defaulted),
    ):
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from _win_error(
            "GetSecurityDescriptor"
        )
    if owner_defaulted or not dacl_present or not dacl or dacl_defaulted:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    information = (
        _OWNER_SECURITY_INFORMATION
        | _DACL_SECURITY_INFORMATION
        | _PROTECTED_DACL_SECURITY_INFORMATION
    )
    result = _ADVAPI32.SetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        information,
        owner,
        None,
        dacl,
        None,
    )
    if result:
        code = (
            "birth_provisioning_elevation_required"
            if result in {_ERROR_ACCESS_DENIED, _ERROR_PRIVILEGE_NOT_HELD}
            else "birth_provisioning_acl_unsafe"
        )
        raise BirthSecureFSError(code) from OSError(result, "SetSecurityInfo")

    _win_verify_security(handle, expected_descriptor)


def _win_verify_security(handle: int, expected_descriptor: int) -> None:
    actual_descriptor = ctypes.c_void_p()
    result = _ADVAPI32.GetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(actual_descriptor),
    )
    if result:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from OSError(
            result, "GetSecurityInfo"
        )
    try:
        expected = _win_descriptor_sddl(expected_descriptor)
        actual = _win_descriptor_sddl(actual_descriptor.value)
        if actual.casefold() != expected.casefold():
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    except OSError as exc:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe") from exc
    finally:
        _KERNEL32.LocalFree(actual_descriptor)


def _win_dispose_created(handle: int) -> None:
    disposition = _FILE_DISPOSITION_INFO_EX(
        _FILE_DISPOSITION_FLAG_DELETE
        | _FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
        | _FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE
    )
    if not _KERNEL32.SetFileInformationByHandle(
        handle,
        _FILE_DISPOSITION_INFO_EX_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise _win_error("SetFileInformationByHandle(FileDispositionInfoEx)")


class _SecureDirectoryHandle:
    """Opaque directory capability; it never reveals an OS path or raw handle."""

    __slots__ = ("_session", "_components")

    def __init__(self, session: "_SecureRootSession", components: tuple[str, ...]) -> None:
        self._session = session
        self._components = components

    def read_file(
        self,
        name: str,
        *,
        maximum: int,
        role: _BirthObjectRole | None = None,
    ) -> bytes:
        return self._session.read_file(
            self._components + _relative_components((name,)),
            maximum=maximum,
            role=role,
        )

    def inventory(self) -> tuple[str, ...]:
        return self._session.inventory(self._components)

    def open_directory(
        self, name: str, *, role: _BirthObjectRole | None = None
    ) -> "_SecureDirectoryHandle":
        return self._session.open_directory(
            self._components + _relative_components((name,)), role=role
        )


class _SecureRootSession:
    """Root-bound capability adopted from an authenticated descriptor."""

    __slots__ = (
        "_closed",
        "_authoritative",
        "_identity",
        "_role_catalog",
        "_role_overlay",
        "_directories",
        "_directory_roles",
        "_file_roles",
        "_handles",
        "_lock_stack",
        "_expected_uid",
        "_root_role",
        "_root_path",
        "_root_name",
        "_root_parent_handle",
        "_service_sid",
    )

    def __init__(
        self,
        token: object,
        handles,
        root_path: str,
        *,
        identity: _PlatformIdentity,
        role_catalog: _BirthRoleCatalogV1,
    ) -> None:
        if token is not _SESSION_TOKEN:
            raise TypeError("private constructor")
        if not isinstance(identity, _PlatformIdentity) or not isinstance(
            role_catalog, _BirthRoleCatalogV1
        ):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        handles = list(handles)
        self._identity = identity
        self._role_catalog = role_catalog
        self._role_overlay: dict[
            tuple[str, ...],
            tuple[
                _BirthRoleBindingV1,
                _BirthRoleBindingOriginV1,
                _ObjectIdentity | None,
            ],
        ] = {}
        service_sid = identity.windows_service_sid
        expected_uid = identity.posix_uid
        # A historical catalogue carries only historical roles and therefore
        # never grants a mutating capability: the compatibility facade is
        # bounded by its own closed profile, not by a free boolean.
        historical = {
            _BirthObjectRole.historical_private,
            _BirthObjectRole.historical_public,
        }
        declared = {binding.role for binding in role_catalog.exact_bindings}
        authoritative = bool(role_catalog.patterns) or not (declared & historical)
        root_role = role_catalog._resolve_binding_v1(()).role
        self._handles = handles
        self._directories = {(): handles[-1]}
        self._directory_roles = {(): root_role}
        self._file_roles: dict[
            tuple[str, ...], tuple[_ObjectIdentity, _BirthObjectRole]
        ] = {}
        self._root_path = root_path
        self._root_name = os.path.basename(root_path.rstrip(os.sep))
        self._root_parent_handle = handles[-2] if len(handles) > 1 else None
        self._root_role = root_role
        self._service_sid = service_sid
        self._expected_uid = expected_uid
        self._authoritative = authoritative
        self._lock_stack: list[tuple[int, str, bool]] = []
        self._closed = False
        try:
            if os.name == "nt":
                self._verify_windows_role(
                    self._root_handle, directory=True, role=root_role
                )
            else:
                _verify_posix_directory(
                    self._root_handle,
                    role=root_role,
                    expected_uid=expected_uid,
                )
        except BaseException:
            closer = _win_close if os.name == "nt" else os.close
            for handle in reversed(self._handles):
                try:
                    closer(handle)
                except BaseException:
                    pass
            self._handles.clear()
            self._closed = True
            raise

    def __enter__(self) -> "_SecureRootSession":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.close()
        except BaseException:
            if exc is None:
                raise

    def close(self) -> None:
        if self._closed:
            return
        if self._lock_stack:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        self._closed = True
        closer = _win_close if os.name == "nt" else os.close
        failure: BaseException | None = None
        for handle in reversed(self._handles):
            try:
                closer(handle)
            except BaseException as exc:
                if failure is None:
                    failure = exc
        self._handles.clear()
        if failure is not None:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from failure

    def _require_open(self) -> None:
        if self._closed:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")

    def _resolve_effective_role_binding_v1(
        self, components: tuple[str, ...],
    ) -> _ResolvedBirthRoleBindingV1:
        """Single authoritative read-only resolution of one relative name.

        The immutable catalogue and the private overlay are consulted together
        and must agree.  An absence is ambiguous; two different results for the
        same components contradict each other (section 16.13.1).
        """
        components = _relative_components(components)
        overlay = self._role_overlay.get(components)
        catalog: _BirthRoleBindingV1 | None
        try:
            catalog = self._role_catalog._resolve_binding_v1(components)
        except BirthSecureFSError as exc:
            if overlay is None or exc.code != "birth_provisioning_recovery_ambiguous":
                raise
            catalog = None
        if overlay is None:
            assert catalog is not None
            return _ResolvedBirthRoleBindingV1(
                binding=catalog, origin=_BirthRoleBindingOriginV1.CATALOG,
            )
        binding, origin, _identity = overlay
        if catalog is not None and (
            catalog.kind is not binding.kind or catalog.role is not binding.role
        ):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        return _ResolvedBirthRoleBindingV1(binding=binding, origin=origin)

    @contextlib.contextmanager
    def _reserve_exact_role_binding_v1(
        self, binding: _BirthRoleBindingV1,
    ) -> Iterator[None]:
        """Reserve one concrete binding before the first traversal syscall.

        Only the exact fixture mode keeps an overlay: a productive catalogue
        classifies every admitted name through its grammar.  An exception of
        any kind cancels the reservation, so the logical inventory is unchanged
        unless the creation completed and became durable.
        """
        if self._role_catalog.patterns:
            yield
            return
        if not isinstance(binding, _BirthRoleBindingV1):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        components = _relative_components(binding.components)
        existing = self._role_overlay.get(components)
        if existing is not None:
            raise BirthSecureFSError("birth_provisioning_transaction_conflict")
        self._role_overlay[components] = (
            binding, _BirthRoleBindingOriginV1.OVERLAY_RESERVED, None,
        )
        committed = False
        try:
            yield
            committed = True
        finally:
            if not committed:
                self._role_overlay.pop(components, None)

    def _commit_exact_role_binding_v1(
        self, components: tuple[str, ...], identity: _ObjectIdentity,
    ) -> None:
        entry = self._role_overlay.get(components)
        if entry is None:
            return
        binding, _origin, _identity = entry
        self._role_overlay[components] = (
            binding, _BirthRoleBindingOriginV1.OVERLAY_COMMITTED, identity,
        )

    def _holds_global_lock(self) -> bool:
        self._require_open()
        return any(rank == 0 for rank, _, _ in self._lock_stack)

    def _holds_global_exclusive(self) -> bool:
        self._require_open()
        return any(
            rank == 0 and exclusive for rank, _, exclusive in self._lock_stack
        )

    def _require_global_exclusive(self) -> None:
        if not self._holds_global_exclusive():
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")

    @property
    def _root_handle(self) -> int:
        self._require_open()
        return self._directories[()]

    @contextlib.contextmanager
    def _directory_chain(
        self,
        components: tuple[str, ...],
        *,
        final_role: _BirthObjectRole | None = None,
    ) -> Iterator[tuple[int, str]]:
        self._require_open()
        self._verify_root_binding()
        components = _relative_components(components)
        current = self._root_handle
        current_path = self._root_path
        try:
            prefix: tuple[str, ...] = ()
            for component in components:
                prefix += (component,)
                current_path = os.path.join(current_path, component)
                child = self._directories.get(prefix)
                role = self._directory_roles.get(prefix)
                if (
                    role is not None
                    and prefix == components
                    and final_role is not None
                    and role is not final_role
                ):
                    raise BirthSecureFSError("birth_provisioning_acl_unsafe")
                if role is None:
                    role = final_role if prefix == components and final_role else self._root_role
                if child is None:
                    try:
                        if os.name == "nt":
                            child = _win_open_path(current_path, directory=True)
                            _verify_win_object(child, current_path, directory=True)
                            self._verify_windows_role(
                                child, directory=True, role=role
                            )
                        else:
                            flags = (
                                os.O_RDONLY
                                | getattr(os, "O_CLOEXEC", 0)
                                | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0)
                            )
                            child = os.open(component, flags, dir_fd=current)
                            _verify_posix_directory(
                                child,
                                role=role,
                                expected_uid=self._expected_uid,
                            )
                    except BaseException:
                        if child is not None:
                            (_win_close if os.name == "nt" else os.close)(child)
                        raise
                    self._directories[prefix] = child
                    self._directory_roles[prefix] = role
                    self._handles.append(child)
                elif os.name == "nt":
                    _verify_win_object(child, current_path, directory=True)
                    self._verify_windows_role(
                        child, directory=True, role=role
                    )
                else:
                    _verify_posix_directory(
                        child,
                        role=role,
                        expected_uid=self._expected_uid,
                    )
                    flags = (
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    rebound = os.open(component, flags, dir_fd=current)
                    try:
                        if _posix_identity(rebound) != _posix_identity(child):
                            raise BirthSecureFSError(
                                "birth_provisioning_recovery_ambiguous"
                            )
                    finally:
                        os.close(rebound)
                current = child
            yield current, current_path
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc

    def _verify_windows_role(
        self,
        handle: int,
        *,
        directory: bool,
        role: _BirthObjectRole,
    ) -> None:
        if os.name != "nt":
            return
        if self._service_sid is None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        profile = {
            _BirthObjectRole.birth_confidential: "confidential",
            _BirthObjectRole.birth_integrity_only: "integrity_only",
            _BirthObjectRole.historical_private: "historical_private",
            _BirthObjectRole.historical_public: "historical_public",
        }[role]
        with _win_security_attributes(
            profile, directory=directory, service_sid=self._service_sid
        ) as (_, descriptor):
            _win_verify_security(handle, descriptor)

    def _verify_root_binding(self) -> None:
        if os.name != "posix" or self._root_parent_handle is None or not self._root_name:
            return
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            rebound = os.open(
                self._root_name, flags, dir_fd=self._root_parent_handle
            )
            try:
                if _posix_identity(rebound) != _posix_identity(self._root_handle):
                    raise BirthSecureFSError(
                        "birth_provisioning_recovery_ambiguous"
                    )
            finally:
                os.close(rebound)
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError(
                "birth_provisioning_recovery_ambiguous"
            ) from exc

    def open_directory(
        self,
        components: tuple[str, ...],
        *,
        role: _BirthObjectRole | None = None,
    ) -> _SecureDirectoryHandle:
        components = _relative_components(components)
        role = self._root_role if role is None else role
        if not isinstance(role, _BirthObjectRole):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        with self._directory_chain(components, final_role=role) as (handle, expected):
            if os.name == "nt":
                _verify_win_object(handle, expected, directory=True)
            else:
                _verify_posix_directory(
                    handle,
                    role=role,
                    expected_uid=self._expected_uid,
                )
        return _SecureDirectoryHandle(self, components)

    def read_file(
        self,
        components: tuple[str, ...],
        *,
        maximum: int,
        role: _BirthObjectRole | None = None,
    ) -> bytes:
        components = _relative_components(components)
        if not components or isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        role = self._root_role if role is None else role
        if not isinstance(role, _BirthObjectRole):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                return self._read_file_windows(
                    components, directory_path, name, maximum, role
                )
            return self._read_file_posix(components, directory, name, maximum, role)

    def _read_file_posix(
        self,
        components: tuple[str, ...],
        directory: int,
        name: str,
        maximum: int,
        role: _BirthObjectRole,
    ) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=directory)
            try:
                _verify_posix_file(
                    fd,
                    role=role,
                    expected_uid=self._expected_uid,
                )
                before = _posix_snapshot(fd)
                identity = _posix_identity(fd)
                bound = self._file_roles.get(components)
                if bound is not None and bound != (identity, role):
                    raise BirthSecureFSError("birth_provisioning_acl_unsafe")
                if before[5] > maximum:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable")
                result = bytearray()
                while len(result) <= maximum:
                    block = os.read(fd, min(8192, maximum + 1 - len(result)))
                    if not block:
                        break
                    result.extend(block)
                after = _posix_snapshot(fd)
                if len(result) > maximum or before != after or len(result) != before[5]:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable")
                self._file_roles.setdefault(components, (identity, role))
                return bytes(result)
            finally:
                os.close(fd)
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc

    def _read_file_windows(
        self,
        components: tuple[str, ...],
        directory_path: str,
        name: str,
        maximum: int,
        role: _BirthObjectRole,
    ) -> bytes:
        path = os.path.join(directory_path, name)
        handle = None
        try:
            handle = _win_open_path(path, directory=False)
            before = _verify_win_object(handle, path, directory=False)
            self._verify_windows_role(
                handle, directory=False, role=role
            )
            bound = self._file_roles.get(components)
            if bound is not None and bound != (before[0], role):
                raise BirthSecureFSError("birth_provisioning_acl_unsafe")
            size = before[5]
            if size > maximum:
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
            result = bytearray()
            while len(result) <= maximum:
                capacity = min(8192, maximum + 1 - len(result))
                buffer = ctypes.create_string_buffer(capacity)
                count = wintypes.DWORD()
                if not _KERNEL32.ReadFile(
                    handle, buffer, capacity, ctypes.byref(count), None
                ):
                    raise _win_error("ReadFile")
                if not count.value:
                    break
                result.extend(buffer.raw[: count.value])
            after = _verify_win_object(handle, path, directory=False)
            if len(result) > maximum or len(result) != size or before != after:
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
            self._file_roles.setdefault(components, (before[0], role))
            return bytes(result)
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        finally:
            if handle is not None:
                _win_close(handle)

    def inventory(self, components: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item.name for item in self._inventory_state(components))

    def _inventory_state(
        self, components: tuple[str, ...]
    ) -> tuple[_InventoryEntry, ...]:
        components = _relative_components(components)
        with self._directory_chain(components) as (handle, _):
            try:
                def resolve(relative, scope=components):
                    return self._resolve_effective_role_binding_v1(
                        scope + relative
                    ).binding

                before = (
                    _win_inventory(handle)
                    if os.name == "nt"
                    else _posix_inventory(handle, resolve)
                )
                after = (
                    _win_inventory(handle)
                    if os.name == "nt"
                    else _posix_inventory(handle, resolve)
                )
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
            if before != after:
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
            if len({item.name for item in before}) != len(before):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            return before

    @contextlib.contextmanager
    def global_lock(
        self,
        *,
        exclusive: bool,
        create: bool,
        timeout: float = 5.0,
    ) -> Iterator[None]:
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        with self._lock_file(
            ("provisioning-v1.lock",),
            exclusive=exclusive,
            create=create,
            timeout=timeout,
            rank=0,
            order_key="provisioning-v1.lock",
        ):
            yield

    @contextlib.contextmanager
    def local_lock(
        self,
        directory: tuple[str, ...],
        *,
        exclusive: bool = False,
        create: bool = False,
        timeout: float = 5.0,
    ) -> Iterator[None]:
        directory = _relative_components(directory)
        if (exclusive or create) and not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        with self._lock_file(
            directory + ("birth-keystore.lock",),
            exclusive=exclusive,
            create=create,
            timeout=timeout,
            rank=1,
            order_key="/".join(directory) or ".",
        ):
            yield

    @contextlib.contextmanager
    def _lock_file(
        self,
        components: tuple[str, ...],
        *,
        exclusive: bool,
        create: bool,
        timeout: float = 5.0,
        rank: int,
        order_key: str,
    ) -> Iterator[None]:
        components = _relative_components(components)
        if (
            not components
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise BirthSecureFSError("birth_provisioning_lock_unavailable")
        if rank not in {0, 1}:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        key = order_key
        if not isinstance(key, str) or not key:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        if self._lock_stack:
            previous_rank, previous_key, _ = self._lock_stack[-1]
            if rank < previous_rank or (
                rank == previous_rank == 1 and key <= previous_key
            ):
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        if rank == 0 and any(item_rank == 0 for item_rank, _, _ in self._lock_stack):
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                with self._win_lock(directory_path, name, exclusive, create, timeout):
                    self._lock_stack.append((rank, key, exclusive))
                    try:
                        yield
                    finally:
                        if self._lock_stack.pop() != (rank, key, exclusive):
                            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            else:
                role = (
                    _BirthObjectRole.birth_integrity_only
                    if rank == 0
                    else _BirthObjectRole.birth_confidential
                )
                with self._posix_lock(
                    directory, name, exclusive, create, timeout, role
                ):
                    self._lock_stack.append((rank, key, exclusive))
                    try:
                        yield
                    finally:
                        if self._lock_stack.pop() != (rank, key, exclusive):
                            raise BirthSecureFSError("birth_provisioning_lock_unsafe")

    @contextlib.contextmanager
    def _posix_lock(
        self,
        directory: int,
        name: str,
        exclusive: bool,
        create: bool,
        timeout: float,
        role: _BirthObjectRole,
    ) -> Iterator[None]:
        import fcntl

        flags = (os.O_RDWR if exclusive else os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = None
        created = False
        try:
            if create and exclusive:
                try:
                    mode = (
                        0o644
                        if role is _BirthObjectRole.birth_integrity_only
                        else 0o600
                    )
                    fd = os.open(
                        name,
                        flags | os.O_CREAT | os.O_EXCL,
                        mode,
                        dir_fd=directory,
                    )
                    created = True
                except FileExistsError:
                    fd = os.open(name, flags, dir_fd=directory)
            else:
                fd = os.open(name, flags, dir_fd=directory)
            _verify_posix_file(
                fd, role=role, expected_uid=self._expected_uid
            )
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            deadline = time.monotonic() + timeout
            delay_index = 0
            while True:
                try:
                    fcntl.flock(fd, operation | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise BirthSecureFSError("birth_provisioning_lock_unsafe") from exc
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BirthSecureFSError("birth_provisioning_lock_unavailable") from exc
                    delay = _LOCK_DELAYS[min(delay_index, len(_LOCK_DELAYS) - 1)]
                    delay_index += 1
                    time.sleep(min(delay, remaining))
            before = _posix_snapshot(fd)
            if before[5] == 0 and exclusive:
                _write_all_posix(fd, _LOCK_BYTE)
                os.fsync(fd)
                os.fsync(directory)
            elif before[5] != 1:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, 2) != _LOCK_BYTE:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            yield
        except FileNotFoundError as exc:
            raise BirthSecureFSError("birth_provisioning_lock_unavailable") from exc
        except BirthSecureFSError:
            raise
        except OSError as exc:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe") from exc
        finally:
            if fd is not None:
                primary = sys.exc_info()[1]
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BaseException as exc:
                    if primary is None:
                        raise BirthSecureFSError(
                            "birth_provisioning_lock_unsafe"
                        ) from exc
                try:
                    os.close(fd)
                except BaseException as exc:
                    if primary is None:
                        raise BirthSecureFSError(
                            "birth_provisioning_lock_unsafe"
                        ) from exc

    @contextlib.contextmanager
    def _win_lock(
        self,
        directory_path: str,
        name: str,
        exclusive: bool,
        create: bool,
        timeout: float,
    ) -> Iterator[None]:
        path = os.path.join(directory_path, name)
        handle = None
        locked = False
        overlapped = _OVERLAPPED()
        try:
            try:
                if create and exclusive:
                    if self._service_sid is None:
                        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
                    _win_require_supported_volume(self._root_handle)
                    with _win_restore_privilege():
                        with _win_security_attributes(
                            "integrity_only",
                            directory=False,
                            service_sid=self._service_sid,
                        ) as (attributes, descriptor):
                            handle = _win_open_path(
                                path,
                                directory=False,
                                writable=True,
                                create=True,
                                security_attributes=ctypes.byref(attributes),
                                security_write=True,
                            )
                            _win_apply_and_verify_security(handle, descriptor)
                else:
                    handle = _win_open_path(
                        path,
                        directory=False,
                        writable=exclusive,
                        generic_read=not exclusive,
                    )
            except OSError as exc:
                if create and exclusive and exc.errno in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                    handle = _win_open_path(
                        path, directory=False, writable=True, generic_read=True
                    )
                else:
                    raise
            deadline = time.monotonic() + timeout
            delay_index = 0
            flags = _LOCKFILE_FAIL_IMMEDIATELY
            if exclusive:
                flags |= _LOCKFILE_EXCLUSIVE_LOCK
            while True:
                if _KERNEL32.LockFileEx(
                    handle, flags, 0, 1, 0, ctypes.byref(overlapped)
                ):
                    locked = True
                    break
                code = ctypes.get_last_error()
                if code != _ERROR_LOCK_VIOLATION:
                    raise OSError(code, "LockFileEx")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BirthSecureFSError("birth_provisioning_lock_unavailable")
                delay = _LOCK_DELAYS[min(delay_index, len(_LOCK_DELAYS) - 1)]
                delay_index += 1
                time.sleep(min(delay, remaining))
            before = _verify_win_object(handle, path, directory=False)
            if self._service_sid is not None:
                with _win_security_attributes(
                    "integrity_only", directory=False, service_sid=self._service_sid
                ) as (_, descriptor):
                    _win_verify_security(handle, descriptor)
            size = before[5]
            if size == 0 and exclusive:
                _win_write_all(handle, _LOCK_BYTE)
                if not _KERNEL32.FlushFileBuffers(handle):
                    raise _win_error("FlushFileBuffers")
            elif size != 1:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            if not _KERNEL32.SetFilePointerEx(handle, 0, None, 0):
                raise _win_error("SetFilePointerEx")
            buffer = ctypes.create_string_buffer(2)
            count = wintypes.DWORD()
            if not _KERNEL32.ReadFile(handle, buffer, 2, ctypes.byref(count), None):
                raise _win_error("ReadFile")
            if count.value != 1 or buffer.raw[:1] != _LOCK_BYTE:
                raise BirthSecureFSError("birth_provisioning_lock_unsafe")
            yield
        except BirthSecureFSError:
            raise
        except OSError as exc:
            code = "birth_provisioning_lock_unavailable" if exc.errno in {
                _ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND
            } else "birth_provisioning_lock_unsafe"
            raise BirthSecureFSError(code) from exc
        finally:
            if locked and not _KERNEL32.UnlockFileEx(
                handle, 0, 1, 0, ctypes.byref(overlapped)
            ):
                unlock_error = _win_error("UnlockFileEx")
                raise BirthSecureFSError("birth_provisioning_lock_unsafe") from unlock_error
            if handle is not None:
                _win_close(handle)

    def create_file_exclusive(
        self,
        components: tuple[str, ...],
        payload: bytes,
        *,
        role: _BirthObjectRole,
    ) -> _ObjectIdentity:
        self._require_global_exclusive()
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        components = _relative_components(components)
        if (
            not components
            or not isinstance(payload, bytes)
            or role not in {
                _BirthObjectRole.birth_confidential,
                _BirthObjectRole.birth_integrity_only,
            }
        ):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        requested = _BirthRoleBindingV1(
            components=components, kind=_ObjectKind.regular_file, role=role,
        )
        with self._reserve_exact_role_binding_v1(requested):
            resolved = self._resolve_effective_role_binding_v1(components)
            return self._create_file_exclusive_bound(
                resolved.binding, payload,
            )

    def _create_file_exclusive_bound(
        self, binding: _BirthRoleBindingV1, payload: bytes,
    ) -> _ObjectIdentity:
        components = binding.components
        role = binding.role
        if binding.kind is not _ObjectKind.regular_file:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                return self._create_file_exclusive_windows(
                    components, directory_path, name, payload, role
                )
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                mode = (
                    0o600
                    if role is _BirthObjectRole.birth_confidential
                    else 0o644
                )
                fd = os.open(name, flags, mode, dir_fd=directory)
                committed = False
                try:
                    _verify_posix_file(
                        fd, role=role, expected_uid=self._expected_uid
                    )
                    before = _posix_identity(fd)
                    _write_all_posix(fd, payload)
                    os.fsync(fd)
                    os.lseek(fd, 0, os.SEEK_SET)
                    if _read_all_posix(fd, len(payload)) != payload:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    if _posix_identity(fd) != before:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    os.fsync(directory)
                    self._file_roles[components] = (before, role)
                    self._commit_exact_role_binding_v1(components, before)
                    committed = True
                    return before
                finally:
                    os.close(fd)
                    if not committed:
                        # Section 16.13.1: any exception after the creation or
                        # the write removes the new object, releases the
                        # reservation and leaves the logical inventory
                        # unchanged.  The primary error is preserved.
                        self._file_roles.pop(components, None)
                        try:
                            os.unlink(name, dir_fd=directory)
                            os.fsync(directory)
                        except OSError:
                            pass
            except FileExistsError as exc:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            except BirthSecureFSError:
                raise
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc

    def _create_file_exclusive_windows(
        self,
        components: tuple[str, ...],
        directory_path: str,
        name: str,
        payload: bytes,
        role: _BirthObjectRole,
    ) -> _ObjectIdentity:
        if self._service_sid is None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        _win_require_supported_volume(self._root_handle)
        path = os.path.join(directory_path, name)
        handle = None
        created = False
        complete = False
        profile = (
            "confidential"
            if role is _BirthObjectRole.birth_confidential
            else "integrity_only"
        )
        try:
            with _win_restore_privilege():
                with _win_security_attributes(
                    profile, directory=False, service_sid=self._service_sid
                ) as (attributes, descriptor):
                    handle = _win_open_path(
                        path,
                        directory=False,
                        writable=True,
                        delete=True,
                        create=True,
                        security_attributes=ctypes.byref(attributes),
                        security_write=True,
                    )
                    created = True
                    before = _verify_win_object(handle, path, directory=False)
                    _win_apply_and_verify_security(handle, descriptor)
                    _win_write_all(handle, payload)
                    if not _KERNEL32.FlushFileBuffers(handle):
                        raise _win_error("FlushFileBuffers")
                    if not _KERNEL32.SetFilePointerEx(handle, 0, None, 0):
                        raise _win_error("SetFilePointerEx")
                    actual = bytearray()
                    while len(actual) <= len(payload):
                        capacity = min(8192, len(payload) + 1 - len(actual))
                        buffer = ctypes.create_string_buffer(capacity)
                        count = wintypes.DWORD()
                        if not _KERNEL32.ReadFile(
                            handle, buffer, capacity, ctypes.byref(count), None
                        ):
                            raise _win_error("ReadFile")
                        if not count.value:
                            break
                        actual.extend(buffer.raw[: count.value])
                    after = _verify_win_object(handle, path, directory=False)
                    if bytes(actual) != payload or before[0] != after[0]:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    complete = True
                    self._file_roles[components] = (before[0], role)
                    return before[0]
        except OSError as exc:
            if exc.errno in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        except BirthSecureFSError:
            raise
        finally:
            if handle is not None:
                if created and not complete:
                    try:
                        _win_dispose_created(handle)
                    except OSError:
                        pass
                _win_close(handle)

    def create_directory_exclusive(
        self,
        components: tuple[str, ...],
        *,
        role: _BirthObjectRole,
    ) -> _SecureDirectoryHandle:
        self._require_global_exclusive()
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        components = _relative_components(components)
        if not components or role not in {
            _BirthObjectRole.birth_confidential,
            _BirthObjectRole.birth_integrity_only,
        }:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        requested = _BirthRoleBindingV1(
            components=components, kind=_ObjectKind.directory, role=role,
        )
        with self._reserve_exact_role_binding_v1(requested):
            resolved = self._resolve_effective_role_binding_v1(components)
            return self._create_directory_exclusive_bound(resolved.binding)

    def _create_directory_exclusive_bound(
        self, binding: _BirthRoleBindingV1,
    ) -> _SecureDirectoryHandle:
        components = binding.components
        role = binding.role
        if binding.kind is not _ObjectKind.directory:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        parent, name = components[:-1], components[-1]
        with self._directory_chain(parent) as (directory, directory_path):
            if os.name == "nt":
                handle = self._create_directory_exclusive_windows(
                    directory_path,
                    name,
                    role,
                )
                self._directories[components] = handle
                self._directory_roles[components] = role
                self._handles.append(handle)
                return _SecureDirectoryHandle(self, components)
            try:
                mode = (
                    0o700
                    if role is _BirthObjectRole.birth_confidential
                    else 0o755
                )
                os.mkdir(name, mode, dir_fd=directory)
                os.fsync(directory)
                opened = os.open(
                    name,
                    getattr(os, "O_PATH", os.O_RDONLY)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory,
                )
                try:
                    self._commit_exact_role_binding_v1(
                        components, _posix_identity(opened),
                    )
                finally:
                    os.close(opened)
            except FileExistsError as exc:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        return self.open_directory(components, role=role)

    def _create_directory_exclusive_windows(
        self,
        directory_path: str,
        name: str,
        role: _BirthObjectRole,
    ) -> int:
        if self._service_sid is None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        _win_require_supported_volume(self._root_handle)
        path = os.path.join(directory_path, name)
        handle = None
        created = False
        complete = False
        profile = (
            "confidential"
            if role is _BirthObjectRole.birth_confidential
            else "integrity_only"
        )
        try:
            with _win_restore_privilege():
                with _win_security_attributes(
                    profile, directory=True, service_sid=self._service_sid
                ) as (attributes, descriptor):
                    if not _KERNEL32.CreateDirectoryW(path, ctypes.byref(attributes)):
                        raise _win_error("CreateDirectoryW")
                    created = True
                    handle = _win_open_path(
                        path,
                        directory=True,
                        delete=True,
                        security_write=True,
                    )
                    _verify_win_object(handle, path, directory=True)
                    _win_apply_and_verify_security(handle, descriptor)
                    complete = True
                    return handle
        except OSError as exc:
            if exc.errno in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                raise BirthSecureFSError("birth_provisioning_transaction_conflict") from exc
            raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        except BirthSecureFSError:
            raise
        finally:
            if handle is not None and not complete:
                if created and not complete:
                    try:
                        _win_dispose_created(handle)
                    except OSError:
                        pass
                _win_close(handle)

    def rename_no_replace(
        self, source: tuple[str, ...], destination: tuple[str, ...], *, directory: bool
    ) -> _ObjectIdentity:
        self._require_global_exclusive()
        if not self._authoritative:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        source = _relative_components(source)
        destination = _relative_components(destination)
        if not source or not destination:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        if destination in self._directories:
            raise BirthSecureFSError("birth_provisioning_transaction_conflict")
        if os.name == "nt":
            return self._rename_no_replace_windows(source, destination, directory)
        return self._rename_no_replace_posix(source, destination, directory)

    def _rename_no_replace_posix(
        self, source: tuple[str, ...], destination: tuple[str, ...], directory: bool
    ) -> _ObjectIdentity:
        source_parent, source_name = source[:-1], source[-1]
        target_parent, target_name = destination[:-1], destination[-1]
        if directory:
            role = self._directory_roles.get(source)
        else:
            binding = self._file_roles.get(source)
            role = binding[1] if binding is not None else None
        if role not in {
            _BirthObjectRole.birth_confidential,
            _BirthObjectRole.birth_integrity_only,
        }:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        with self._directory_chain(source_parent) as (source_fd, _):
            with self._directory_chain(target_parent) as (target_fd, _):
                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                if directory:
                    flags |= getattr(os, "O_DIRECTORY", 0)
                try:
                    object_fd = os.open(source_name, flags, dir_fd=source_fd)
                    try:
                        if directory:
                            _verify_posix_directory(
                                object_fd,
                                role=role,
                                expected_uid=self._expected_uid,
                            )
                        else:
                            _verify_posix_file(
                                object_fd,
                                role=role,
                                expected_uid=self._expected_uid,
                            )
                        identity = _posix_identity(object_fd)
                        if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
                            raise BirthSecureFSError(
                                "birth_provisioning_atomic_install_unsupported"
                            )
                        _renameat2_no_replace(source_fd, source_name, target_fd, target_name)
                        os.fsync(source_fd)
                        if source_fd != target_fd:
                            os.fsync(target_fd)
                        # The object keeps its identity and role; only its name
                        # changes, so a reserved binding follows the rename and
                        # the post-validation can classify the destination.
                        moved_binding = self._role_overlay.pop(source, None)
                        if moved_binding is not None:
                            binding, origin, observed = moved_binding
                            self._role_overlay[destination] = (
                                _BirthRoleBindingV1(
                                    components=destination,
                                    kind=binding.kind,
                                    role=binding.role,
                                ),
                                origin,
                                observed,
                            )
                    finally:
                        os.close(object_fd)
                except BirthSecureFSError:
                    raise
                except OSError as exc:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        source_entries = self._inventory_state(source_parent)
        if any(item.name == source_name for item in source_entries):
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        target_entries = (
            source_entries
            if target_parent == source_parent
            else self._inventory_state(target_parent)
        )
        moved = [item for item in target_entries if item.name == target_name]
        if len(moved) != 1 or moved[0].identity != identity:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")
        with self._directory_chain(target_parent) as (target_fd, _):
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            if directory:
                flags |= getattr(os, "O_DIRECTORY", 0)
            try:
                final_fd = os.open(target_name, flags, dir_fd=target_fd)
                try:
                    if directory:
                        _verify_posix_directory(
                            final_fd,
                            role=role,
                            expected_uid=self._expected_uid,
                        )
                    else:
                        _verify_posix_file(
                            final_fd,
                            role=role,
                            expected_uid=self._expected_uid,
                        )
                    if _posix_identity(final_fd) != identity:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                finally:
                    os.close(final_fd)
            except BirthSecureFSError:
                raise
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
        self._remap_cached_directories(source, destination)
        return identity

    def _rename_no_replace_windows(
        self, source: tuple[str, ...], destination: tuple[str, ...], directory: bool
    ) -> _ObjectIdentity:
        source_parent, source_name = source[:-1], source[-1]
        target_parent, target_name = destination[:-1], destination[-1]
        with self._directory_chain(source_parent) as (_, source_path):
            with self._directory_chain(target_parent) as (target_handle, target_path):
                source_path = os.path.join(source_path, source_name)
                source_handle = self._directories.get(source) if directory else None
                close_source = source_handle is None
                try:
                    if source_handle is None:
                        source_handle = _win_open_path(
                            source_path, directory=directory, delete=True
                        )
                    before = _verify_win_object(source_handle, source_path, directory=directory)
                    target_identity = _win_info(target_handle)[0]
                    if before[0].volume != target_identity.volume:
                        raise BirthSecureFSError(
                            "birth_provisioning_atomic_install_unsupported"
                        )
                    encoded = target_name.encode("utf-16-le")
                    offset = _FILE_RENAME_INFO_HEADER.FileName.offset
                    buffer = ctypes.create_string_buffer(offset + len(encoded))
                    header = _FILE_RENAME_INFO_HEADER.from_buffer(buffer)
                    header.ReplaceIfExists = False
                    header.RootDirectory = target_handle
                    header.FileNameLength = len(encoded)
                    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
                    if not _KERNEL32.SetFileInformationByHandle(
                        source_handle,
                        _FILE_RENAME_INFO_CLASS,
                        buffer,
                        len(buffer),
                    ):
                        error = ctypes.get_last_error()
                        if error in {
                            _ERROR_FILE_EXISTS,
                            _ERROR_ALREADY_EXISTS,
                            _ERROR_ACCESS_DENIED,
                            _ERROR_SHARING_VIOLATION,
                        } and _win_destination_exists(target_path, target_name, directory):
                            raise BirthSecureFSError(
                                "birth_provisioning_transaction_conflict"
                            )
                        if error in {_ERROR_NOT_SUPPORTED, _ERROR_NOT_SAME_DEVICE}:
                            raise BirthSecureFSError(
                                "birth_provisioning_atomic_install_unsupported"
                            )
                        raise OSError(error, "SetFileInformationByHandle")
                    after = _win_info(source_handle)
                    if after[0] != before[0]:
                        raise BirthSecureFSError("birth_provisioning_io_unavailable")
                    identity = before[0]
                except BirthSecureFSError:
                    raise
                except OSError as exc:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
                finally:
                    if source_handle is not None and close_source:
                        _win_close(source_handle)
        with self._directory_chain(target_parent) as (_, target_path):
            final_path = os.path.join(target_path, target_name)
            final_handle = None
            try:
                final_handle = _win_open_path(final_path, directory=directory)
                if _verify_win_object(final_handle, final_path, directory=directory)[0] != identity:
                    raise BirthSecureFSError("birth_provisioning_io_unavailable")
            except BirthSecureFSError:
                raise
            except OSError as exc:
                raise BirthSecureFSError("birth_provisioning_io_unavailable") from exc
            finally:
                if final_handle is not None:
                    _win_close(final_handle)
        self._remap_cached_directories(source, destination)
        return identity

    def dispose_transaction_object(
        self, expectation: _DisposalExpectation,
    ) -> _DispositionResult:
        """Remove one transaction object that matches the expectation exactly.

        The operation is relative to a handle, requires the exclusive global
        lock and never follows a link.  It compares identity, kind, security
        role, link count, size and inventory on the very handle it opened, and
        it knows nothing about a journal or a checkpoint: recording the outcome
        belongs to the caller of increment 2B (section 16.13.2).
        """
        self._require_global_exclusive()
        if not isinstance(expectation, _DisposalExpectation):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        components = _relative_components(expectation.components)
        if not components:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        resolved = self._resolve_effective_role_binding_v1(components)
        if (
            resolved.binding.kind is not expectation.kind
            or resolved.binding.role is not expectation.role
        ):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        if expectation.disposal_class is _DisposalClass.partial_pending_file:
            # A partial pending carries no digest and no complete inventory, so
            # only the private provenance register of the session that created
            # it can authorise its removal (section 7.6).
            recorded = self._file_roles.get(components)
            if recorded is None or recorded[0] != expectation.identity or (
                recorded[1] is not expectation.role
            ):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        if os.name == "nt":
            raise BirthSecureFSError(
                "birth_provisioning_atomic_install_unsupported"
            )
        return self._dispose_transaction_object_posix(expectation, components)

    def _dispose_transaction_object_posix(
        self, expectation: _DisposalExpectation, components: tuple[str, ...],
    ) -> _DispositionResult:
        parent, name = components[:-1], components[-1]
        directory_expected = expectation.kind is _ObjectKind.directory
        with self._directory_chain(parent) as (directory, _path):
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            if directory_expected:
                flags |= getattr(os, "O_DIRECTORY", 0)
            try:
                target = os.open(name, flags, dir_fd=directory)
            except FileNotFoundError as exc:
                # An initial absence is not an idempotent success: increment 2A
                # owns no journal that could prove an earlier disposal.
                raise BirthSecureFSError(
                    "birth_provisioning_recovery_ambiguous"
                ) from exc
            except OSError as exc:
                raise BirthSecureFSError(
                    "birth_provisioning_io_unavailable"
                ) from exc
            try:
                value = os.fstat(target)
                observed_directory = stat.S_ISDIR(value.st_mode)
                if observed_directory != directory_expected or not (
                    observed_directory or stat.S_ISREG(value.st_mode)
                ):
                    raise BirthSecureFSError(
                        "birth_provisioning_recovery_ambiguous"
                    )
                if _posix_identity(target) != expectation.identity:
                    raise BirthSecureFSError(
                        "birth_provisioning_recovery_ambiguous"
                    )
                if value.st_nlink != expectation.links:
                    raise BirthSecureFSError(
                        "birth_provisioning_recovery_ambiguous"
                    )
                if directory_expected:
                    _verify_posix_directory(
                        target,
                        role=expectation.role,
                        expected_uid=self._expected_uid,
                    )
                    entries = _posix_inventory(target)
                    if entries != (expectation.inventory or ()):
                        raise BirthSecureFSError(
                            "birth_provisioning_recovery_ambiguous"
                        )
                else:
                    _verify_posix_file(
                        target,
                        role=expectation.role,
                        expected_uid=self._expected_uid,
                    )
                    self._verify_disposal_payload(target, expectation, value)
                identity = _posix_identity(target)
            finally:
                os.close(target)
            try:
                if directory_expected:
                    os.rmdir(name, dir_fd=directory)
                else:
                    os.unlink(name, dir_fd=directory)
                os.fsync(directory)
            except OSError as exc:
                raise BirthSecureFSError(
                    "birth_provisioning_io_unavailable"
                ) from exc
            try:
                os.open(name, flags, dir_fd=directory)
            except FileNotFoundError:
                pass
            else:
                raise BirthSecureFSError("birth_provisioning_io_unavailable")
        self._file_roles.pop(components, None)
        self._directory_roles.pop(components, None)
        self._role_overlay.pop(components, None)
        return _DispositionResult(
            identity=identity, kind=expectation.kind, removed=True,
        )

    @staticmethod
    def _verify_disposal_payload(
        target: int, expectation: _DisposalExpectation, value,
    ) -> None:
        """Check the bytes of a file on the very handle that was opened."""
        if expectation.disposal_class is _DisposalClass.partial_pending_file:
            maximum = expectation.maximum_partial_size
            if maximum is None or value.st_size > maximum:
                raise BirthSecureFSError(
                    "birth_provisioning_recovery_ambiguous"
                )
            return
        if value.st_size != expectation.expected_size:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        payload = _read_all_posix(target, value.st_size)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if digest != expectation.content_sha256:
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")

    def _remap_cached_directories(
        self, source: tuple[str, ...], destination: tuple[str, ...]
    ) -> None:
        moved = {
            key: destination + key[len(source) :]
            for key in tuple(self._directories)
            if key[: len(source)] == source
        }
        if any(target in self._directories and target not in moved for target in moved.values()):
            raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
        for key, target in moved.items():
            self._directories[target] = self._directories.pop(key)
            self._directory_roles[target] = self._directory_roles.pop(key)
        moved_files = {
            key: destination + key[len(source) :]
            for key in tuple(self._file_roles)
            if key[: len(source)] == source
        }
        for key, target in moved_files.items():
            self._file_roles[target] = self._file_roles.pop(key)


class _LegacyReadSession:
    """Path compatibility facade with no provisioning or global-lock capability."""

    __slots__ = ("_session",)

    def __init__(self, token: object, session: _SecureRootSession) -> None:
        if token is not _LEGACY_TOKEN:
            raise TypeError("private legacy session")
        self._session = session

    def __enter__(self) -> "_LegacyReadSession":
        self._session._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def open_directory(
        self, components: tuple[str, ...], *, exact_private: bool | None = None
    ) -> _SecureDirectoryHandle:
        role = (
            self._session._root_role
            if exact_private is None
            else _BirthObjectRole.historical_private
            if exact_private
            else _BirthObjectRole.historical_public
        )
        return self._session.open_directory(
            components, role=role
        )

    def read_file(
        self,
        components: tuple[str, ...],
        *,
        maximum: int,
        exact_private: bool = True,
    ) -> bytes:
        role = (
            _BirthObjectRole.historical_private
            if exact_private
            else _BirthObjectRole.historical_public
        )
        return self._session.read_file(
            components, maximum=maximum, role=role
        )

    def inventory(self, components: tuple[str, ...]) -> tuple[str, ...]:
        return self._session.inventory(components)

    def _inventory_state(
        self, components: tuple[str, ...]
    ) -> tuple[_InventoryEntry, ...]:
        return self._session._inventory_state(components)

    @contextlib.contextmanager
    def local_lock(
        self,
        directory: tuple[str, ...],
        *,
        exclusive: bool = False,
        create: bool = False,
        timeout: float = 5.0,
    ) -> Iterator[None]:
        if exclusive or create:
            raise BirthSecureFSError("birth_provisioning_lock_unsafe")
        with self._session.local_lock(
            directory,
            exclusive=False,
            create=False,
            timeout=timeout,
        ):
            yield


_SESSION_TOKEN = object()
_LEGACY_TOKEN = object()
_ADOPTED_DESCRIPTOR_IDS: dict[int, weakref.ReferenceType[_AuthenticatedRootDescriptor]] = {}
_ADOPTED_DESCRIPTOR_LOCK = threading.Lock()


def _historical_role_catalog_v1(
    role: _BirthObjectRole,
) -> _BirthRoleCatalogV1:
    """Constant profile of one historical compatibility root.

    The legacy facades receive a Path chosen by a caller that predates the
    Birth layout, so their catalogue is constant in role rather than in names:
    the whole subtree carries the same historical profile and no Birth pattern
    is enabled.  The exact name set of the three loaders is closed by G2.
    """
    if role not in {
        _BirthObjectRole.historical_private,
        _BirthObjectRole.historical_public,
    }:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    return _BirthRoleCatalogV1(
        schema_version=1,
        patterns=(),
        exact_bindings=(
            _BirthRoleBindingV1(
                components=(), kind=_ObjectKind.directory, role=role,
            ),
        ),
        generation=0,
    )


def _open_legacy_root_session(
    root: Path, *, exact_private: bool = True
) -> _LegacyReadSession:
    """Open one historical Path root through a read-only compatibility facade."""
    if not isinstance(root, Path):
        root = Path(root)
    if os.name == "nt":
        handles, absolute = _open_win_root(root)
        expected_uid = None
        service_sid = _windows_service_sid_for_current_process()
    else:
        expected_uid = os.geteuid() if exact_private else None
        handles, absolute = _open_posix_root(
            root, exact_private=exact_private, expected_uid=expected_uid
        )
        service_sid = None
    root_role = (
        _BirthObjectRole.historical_private
        if exact_private
        else _BirthObjectRole.historical_public
    )
    session = _SecureRootSession(
        _SESSION_TOKEN,
        handles,
        absolute,
        identity=_PlatformIdentity(
            posix_uid=expected_uid, windows_service_sid=service_sid
        ),
        role_catalog=_historical_role_catalog_v1(root_role),
    )
    return _LegacyReadSession(_LEGACY_TOKEN, session)


def _adopt_authenticated_root(
    descriptor: _AuthenticatedRootDescriptor,
) -> _SecureRootSession:
    """Consume an installer-authenticated descriptor without accepting path policy."""
    if not isinstance(descriptor, _AuthenticatedRootDescriptor):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    descriptor_id = id(descriptor)
    with _ADOPTED_DESCRIPTOR_LOCK:
        previous = _ADOPTED_DESCRIPTOR_IDS.get(descriptor_id)
        if previous is not None and previous() is descriptor:
            raise BirthSecureFSError("birth_provisioning_io_unavailable")

        def release(reference, *, key=descriptor_id):
            with _ADOPTED_DESCRIPTOR_LOCK:
                if _ADOPTED_DESCRIPTOR_IDS.get(key) is reference:
                    _ADOPTED_DESCRIPTOR_IDS.pop(key, None)

        _ADOPTED_DESCRIPTOR_IDS[descriptor_id] = weakref.ref(descriptor, release)
    identity = descriptor.identity
    if os.name == "nt":
        if identity.windows_service_sid is None or identity.posix_uid is not None:
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    elif identity.posix_uid is None or identity.windows_service_sid is not None:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    return _SecureRootSession(
        _SESSION_TOKEN,
        descriptor.handles,
        descriptor.root_path,
        identity=descriptor.identity,
        role_catalog=descriptor.role_catalog,
    )


def _read_path_once(
    path: Path, *, maximum: int, exact_private: bool
) -> bytes:
    path = Path(path)
    with _open_legacy_root_session(
        path.parent, exact_private=exact_private
    ) as session:
        return session.read_file(
            (path.name,), maximum=maximum, exact_private=exact_private
        )


def _write_all_posix(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        try:
            count = os.write(fd, view[written:])
        except InterruptedError:
            continue
        if count <= 0:
            raise OSError(errno.EIO, "short write")
        written += count


def _read_all_posix(fd: int, size: int) -> bytes:
    result = bytearray()
    while len(result) <= size:
        block = os.read(fd, min(8192, size + 1 - len(result)))
        if not block:
            break
        result.extend(block)
    return bytes(result)


def _posix_inventory(directory: int, resolve=None) -> tuple[_InventoryEntry, ...]:
    """Build the shared record for one directory, refusing foreign types.

    Every entry is reopened relative to the parent descriptor without following
    links.  A symbolic link, a hard link, any other type or an entry the
    catalogue cannot classify is refused before the record exists, so the
    closed kind never has to grow a third value (section 16.3, R7).
    """
    result: list[_InventoryEntry] = []
    names = tuple(os.listdir(directory))
    flags = (
        getattr(os, "O_PATH", os.O_RDONLY)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for raw_name in names:
        name = _relative_components((raw_name,))[0]
        handle = os.open(name, flags, dir_fd=directory)
        try:
            value = os.fstat(handle)
            directory_entry = stat.S_ISDIR(value.st_mode)
            if not directory_entry and not stat.S_ISREG(value.st_mode):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            if not directory_entry and value.st_nlink != 1:
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            kind = (
                _ObjectKind.directory
                if directory_entry
                else _ObjectKind.regular_file
            )
            binding = resolve((name,)) if resolve is not None else None
            if binding is not None and binding.kind is not kind:
                raise BirthSecureFSError("birth_provisioning_acl_unsafe")
            result.append(
                _InventoryEntry(
                    name=name,
                    identity=_ObjectIdentity(
                        f"{value.st_dev:x}", f"{value.st_ino:x}"
                    ),
                    kind=kind,
                    role=(
                        binding.role
                        if binding is not None
                        else _BirthObjectRole.birth_integrity_only
                    ),
                    links=value.st_nlink,
                    size=None if directory_entry else value.st_size,
                )
            )
        finally:
            os.close(handle)
    return tuple(sorted(result, key=lambda item: item.name.encode("utf-8")))


def _renameat2_no_replace(
    source_fd: int, source_name: str, target_fd: int, target_name: str
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_fd,
        os.fsencode(source_name),
        target_fd,
        os.fsencode(target_name),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise BirthSecureFSError("birth_provisioning_transaction_conflict")
    if error in {
        errno.EXDEV,
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise BirthSecureFSError("birth_provisioning_atomic_install_unsupported")
    raise OSError(error, "renameat2")


def _win_write_all(handle: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        block = payload[written : written + 1024 * 1024]
        buffer = ctypes.create_string_buffer(block)
        count = wintypes.DWORD()
        if not _KERNEL32.WriteFile(
            handle, buffer, len(block), ctypes.byref(count), None
        ):
            raise _win_error("WriteFile")
        if count.value <= 0:
            raise OSError(errno.EIO, "WriteFile")
        written += count.value


def _win_destination_exists(parent_path: str, name: str, directory: bool) -> bool:
    handle = None
    try:
        handle = _win_open_path(os.path.join(parent_path, name), directory=directory)
        return True
    except OSError as exc:
        if exc.errno in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            return False
        raise
    finally:
        if handle is not None:
            _win_close(handle)


def _win_inventory(handle: int) -> tuple[_InventoryEntry, ...]:
    result: list[_InventoryEntry] = []
    volume = _win_info(handle)[0].volume
    first = True
    buffer = ctypes.create_string_buffer(64 * 1024)
    while True:
        info_class = (
            _FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS
            if first
            else _FILE_ID_EXTD_DIRECTORY_INFO_CLASS
        )
        first = False
        if not _KERNEL32.GetFileInformationByHandleEx(
            handle, info_class, buffer, len(buffer)
        ):
            error = ctypes.get_last_error()
            if error == _ERROR_NO_MORE_FILES:
                break
            raise OSError(error, "GetFileInformationByHandleEx(directory)")
        offset = 0
        while True:
            entry = _FILE_ID_EXTD_DIR_INFO.from_buffer(buffer, offset)
            name_offset = offset + _FILE_ID_EXTD_DIR_INFO.FileName.offset
            if entry.FileNameLength % 2 or name_offset + entry.FileNameLength > len(buffer):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            name = ctypes.wstring_at(
                ctypes.addressof(buffer) + name_offset, entry.FileNameLength // 2
            )
            if name not in {".", ".."}:
                name = _relative_components((name,))[0]
                result.append(
                    _InventoryEntry(
                        name,
                        _ObjectIdentity(volume, bytes(entry.FileId.Identifier).hex()),
                        bool(entry.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY),
                        1,
                    )
                )
            if entry.NextEntryOffset == 0:
                break
            if entry.NextEntryOffset < _FILE_ID_EXTD_DIR_INFO.FileName.offset:
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
            offset += entry.NextEntryOffset
            if offset >= len(buffer):
                raise BirthSecureFSError("birth_provisioning_recovery_ambiguous")
    return tuple(sorted(result, key=lambda item: item.name.encode("utf-8")))
