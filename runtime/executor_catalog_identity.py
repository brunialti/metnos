# SPDX-License-Identifier: AGPL-3.0-only
"""Common cache identity for one effective executor catalog.

RM-0008 requires every catalog-dependent cache to agree on one identity.  A
published executor is identified by its RM-0007 generation; entries which do
not yet have a published generation use an explicitly domain-separated digest
of their effective virtual or legacy catalog projection.  Lifecycle is always
bound separately because an effective visibility override can change without
rewriting the admitted generation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


_ENTRY_GENERATION_DOMAIN = b"metnos.catalog-entry/rm0007-generation/v1\0"
_ENTRY_VIRTUAL_DOMAIN = b"metnos.catalog-entry/virtual-fallback/v1\0"
_ENTRY_LEGACY_DOMAIN = b"metnos.catalog-entry/legacy-fallback/v1\0"
_CATALOG_DOMAIN = b"metnos.executor-catalog/v1\0"
_IDENTITY_PREFIX = "eci1-"

# These are the effective manifest/catalog attributes which can affect
# selection, presentation, invocation, or a prefilter corpus.  Published
# entries need no projection: generation_id already commits their admitted
# material.  The projection is deliberately broad for the transitional
# virtual/legacy domains so a manifest-only edit cannot retain stale caches.
_FALLBACK_FIELDS = (
    "name", "version", "description", "affinity", "args_schema",
    "capabilities", "tests", "revertible", "superseded_by",
    "reverse_pattern", "deprecation_ttl_hours", "dormant",
    "dormant_reason", "sandbox_profile", "provenance", "placement", "undo",
    "complexity", "planning_companions", "planning_object_aliases",
    "platforms", "digest", "contract_id", "executor_standard",
    "standard_state", "membership", "source", "intelligence", "transport",
    "output_schema", "presentation", "execution_policy",
    "execution_policy_declared", "managed_dependencies",
)


def _hash(domain: bytes, payload: bytes) -> str:
    digest = hashlib.sha256(domain + payload).hexdigest()
    return _IDENTITY_PREFIX + digest


def _json_value(value: Any) -> Any:
    """Convert loaded catalog values to a deterministic JSON projection."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        # Paths are deployment location, not executor semantics.
        return value.name
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return repr(value)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _is_virtual(entry: Any) -> bool:
    if getattr(entry, "membership", None) == "virtual":
        return True
    if getattr(entry, "source", None) == "virtual":
        return True
    manifest_path = getattr(entry, "manifest_path", None)
    return isinstance(manifest_path, (str, Path)) and Path(manifest_path).suffix == ".py"


def catalog_entry_identity(entry: Any) -> str:
    """Return the identity of an entry's effective cache-visible revision."""
    lifecycle = str(getattr(entry, "lifecycle", None) or "active")
    generation_id = getattr(entry, "generation_id", None)
    if generation_id:
        payload = _canonical_json({
            "generation_id": str(generation_id),
            "lifecycle": lifecycle,
        })
        return _hash(_ENTRY_GENERATION_DOMAIN, payload)

    projection = {
        field: getattr(entry, field, None)
        for field in _FALLBACK_FIELDS
    }
    projection["lifecycle"] = lifecycle
    domain = _ENTRY_VIRTUAL_DOMAIN if _is_virtual(entry) else _ENTRY_LEGACY_DOMAIN
    return _hash(domain, _canonical_json(projection))


def catalog_identity(catalog: Iterable[Any] | None) -> str:
    """Identity of the whole pool, including every sibling and lifecycle."""
    if catalog is None:
        return ""
    rows = sorted(
        (str(getattr(entry, "name", None) or ""), catalog_entry_identity(entry))
        for entry in catalog
    )
    return _hash(_CATALOG_DOMAIN, _canonical_json(rows))

