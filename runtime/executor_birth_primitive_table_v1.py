"""The closed set of primitives the Birth properties may reach (RM-0008).

Group 2 left ``primitive_allowlist`` in the admission context as an identity
with nothing behind it: changing its digest changed no reachability, because
the reachable set lived in a registry nobody compared it against.

This module owns that set, once.  The property runner resolves against it and
refuses anything outside it; the same enumeration is what the context component
will carry when the last step of group 3 rebuilds identity and epoch.  One
owner means the two can no longer drift apart without somebody noticing.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Mapping

# Every identifier a property may name, by kind.  Adding one here is a
# deliberate act reviewed as a whole, exactly like the context catalogue.
PRIMITIVE_TABLE_V1: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "applicability": (
        "bounded_collection_input",
        "collection_output",
        "destructive_with_undo",
        "entries_and_results_output",
        "output_schema_declared",
        "revertible_executor",
        "truncation_declared",
    ),
    "fixture": (
        "bounded_collection",
        "empty_private_root",
        "oversized_collection",
        "private_deletion_tree",
        "private_mutable_state",
    ),
    "generator": (
        "cardinality_cases",
        "declared_output_cases",
        "delete_copy_cases",
        "entries_results_cases",
        "limit_boundary_cases",
        "truncation_cases",
        "undo_round_trip_cases",
    ),
    "oracle": (
        "cardinality",
        "copy_precedes_delete",
        "entries_results_coherence",
        "limit_semantics",
        "output_schema",
        "state_round_trip",
        "truncation",
    ),
})

PRIMITIVE_TABLE_DOMAIN_V1 = b"metnos.executor-birth.primitive-table/v1\0"


class PrimitiveTableError(RuntimeError):
    """A primitive was named that the closed table does not contain."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def primitive_table_digest_v1() -> str:
    """One digest over the whole table, for the context component to carry."""
    payload = json.dumps(
        {kind: list(names) for kind, names in PRIMITIVE_TABLE_V1.items()},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(
        PRIMITIVE_TABLE_DOMAIN_V1 + payload
    ).hexdigest()


def check_primitive_v1(kind: str, identifier: str) -> str:
    """Return the identifier when the table admits it, refuse otherwise."""
    admitted = PRIMITIVE_TABLE_V1.get(kind)
    if admitted is None:
        raise PrimitiveTableError("primitive_kind_unknown", kind)
    if identifier not in admitted:
        raise PrimitiveTableError("primitive_not_admitted", f"{kind}:{identifier}")
    return identifier


def check_registry_v1(kind: str, names) -> None:
    """Refuse a registry that does not match the table exactly.

    Both directions matter: an entry the table does not list is reachable
    without review, and a listed entry with no implementation is an identity
    that promises something absent.
    """
    admitted = PRIMITIVE_TABLE_V1.get(kind)
    if admitted is None:
        raise PrimitiveTableError("primitive_kind_unknown", kind)
    observed = tuple(sorted(names))
    if observed != admitted:
        raise PrimitiveTableError(
            "primitive_registry_mismatch",
            f"{kind}:{'|'.join(set(observed) ^ set(admitted))}",
        )
