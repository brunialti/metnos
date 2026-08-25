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
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


_ENTRY_GENERATION_DOMAIN = b"metnos.catalog-entry/rm0007-generation/v1\0"
_ENTRY_VIRTUAL_DOMAIN = b"metnos.catalog-entry/virtual-fallback/v1\0"
_ENTRY_LEGACY_DOMAIN = b"metnos.catalog-entry/legacy-fallback/v1\0"
_CATALOG_DOMAIN = b"metnos.executor-catalog/v1\0"
_IDENTITY_PREFIX = "eci1-"

# These locations describe the installation, not the effective catalog
# contract.  Their *field names* remain in the projection through a constant
# marker, so adding/removing one is still visible without making identities
# host-path-dependent.
_DEPLOYMENT_PATH_FIELDS = frozenset({
    "code_path", "manifest_path", "authoring_manifest_path",
})


class CatalogIdentityError(ValueError):
    """An effective catalog value has no deterministic canonical encoding."""


def _hash(domain: bytes, payload: bytes) -> str:
    digest = hashlib.sha256(domain + payload).hexdigest()
    return _IDENTITY_PREFIX + digest


def _json_value(value: Any) -> Any:
    """Convert loaded catalog values to a deterministic JSON projection."""
    if isinstance(value, Enum):
        return {
            "$enum": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _json_value(value.value),
        }
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CatalogIdentityError("non-finite float in catalog identity")
        return value
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    if isinstance(value, Path):
        return {"$path": value.as_posix()}
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CatalogIdentityError("non-string mapping key in catalog identity")
        return {
            key: _json_value(value[key])
            for key in sorted(value)
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$dataclass": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": {
                field.name: _json_value(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    raise CatalogIdentityError(
        "unsupported catalog identity value: "
        f"{type(value).__module__}.{type(value).__qualname__}")


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


def _entry_fields(entry: Any) -> dict[str, Any]:
    """Read every effective field; future fields are included automatically."""
    if is_dataclass(entry) and not isinstance(entry, type):
        names = [field.name for field in fields(entry)]
    elif hasattr(entry, "__dict__"):
        names = list(vars(entry))
    else:
        raise CatalogIdentityError("catalog entry has no inspectable fields")
    private = sorted(name for name in names if name.startswith("_"))
    if private:
        raise CatalogIdentityError(
            f"unknown private catalog fields: {', '.join(private)}")
    projection = {}
    for name in sorted(names):
        projection[name] = (
            {"$excluded": "deployment-path"}
            if name in _DEPLOYMENT_PATH_FIELDS
            else getattr(entry, name)
        )
    return projection


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

    projection = _entry_fields(entry)
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
