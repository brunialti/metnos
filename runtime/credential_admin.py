# SPDX-License-Identifier: MIT
"""Administrative view of the credential vault, never its secret values.

The Settings page lists every stored credential: its record name, its kind,
the fields the kind declares non-secret (for example the user name), the
sign-in origins it may be filled on and the delegated origins the user
approved and Metnos remembers. Secret fields, declared by the same signed
`[credential_form]` specs that collect them, never leave this module.
"""
from __future__ import annotations

import datetime as _dt

import credentials
import sites_origin


def _kinds() -> dict:
    try:
        return credentials.credential_form_kinds()
    except Exception:  # noqa: BLE001 -- a broken catalog still lists records
        return {}


def _stored_names(payload: dict) -> set[str]:
    form = payload.get("form_data") if isinstance(payload.get("form_data"), dict) else {}
    return {str(name) for name in (*payload, *form)}


def _kind_of(binding: str, payload: dict, kinds: dict) -> str | None:
    """Kind by the declared name prefix, else by the stored field names.

    A record saved under a plain site name carries no prefix: the kind whose
    required fields are all present and that explains most stored fields
    wins, the more specific one (more required fields) on equal coverage; a
    remaining tie stays unknown rather than guessed.
    """
    try:
        kind = credentials.kind_for_binding(binding, kinds)
    except Exception:  # noqa: BLE001
        kind = None
    if kind or not payload:
        return kind
    stored = _stored_names(payload)
    scored = []
    for slug, spec in kinds.items():
        fields = [f for f in spec.get("fields") or () if isinstance(f, dict)]
        required = {str(f.get("name")) for f in fields if f.get("required")}
        if not required or not required <= stored:
            continue
        coverage = len({str(f.get("name")) for f in fields} & stored)
        scored.append(((coverage, len(required)), slug))
    scored.sort(reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


def _public_fields(payload: dict, spec: dict) -> list[dict]:
    """Values of the fields the kind declares non-secret, in spec order."""
    form = payload.get("form_data") if isinstance(payload.get("form_data"), dict) else {}
    out = []
    for field in spec.get("fields") or ():
        if not isinstance(field, dict) or field.get("secret") is not False:
            continue
        name = str(field.get("name") or "")
        value = payload.get(name, form.get(name))
        if isinstance(value, (str, int, float)) and str(value).strip():
            out.append({"name": name, "label_key": str(field.get("prompt_key") or ""),
                        "text": str(value)})
    return out


def _updated(binding: str) -> str:
    try:
        mtime = credentials._file_for(binding).stat().st_mtime
    except (OSError, ValueError):
        return ""
    return _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def list_credentials() -> list[dict]:
    """One row per stored record; unreadable records are listed, not hidden."""
    kinds = _kinds()
    rows = []
    for binding in credentials.list_domains():
        try:
            payload = credentials.load(binding) or {}
            readable = True
        except Exception:  # noqa: BLE001 -- show it as unreadable
            payload, readable = {}, False
        kind = _kind_of(binding, payload, kinds) if kinds else None
        spec = kinds.get(kind) or {}
        explicit = sites_origin.explicit_origins(payload)
        rows.append({
            "binding": binding,
            "kind": kind or "",
            "kind_label_key": str(spec.get("label_key") or ""),
            "readable": readable,
            "fields": _public_fields(payload, spec) if readable else [],
            "origin_regime": "explicit" if explicit is not None else "same_site",
            "origins": explicit or [],
            "approved_origins": sites_origin.approved_origins(payload),
            "updated": _updated(binding),
        })
    credentials.assert_no_secrets_in_return(rows)
    return rows


def _known(binding: str) -> bool:
    return isinstance(binding, str) and binding in credentials.list_domains()


def forget_origin(binding: str, origin: str) -> bool:
    """Remove one remembered delegated origin; the next login asks again."""
    if not _known(binding):
        return False
    return credentials.forget_approved_origin(binding, str(origin or ""))


def delete(binding: str) -> bool:
    """Delete the whole record, exactly like the chat deletion."""
    if not _known(binding):
        return False
    return credentials.remove(binding)
