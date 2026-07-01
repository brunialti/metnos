#!/usr/bin/env python3
"""set_credentials — cifra e salva una credenziale nello storage di Metnos.

Vettoriale §2.1 (output sempre lista): `results: list[dict]` di lunghezza
0 (decisione differita) o 1 (write completata).

Verbo `set` §2.2: modifica valore di configurazione. Reverse pattern §2.3:
`delete_credentials_by_id` (catalogo 5a famiglia).

Invariante di sicurezza (capability `metnos:credentials_metadata_only`):
i `fields` arrivano cleartext in INPUT (necessario, e' la write) ma il
return contiene SOLO metadati. `_assert_no_secrets_in_return` enforca
l'invariante prima del return finale.

Pattern §6 conferma sovrascrittura: se binding esiste e `replace=False`,
ritorna `decision='needs_inputs'` con dialogo yes_no (ADR 0090). I
`fields` cleartext NON vanno nel payload del dialog (sarebbero visibili
al PLANNER): sono stashati in un pending store opaco sotto
`<CRED_DIR>/_pending/<pending_id>.json` (mode 0600); il callback porta
solo `pending_id` opaco. Il pending file viene rimosso dopo il resume.

Determinismo §7.9: niente LLM, solo Fernet+HKDF via `runtime/credentials.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets as _secrets
import sys
import time
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

import credentials as _cred  # noqa: E402


# Invariante credentials_metadata_only centralizzata in runtime/credentials.py
# (ADR 0123, regola del 3 §7.2).
from credentials import (  # noqa: E402
    _is_empty_value,
    assert_no_secrets_in_return as _assert_no_secrets_in_return,
)

# Pending file TTL: dopo questa finestra il file viene considerato stale e
# rifiutato (forza un nuovo dialog). Conserva sicurezza in caso di crash.
_PENDING_TTL_S = 600


def _override_cred_dir() -> None:
    v = os.environ.get("METNOS_USER_DATA")
    if v:
        _cred.CRED_DIR = Path(v) / "credentials"


def _pending_dir() -> Path:
    return _cred.CRED_DIR / "_pending"


def _stash_pending(payload: dict) -> str:
    """Salva payload cleartext in <CRED_DIR>/_pending/<id>.json (mode 0600).
    Ritorna l'id opaco. Il file e' fuori dalla view del PLANNER (transita
    solo come token nel callback `resume_executor_with_values`).
    """
    pd = _pending_dir()
    pd.mkdir(parents=True, exist_ok=True)
    os.chmod(pd, 0o700)
    pid = _secrets.token_urlsafe(16)
    fp = pd / f"{pid}.json"
    fp.write_text(json.dumps({"created_at": time.time(),
                              "payload": payload}, ensure_ascii=False))
    os.chmod(fp, 0o600)
    return pid


def _consume_pending(pid: str):
    """Carica e RIMUOVE il pending. Ritorna (payload | None, error | None)."""
    fp = _pending_dir() / f"{pid}.json"
    if not fp.exists():
        return None, "pending_id non trovato (scaduto o gia' consumato)"
    try:
        blob = json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return None, f"pending file corrupted: {e}"
    finally:
        try:
            fp.unlink()
        except OSError:
            pass
    created = float(blob.get("created_at") or 0)
    if (time.time() - created) > _PENDING_TTL_S:
        return None, "pending_id scaduto"
    return blob.get("payload"), None


def _fingerprint_payload(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()[:16]


def _validate_fields(fields):
    """Ritorna None se valido, altrimenti un messaggio di errore."""
    if not isinstance(fields, dict):
        return "fields must be a dict"
    if not fields:
        return "fields must be a non-empty dict"
    for k, v in fields.items():
        if not isinstance(k, str) or not k.strip():
            return "each field name must be a non-empty string"
        if _is_empty_value(v):
            return f"field {k!r} has empty value (use non-empty value or omit)"
    return None


def _build_overwrite_dialog(binding: str, field_names: list, pending_id: str) -> dict:
    """Costruisce il payload `needs_inputs` per la conferma sovrascrittura.
    NESSUN valore di `fields` nel dialog: solo nomi (metadata) + pending_id
    opaco (token URL-safe, non riconducibile ai valori).
    """
    return {
        "title": f"Sovrascrivere le credenziali per '{binding}'?",
        "dialog": [
            {
                "var": "overwrite_confirmed",
                "prompt": (
                    f"Le credenziali per '{binding}' esistono gia'. "
                    f"Vuoi sovrascriverle con i nuovi campi "
                    f"({', '.join(field_names)})?"
                ),
                "schema": {"kind": "yes_no"},
            }
        ],
        "fmt": "auto",
        "on_complete": {
            "type": "resume_executor_with_values",
            "executor": "set_credentials",
            "args_base": {
                "binding": binding,
                "pending_id": pending_id,
                "replace": True,
            },
        },
    }


def _resolve_confirmed_flag(raw):
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("yes", "si", "sì", "true", "1", "y")
    return bool(raw)


def invoke(args):
    _override_cred_dir()

    binding = args.get("binding")
    fields = args.get("fields")
    scopes = args.get("scopes")
    expires_at = args.get("expires_at")
    replace = bool(args.get("replace", False))
    overwrite_confirmed = _resolve_confirmed_flag(args.get("overwrite_confirmed"))
    pending_id = args.get("pending_id")

    if not isinstance(binding, str) or not binding.strip():
        return {"ok": False, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="binding")}
    try:
        _cred._validate_domain(binding)
    except ValueError as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="binding", reason=str(e))}

    # Resume path (pending_id): risolvi i fields da pending store.
    if pending_id is not None:
        if not isinstance(pending_id, str) or not pending_id.strip():
            return {"ok": False, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="pending_id")}
        stashed, perr = _consume_pending(pending_id)
        if perr is not None:
            return {"ok": False, "error": perr}
        if not isinstance(stashed, dict):
            return {"ok": False, "error": _msg("ERR_PENDING_PAYLOAD_MALFORMED")}
        fields = stashed.get("fields", fields)
        if scopes is None:
            scopes = stashed.get("scopes")
        if expires_at is None:
            expires_at = stashed.get("expires_at")

    err = _validate_fields(fields)
    if err is not None:
        return {"ok": False, "error": err}

    if scopes is not None and not isinstance(scopes, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="scopes", of="strings")}
    if expires_at is not None and not isinstance(expires_at, str):
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="expires_at", reason="ISO 8601")}

    exists = _cred._file_for(binding).exists()
    confirmed = replace or (overwrite_confirmed is True)

    if exists and not confirmed:
        # Stash cleartext fields OUT OF BAND. Il dialog porta solo l'id.
        pid = _stash_pending({
            "fields": fields,
            "scopes": scopes,
            "expires_at": expires_at,
        })
        field_names = sorted(str(k) for k in fields.keys())
        payload = _build_overwrite_dialog(binding, field_names, pid)
        result = {
            "ok": True,
            "decision": "needs_inputs",
            "needs_inputs": payload,
            "results": [],
            "final_message_hint": payload["title"],
        }
        _assert_no_secrets_in_return(result)
        return result

    # Cifra e scrivi. Payload include scopes/expires_at come metadati
    # cifrati (visibili a find_credentials post-decrypt).
    payload_to_store = dict(fields)
    if scopes is not None:
        payload_to_store["scopes"] = list(scopes)
    if expires_at is not None:
        payload_to_store["expires_at"] = str(expires_at)

    replaced = bool(exists)
    if replaced:
        # Wipe esplicito prima della nuova cifratura (semantica replace).
        try:
            _cred.remove(binding)
        except OSError as e:
            return {"ok": False, "error": f"cannot remove old credential: {e}"}
    try:
        _cred.store(binding, payload_to_store)
    except (OSError, FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": f"store failed: {e}"}

    fp = _fingerprint_payload(payload_to_store)
    fields_count = len([k for k in payload_to_store.keys()
                        if k not in ("scopes", "expires_at")])
    out_scopes = list(scopes) if scopes is not None else []
    result = {
        "ok": True,
        "results": [{
            "binding": binding,
            "fingerprint": fp,
            "fields_count": fields_count,
            "scopes": out_scopes,
            "replaced": replaced,
        }],
        "final_message_hint": (
            f"Credenziale '{binding}' "
            f"{'sostituita' if replaced else 'salvata'} ({fields_count} campi)."
        ),
    }
    _assert_no_secrets_in_return(result)
    return result


def main():
    run_stdio(invoke, default=str)


if __name__ == "__main__":
    main()
