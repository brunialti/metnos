#!/usr/bin/env python3
"""delete_credentials — rimuove credenziali dallo storage cifrato.

Pattern §2.1 (vettoriale): accetta `bindings: list`, `binding: str`, o
`all=true`. Output sempre `results: list[dict]` (anche degenere N=0).

§2.2 verbo `delete` = mutazione terminale (non reversibile auto).
Modello §2.3: `reverse_pattern` non in catalogo chiuso → `reversible=false`
(modello `delete_persons` ADR 0086 PR2). Per ripristinare: ri-eseguire
`set_credentials` con gli stessi valori.

Invariante di sicurezza (capability `metnos:credentials_metadata_only`):
prima del delete leggiamo i metadati (n campi) ma NON i valori; il return
e' validato da `_assert_no_secrets_in_return`.

Determinismo §7.9: niente LLM, solo filesystem via `runtime/credentials.py`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg  # noqa: E402
import credentials as _cred  # noqa: E402


# Invariante credentials_metadata_only centralizzata in runtime/credentials.py
# (ADR 0123, regola del 3 §7.2).
from credentials import assert_no_secrets_in_return as _assert_no_secrets_in_return  # noqa: E402


def _override_cred_dir() -> None:
    v = os.environ.get("METNOS_USER_DATA")
    if v:
        _cred.CRED_DIR = Path(v) / "credentials"


def _count_fields_for(binding: str) -> int:
    """Conta i campi 'dati' (escludendo scopes/expires_at) per metadata-only.
    Tollera decrypt fail: ritorna 0 senza propagare i valori."""
    try:
        payload = _cred.load(binding)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    return len([k for k in payload.keys() if k not in ("scopes", "expires_at")])


def _coalesce_targets(args) -> tuple[list, str | None]:
    all_flag = bool(args.get("all", False))
    bindings = args.get("bindings")
    binding = args.get("binding")

    if all_flag and (bindings is not None or binding is not None):
        return [], "'all=true' and 'bindings'/'binding' are mutually exclusive"

    if all_flag:
        return ["__ALL__"], None

    if bindings is not None:
        if not isinstance(bindings, list):
            return [], "bindings must be a list of strings"
        out = []
        for v in bindings:
            if not isinstance(v, str) or not v.strip():
                return [], "each binding must be a non-empty string"
            out.append(v.strip())
        if not out:
            return [], "bindings must be non-empty (or pass all=true)"
        return out, None

    if binding is not None:
        if not isinstance(binding, str) or not binding.strip():
            return [], "binding must be a non-empty string"
        return [binding.strip()], None

    return [], "must provide one of: bindings, binding, all=true"


def invoke(args):
    _override_cred_dir()

    targets, err = _coalesce_targets(args)
    if err is not None:
        return {"ok": False, "error": err}

    results: list = []
    unknown_bindings: list = []

    if targets == ["__ALL__"]:
        all_bindings = _cred.list_domains()
        if not all_bindings:
            result = {
                "ok": True, "results": [], "n_deleted": 0,
                "final_message_hint": _msg("MSG_NO_CREDENTIALS_TO_DELETE"),
            }
            _assert_no_secrets_in_return(result)
            return result
        for b in all_bindings:
            n_fields = _count_fields_for(b)
            try:
                ok = _cred.remove(b)
            except OSError as e:
                results.append({
                    "binding": b, "deleted": False,
                    "removed_fields_count": 0,
                    "error": _msg("ERR_OP_FAILED", reason=str(e)),
                })
                continue
            results.append({
                "binding": b,
                "deleted": bool(ok),
                "removed_fields_count": n_fields if ok else 0,
            })
    else:
        for b in targets:
            try:
                _cred._validate_domain(b)
            except ValueError as e:
                results.append({
                    "binding": b, "deleted": False,
                    "removed_fields_count": 0,
                    "error": _msg("ERR_ARG_INVALID", arg="binding", reason=str(e)),
                })
                continue
            path = _cred._file_for(b)
            if not path.exists():
                unknown_bindings.append(b)
                results.append({
                    "binding": b, "deleted": False,
                    "removed_fields_count": 0,
                })
                continue
            n_fields = _count_fields_for(b)
            try:
                ok = _cred.remove(b)
            except OSError as e:
                results.append({
                    "binding": b, "deleted": False,
                    "removed_fields_count": 0,
                    "error": _msg("ERR_OP_FAILED", reason=str(e)),
                })
                continue
            results.append({
                "binding": b,
                "deleted": bool(ok),
                "removed_fields_count": n_fields if ok else 0,
            })

    n_deleted = sum(1 for r in results if r.get("deleted"))
    result: dict = {
        "ok": True,
        "results": results,
        "n_deleted": n_deleted,
    }
    if unknown_bindings:
        result["unknown_bindings"] = unknown_bindings
        result["final_message_hint"] = (
            f"Cancellate {n_deleted}/{len(results)} credenziali "
            f"(sconosciute: {', '.join(unknown_bindings)})."
        )
    else:
        result["final_message_hint"] = (
            f"Cancellate {n_deleted} credenziali."
        )
    _assert_no_secrets_in_return(result)
    return result


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
