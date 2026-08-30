# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve manifest-declared paired-device identities in argument values.

An argument opts in with ``paired_device_identity = "id" | "name"``.
The default mode accepts an exact identity value; the optional
``paired_device_identity_mode = "token"`` also canonicalizes identity tokens
inside a string such as a linear command argument.  Both the declaration and
the selected projection are part of the governed executor manifest.

Resolution is deterministic and owner-scoped.  The accepted aliases are only
the stable id and curated name already present in the paired-device registry;
no network address, hostname or other attribute is inferred.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import re

_PROJECTIONS = frozenset({"id", "name"})
_MODES = frozenset({"exact", "token"})
_IDENTITY_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+")


def _declared(spec: object) -> bool:
    return (
        isinstance(spec, Mapping)
        and spec.get("paired_device_identity") in _PROJECTIONS
        and spec.get("paired_device_identity_mode", "exact") in _MODES
    )


def _owner_devices(actor: str) -> tuple[object, ...]:
    import devices

    owner_id = devices.owner_id_for_actor(actor or "host")
    return tuple(
        device for device in devices.list_devices()
        if str(getattr(device, "owner_user_id", "host") or "host")
        == str(owner_id)
    )


def _alias_projection(devices: tuple[object, ...], projection: str) -> dict[str, str]:
    """Return only aliases that identify one projected value unambiguously."""
    candidates: dict[str, set[str]] = defaultdict(set)
    for device in devices:
        device_id = str(getattr(device, "id", "") or "").strip()
        name = str(getattr(device, "name", "") or "").strip()
        projected = device_id if projection == "id" else name
        if not device_id or not name or not projected:
            continue
        for alias in {device_id, name}:
            candidates[alias.casefold()].add(projected)
    return {
        alias: next(iter(values))
        for alias, values in candidates.items()
        if len(values) == 1
    }


def _resolve_string(value: str, aliases: Mapping[str, str], mode: str) -> str:
    if mode == "exact":
        return aliases.get(value.strip().casefold(), value)

    def replace(match: re.Match[str]) -> str:
        return aliases.get(match.group(0).casefold(), match.group(0))

    return _IDENTITY_TOKEN_RE.sub(replace, value)


def _resolve_declared_value(value: object, aliases: Mapping[str, str], mode: str):
    if isinstance(value, str):
        return _resolve_string(value, aliases, mode)
    if isinstance(value, list):
        return [
            _resolve_string(item, aliases, mode) if isinstance(item, str) else item
            for item in value
        ]
    return value


def _resolve_node(
        value: object, spec: object, devices: tuple[object, ...],
        projections: dict[str, dict[str, str]],
):
    if not isinstance(spec, Mapping):
        return value
    if _declared(spec):
        projection = str(spec["paired_device_identity"])
        mode = str(spec.get("paired_device_identity_mode", "exact"))
        aliases = projections.get(projection)
        if aliases is None:
            aliases = _alias_projection(devices, projection)
            projections[projection] = aliases
        return _resolve_declared_value(
            value, aliases, mode,
        )
    if isinstance(value, Mapping):
        properties = spec.get("properties")
        if not isinstance(properties, Mapping):
            return value
        return {
            key: _resolve_node(
                item, properties.get(key), devices, projections,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        item_spec = spec.get("items")
        if not isinstance(item_spec, Mapping):
            return value
        return [
            _resolve_node(item, item_spec, devices, projections)
            for item in value
        ]
    return value


def _has_declaration(spec: object) -> bool:
    if not isinstance(spec, Mapping):
        return False
    if _declared(spec):
        return True
    properties = spec.get("properties")
    if isinstance(properties, Mapping) and any(
            _has_declaration(child) for child in properties.values()):
        return True
    items = spec.get("items")
    return isinstance(items, Mapping) and _has_declaration(items)


def resolve_paired_device_args(
        args: dict, args_schema: dict | None, *, actor: str) -> dict:
    """Return a resolved copy when the signed schema opts in; otherwise no-op.

    Registry access is deliberately lazy: executors without the declaration
    neither read nor depend on paired-device state.
    """
    if not isinstance(args, dict) or not _has_declaration(args_schema):
        return args
    devices = _owner_devices(actor)
    if not devices:
        return args
    resolved = _resolve_node(args, args_schema or {}, devices, {})
    return resolved if isinstance(resolved, dict) else args
