"""Secret-safe projection of the effective virt configuration.

The factories remain the sole authority for resolution.  This module turns
their resolved bindings into bounded data suitable for an administration UI;
it never instantiates a provider or a model and never returns raw TOML text.
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

from . import DEFAULT_EMBEDDERS, DEFAULT_VLM, tiers


_MAX_FIELDS = 128
_MAX_VALUE_CHARS = 240
_MAX_EDIT_VALUE_CHARS = 4096
UI_EDITABLE_FAMILIES = frozenset({"llm", "vlm"})
_BINDING_KEYS = frozenset({"provider", "model", "endpoint", "base_url"})
_SECRET_PARTS = frozenset({
    "accesskey", "apikey", "auth", "authorization", "bearer", "clientsecret",
    "cookie", "credential", "credentials", "key", "password", "passwd",
    "privatekey", "refresh", "secret", "session", "signature", "token",
})
_SECRET_VALUE = re.compile(
    r"(?:^\s*(?:bearer|basic)\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:sk|gh[pousr]|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)


def _name_parts(name: str) -> frozenset[str]:
    compact = re.sub(r"[^a-z0-9]", "", str(name).casefold())
    parts = {
        re.sub(r"[^a-z0-9]", "", part)
        for part in re.split(r"[^a-z0-9]+", str(name).casefold())
        if part
    }
    parts.add(compact)
    return frozenset(parts)


def _sensitive_name(path: str) -> bool:
    return bool(_name_parts(path) & _SECRET_PARTS)


def _bounded(value: object) -> str:
    text = str(value)
    if len(text) <= _MAX_VALUE_CHARS:
        return text
    return f"{text[:_MAX_VALUE_CHARS - 1]}…"


def config_revision(path: os.PathLike[str] | str) -> str:
    """Content revision used for optimistic, lost-update-safe editing."""

    target = os.fspath(path)
    try:
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        # Do not put paths or filesystem details in form state.  A stable
        # unreadable sentinel still prevents a blind overwrite.
        return f"unreadable:{exc.errno or 0}"


def _json_pointer(parts: Sequence[str]) -> str:
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _safe_url(value: str) -> tuple[str, bool]:
    """Remove URL userinfo and all query/fragment values.

    Endpoints only need scheme, host, port, and path to be useful.  Treating
    every query/fragment value as potentially sensitive also covers provider
    parameters whose future names are not yet known.
    """

    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https", "ws", "wss"}:
            return value, False
        hostname = parsed.hostname
        if not hostname:
            return "REDACTED", True
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        clean = urllib.parse.urlunsplit(
            (parsed.scheme, f"{host}{port}", parsed.path, "", ""))
        changed = clean != value
        if parsed.query:
            clean += "?…"
            changed = True
        if parsed.fragment:
            clean += "#…"
            changed = True
        return clean, changed
    except (TypeError, ValueError):
        return "REDACTED", True


def _safe_scalar(path: str, value: object) -> tuple[str, str]:
    """Return ``(display_value, visibility)`` for one scalar field."""

    if _sensitive_name(path):
        return "••••••", "redacted"
    if value is None:
        return "—", "visible"
    if isinstance(value, bool):
        return ("true" if value else "false"), "visible"
    if isinstance(value, str):
        if _SECRET_VALUE.search(value):
            return "••••••", "redacted"
        safe, changed = _safe_url(value)
        return _bounded(safe), ("sanitized" if changed else "visible")
    return _bounded(value), "visible"


def _top_level_name(path: str) -> str:
    return re.split(r"[.[]", path, maxsplit=1)[0]


def _fields(
        spec: Mapping[str, Any], *, sources: Mapping[str, str] | None = None,
        policy_keys: frozenset[str] = frozenset(),
        pointer_prefix: tuple[str, ...] = (), include_edit: bool = False,
) -> list[dict[str, str]]:
    """Flatten a TOML-shaped mapping deterministically and with hard bounds."""

    rows: list[dict[str, str]] = []
    field_sources = sources or {}

    def add(
            path: str, parts: tuple[str, ...], value: object, *,
            allow_edit: bool = True,
    ) -> None:
        if len(rows) >= _MAX_FIELDS:
            return
        display, visibility = _safe_scalar(path, value)
        top_name = _top_level_name(path)
        group = (
            "policy" if top_name in policy_keys
            else "binding" if top_name in _BINDING_KEYS
            else "parameters"
        )
        row: dict[str, Any] = {
            "name": path,
            "value": display,
            "visibility": visibility,
            "source": field_sources.get(top_name, ""),
            "group": group,
        }
        if include_edit:
            pointer = _json_pointer((*pointer_prefix, *parts))
            if isinstance(value, bool):
                value_type = "boolean"
                edit_value: object = "true" if value else "false"
            elif isinstance(value, int):
                value_type = "integer"
                edit_value = value
            elif isinstance(value, float):
                value_type = "number"
                edit_value = value
            elif isinstance(value, str):
                value_type = (
                    "textarea" if "\n" in value or len(value) > 120
                    else "string"
                )
                edit_value = value
            else:
                value_type = ""
                edit_value = ""
            row.update({
                "editable": bool(
                    allow_edit and visibility == "visible" and value_type
                    and len(str(edit_value)) <= _MAX_EDIT_VALUE_CHARS
                ),
                "edit_key": hashlib.sha256(
                    pointer.encode("utf-8")).hexdigest()[:24],
                "pointer": pointer,
                "value_type": value_type,
            })
            if row["editable"]:
                row["edit_value"] = edit_value
        rows.append(row)

    def walk(path: str, parts: tuple[str, ...], value: object) -> None:
        if len(rows) >= _MAX_FIELDS:
            return
        if _sensitive_name(path):
            add(path, parts, value)
            return
        if isinstance(value, Mapping):
            for key in sorted(value, key=lambda item: str(item)):
                key_text = str(key)
                if key_text.startswith("_"):
                    continue
                child = f"{path}.{key_text}" if path else key_text
                walk(child, (*parts, key_text), value[key])
            return
        if (isinstance(value, Sequence)
                and not isinstance(value, (str, bytes, bytearray))):
            for index, item in enumerate(value):
                walk(f"{path}[{index}]", (*parts, str(index)), item)
            if not value:
                # Empty arrays are informative but have no scalar edit
                # contract.  Treating the display marker as a string would
                # silently change the TOML type on save.
                add(path, parts, "[]", allow_edit=False)
            return
        add(path, parts, value)

    effective = dict(spec)
    if effective.get("endpoint") == effective.get("base_url"):
        effective.pop("base_url", None)
    walk("", (), effective)
    if len(rows) >= _MAX_FIELDS:
        rows.append({
            "name": "…", "value": "…", "visibility": "truncated",
            "source": "", "group": "parameters",
        })
    return rows


def _role_description_key(family: str, name: str) -> str:
    """Derive the explanatory key of one card, empty when not catalogued.

    The key is computed from the role identity, so a new tier or fast level
    gets its own text by adding two i18n rows and no code.  A role without a
    catalogued description simply shows no paragraph: the page never renders
    a missing-key marker to an administrator.
    """

    from i18n import key_exists

    slug = name.upper().replace(".", "_").replace("-", "_")
    candidate = f"UI_VIRT_ROLE_{family.upper()}_{slug}"
    return candidate if key_exists(candidate) else ""


def _role_payload(
        *, name: str, origin: str, spec: Mapping[str, Any],
        family: str = "",
        sources: Mapping[str, str] | None = None,
        policy_keys: frozenset[str] = frozenset(),
        pointer_prefix: tuple[str, ...] | None = None,
        include_edit: bool = False,
) -> dict[str, Any]:
    """Build one role card while retaining a complete flat JSON view."""

    fields = _fields(
        spec, sources=sources, policy_keys=policy_keys,
        pointer_prefix=pointer_prefix or (name,), include_edit=include_edit,
    )
    from model_identity import observation_for

    return {
        "name": name,
        "origin": origin,
        "description_key": (
            _role_description_key(family, name) if family else ""),
        "fields": fields,
        "binding_fields": [f for f in fields if f["group"] == "binding"],
        "policy_fields": [f for f in fields if f["group"] == "policy"],
        "parameter_fields": [
            f for f in fields if f["group"] == "parameters"
        ],
        "model_observation": observation_for(spec),
    }


def _family_status(*, exists: bool, error: str, error_key: str = "") -> str:
    if error or error_key:
        return "invalid"
    return "configured" if exists else "defaults"


def _llm_family(*, include_edit: bool = False) -> dict[str, Any]:
    from llm_router import (
        DEFAULT_TIERS, FAST_LEVEL_ORDER,
        INFERENCE_POLICY_KEYS, LLMRouter, TIER_ORDER,
        TierConfigError,
        complete_tier_spec, tier_config_document,
    )

    document = tier_config_document()
    error = document.error
    error_key = "UI_VIRT_ERROR_TOML" if error else ""
    resolved: dict[str, dict] = {}
    if not error:
        try:
            resolved = LLMRouter(config_path=document.path).tiers
        except TierConfigError as exc:
            # Some historical TierConfigError messages interpolate the whole
            # offending mapping.  Never carry that mapping to an HTTP view:
            # it may contain a provider credential under a future field name.
            error = ""
            if getattr(exc, "tier", ""):
                error_key = "UI_VIRT_ERROR_LLM_PROVIDER"
            else:
                error_key = "UI_VIRT_ERROR_LLM_CONFIG"
        except Exception:
            # Keep provider construction and unforeseen validation failures
            # closed too; no exception detail reaches the secret-safe view.
            error = ""
            error_key = "UI_VIRT_ERROR_LLM_CONFIG"

    if (error or error_key) and document.tiers:
        # A semantically invalid file is still useful diagnostic evidence.
        resolved = document.tiers
    elif error or error_key:
        # Parse/read failures trigger fail-soft defaults in the direct tier
        # resolver.  The family remains visibly invalid; these cards are
        # labelled as fallback, never as a valid effective configuration.
        resolved = DEFAULT_TIERS

    # An existing configuration can omit the optional frontier role. Keep the
    # complete vocabulary visible and editable without pretending
    # that an external call is currently available.  Saving this card
    # materializes the section through the same allowlisted editor as aliases.
    if not (error or error_key) and "frontier" not in resolved:
        resolved = dict(resolved)
        resolved["frontier"] = {
            "provider": "none",
            "model": DEFAULT_TIERS["frontier"]["model"],
        }

    roles = []
    for role in TIER_ORDER:
        if role not in resolved:
            continue
        spec = resolved[role]
        if error or error_key:
            origin = "fallback"
        elif role in document.tiers:
            origin = "configured"
        elif bool(spec.get("_aliased_from")):
            origin = "alias"
        else:
            origin = "defaults"

        if role == "fast":
            raw_source = document.tiers.get("fast", {})
            configured_levels = raw_source.get("level", {}) \
                if isinstance(raw_source, dict) else {}
            for level in FAST_LEVEL_ORDER:
                effective_spec = complete_tier_spec("fast", spec, level=level)
                raw_level = configured_levels.get(level, {}) \
                    if isinstance(configured_levels, dict) else {}
                raw_root_keys = {
                    str(key) for key in raw_source
                    if str(key) != "level" and not str(key).startswith("_")
                } if isinstance(raw_source, dict) else set()
                raw_level_keys = {
                    str(key) for key in raw_level
                    if not str(key).startswith("_")
                } if isinstance(raw_level, dict) else set()
                if origin == "fallback":
                    sources = {str(key): "fallback" for key in effective_spec}
                elif origin == "defaults":
                    sources = {str(key): "defaults" for key in effective_spec}
                else:
                    sources = {
                        str(key): (
                            "configured" if str(key) in raw_level_keys
                            else "configured" if str(key) in raw_root_keys
                            else "defaults"
                        )
                        for key in effective_spec
                    }
                roles.append(_role_payload(
                    name=f"fast.{level}", origin=origin, family="llm",
                    spec=effective_spec, sources=sources,
                    policy_keys=frozenset(INFERENCE_POLICY_KEYS),
                    pointer_prefix=("fast", "level", level),
                    include_edit=include_edit,
                ))
            continue

        effective_spec = complete_tier_spec(str(role), spec)
        if origin == "fallback":
            sources = {str(key): "fallback" for key in effective_spec}
        elif origin == "defaults":
            sources = {str(key): "defaults" for key in effective_spec}
        else:
            raw_source = document.tiers.get(str(role), {})
            inherited_origin = "configured"
            if origin == "alias":
                raw_source = document.tiers.get(
                    str(spec.get("_aliased_from") or "wise"), {})
                inherited_origin = "alias"
            raw_keys = {
                str(key) for key in raw_source
                if not str(key).startswith("_")
            }
            sources = {
                str(key): (
                    inherited_origin if str(key) in raw_keys else "defaults"
                )
                for key in effective_spec
            }
        roles.append(_role_payload(
            name=str(role), origin=origin, family="llm", spec=effective_spec,
            sources=sources,
            policy_keys=frozenset(INFERENCE_POLICY_KEYS),
            include_edit=include_edit,
        ))

    return {
        "key": "llm",
        "title_key": "UI_VIRT_FAMILY_LLM",
        "description_key": "UI_VIRT_FAMILY_LLM_DESC",
        "tip_key": "UI_VIRT_LLM_TIP",
        "config": {
            "path": _bounded(document.path),
            "revision": config_revision(document.path),
            "env_var": "METNOS_LLM_TIERS_CONFIG",
            "env_override": bool(os.environ.get("METNOS_LLM_TIERS_CONFIG")),
            "exists": document.exists,
            "status": _family_status(
                exists=document.exists, error=error, error_key=error_key),
            "error_key": error_key,
            "error": _bounded(error) if error else "",
        },
        "roles": roles,
    }


def _simple_family(
        *, kind: str, defaults: dict[str, dict], key: str,
        title_key: str, description_key: str,
        tip_key: str = "",
        ui_editable: bool = True,
        include_edit: bool = False,
) -> dict[str, Any]:
    document = tiers.config_document(kind)
    sections = document.sections
    role_names = list(defaults)
    role_names.extend(
        str(name) for name, value in sections.items()
        if isinstance(value, dict) and str(name) not in defaults
    )
    roles = []
    for role in role_names:
        base = dict(defaults.get(role) or next(iter(defaults.values()), {}))
        section = sections.get(role)
        if isinstance(section, dict):
            base.update(section)
        origin = (
            "fallback" if document.error
            else "configured" if isinstance(section, dict)
            else "defaults"
        )
        if document.error:
            sources = {str(name): "fallback" for name in base}
        else:
            configured_keys = (
                {str(name) for name in section}
                if isinstance(section, dict) else set()
            )
            sources = {
                str(name): (
                    "configured" if str(name) in configured_keys else "defaults"
                )
                for name in base
            }
        roles.append(_role_payload(
            name=role, origin=origin, family=kind, spec=base, sources=sources,
            include_edit=include_edit,
        ))
    env_var = f"METNOS_{kind.upper()}_TIERS_CONFIG"
    return {
        "key": key,
        "ui_editable": ui_editable,
        "title_key": title_key,
        "description_key": description_key,
        "tip_key": tip_key,
        "config": {
            "path": _bounded(document.path),
            "revision": config_revision(document.path),
            "env_var": env_var,
            "env_override": bool(os.environ.get(env_var)),
            "exists": document.exists,
            "status": _family_status(
                exists=document.exists, error=document.error,
                error_key=("UI_VIRT_ERROR_TOML" if document.error else "")),
            "error_key": (
                "UI_VIRT_ERROR_TOML" if document.error else ""),
            "error": _bounded(document.error) if document.error else "",
        },
        "roles": roles,
    }


def snapshot(*, edit_family: str = "") -> dict[str, Any]:
    """Return all model bindings as a JSON-safe, secret-safe projection.

    ``edit_family`` adds opaque edit metadata only for the selected family.
    The ordinary JSON representation remains read-only and never contains raw
    secret values.
    """

    if edit_family not in UI_EDITABLE_FAMILIES:
        edit_family = ""

    return {
        "read_only": not bool(edit_family),
        "secrets_redacted": True,
        "families": [
            {
                **_llm_family(include_edit=edit_family == "llm"),
                "ui_editable": True,
            },
            _simple_family(
                kind="embedding", defaults=DEFAULT_EMBEDDERS,
                key="embedding", title_key="UI_VIRT_FAMILY_EMBEDDING",
                description_key="UI_VIRT_FAMILY_EMBEDDING_DESC",
                tip_key="UI_VIRT_EMBEDDING_READONLY_TIP",
                ui_editable=False,
            ),
            _simple_family(
                kind="vlm", defaults=DEFAULT_VLM,
                key="vlm", title_key="UI_VIRT_FAMILY_VLM",
                description_key="UI_VIRT_FAMILY_VLM_DESC",
                tip_key="UI_VIRT_VLM_TIP",
                ui_editable=True,
                include_edit=edit_family == "vlm",
            ),
        ],
    }
