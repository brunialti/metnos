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
import credential_mandates as _mandates  # noqa: E402
import sites_origin as _sorigin  # noqa: E402  — ADR 0191 P2


# Invariante credentials_metadata_only centralizzata in runtime/credentials.py
# (ADR 0123, regola del 3 §7.2).
from credentials import (  # noqa: E402
    _is_empty_value,
    assert_no_secrets_in_return as _assert_no_secrets_in_return,
)

# Pending file TTL: dopo questa finestra il file viene considerato stale e
# rifiutato (forza un nuovo dialog). Conserva sicurezza in caso di crash.
_PENDING_TTL_S = 600


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


def _normalize_origins(raw):
    """Normalizza/valida `credential_origins` (ADR 0191 P2). Ritorna
    (list|None, error): list None = non fornito; ogni voce canonicalizzata a
    `scheme://host:port` (https, o http solo su host LAN/loopback)."""
    if raw is None:
        return None, None
    if not isinstance(raw, (list, tuple)):
        return None, "credential_origins must be a list of origins"
    out = []
    for entry in raw:
        origin = _sorigin.normalize_entry(str(entry))
        if not origin:
            return None, (f"invalid credential origin: {entry!r} "
                          "(https, or http only on LAN/loopback host)")
        if origin not in out:
            out.append(origin)
    # Fix adversarial #3: una lista fornita ma vuota diventerebbe deny-all
    # silenzioso; se l'intento e' il default, ometti l'arg. Fail-closed esplicito.
    if not out:
        return None, "credential_origins must contain at least one valid origin"
    return out, None


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


def _is_site_payload(binding: str, payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    fields = payload.get("form_data")
    fields = fields if isinstance(fields, dict) else payload
    has_user = any(fields.get(key) for key in ("username", "user"))
    has_password = any(fields.get(key) for key in (
        "password", "pwd", "passwd"))
    context = payload.get("context") or {}
    return bool(has_user and has_password and (
        context.get("binding") == "web" or "." in binding))


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


def _build_save_policy_dialog(binding: str, field_names: list,
                              pending_id: str, *, overwrite: bool) -> dict:
    dialog = []
    if overwrite:
        dialog.append({
            "var": "overwrite_confirmed",
            "prompt": _msg("MSG_CREDENTIAL_OVERWRITE_PROMPT",
                           binding=binding, fields=", ".join(field_names)),
            "schema": {"kind": "yes_no"},
        })
    dialog.append(_mandates.dialog_step())
    return {
        "title": _msg("MSG_CREDENTIAL_MANDATE_TITLE", binding=binding),
        "dialog": dialog, "fmt": "auto",
        "on_complete": {
            "type": "resume_executor_with_values",
            "executor": "set_credentials",
            "args_base": {
                "binding": binding, "pending_id": pending_id,
                "replace": False, "_mandate_form": True,
            },
        },
    }


def _build_policy_update_dialog(binding: str) -> dict:
    return {
        "title": _msg("MSG_CREDENTIAL_MANDATE_TITLE", binding=binding),
        "dialog": [_mandates.dialog_step()], "fmt": "auto",
        "on_complete": {
            "type": "resume_executor_with_values",
            "executor": "set_credentials",
            "args_base": {"binding": binding, "_mandate_form": True},
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
    binding = args.get("binding")
    fields = args.get("fields")
    scopes = args.get("scopes")
    credential_mandate = args.get("credential_mandate")
    expires_at = args.get("expires_at")
    replace = bool(args.get("replace", False))
    overwrite_confirmed = _resolve_confirmed_flag(args.get("overwrite_confirmed"))
    pending_id = args.get("pending_id")
    credential_origins = args.get("credential_origins")  # ADR 0191 P2
    mandate_form = args.get("_mandate_form") is True

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
        if credential_origins is None:
            credential_origins = stashed.get("credential_origins")

    # ADR 0191 P2: valida subito le origini fornite (anche da resume).
    origins_norm, origins_err = _normalize_origins(credential_origins)
    if origins_err is not None:
        return {"ok": False, "error": origins_err, "error_class": "invalid_args"}

    exists = _cred._file_for(binding).exists()
    existing_payload = None
    if exists:
        try:
            existing_payload = _cred.load(binding)
        except (OSError, ValueError) as e:
            return {"ok": False, "error": f"store failed: {e}"}
    # The planner can request a policy change, but only the form may select
    # the persisted profile. The marker is internal and absent from manifest.
    if credential_mandate is not None and not mandate_form:
        credential_mandate = None
    if (fields is None and credential_mandate is None
            and _is_site_payload(binding, existing_payload)):
        payload = _build_policy_update_dialog(binding)
        result = {
            "ok": True, "decision": "needs_inputs",
            "needs_inputs": payload, "results": [],
            "final_message_hint": payload["title"],
        }
        _assert_no_secrets_in_return(result)
        return result
    if credential_mandate is not None:
        try:
            current_scopes = scopes
            if current_scopes is None and exists:
                current_scopes = (existing_payload or {}).get("scopes")
            scopes = _mandates.apply_profile(
                current_scopes, str(credential_mandate))
        except (OSError, ValueError) as e:
            return {"ok": False, "error": _msg(
                "ERR_ARG_INVALID", arg="credential_mandate", reason=str(e))}
    if scopes is not None:
        try:
            scopes = _mandates.validate_scopes(scopes)
        except ValueError as e:
            return {"ok": False, "error": _msg(
                "ERR_ARG_INVALID", arg="scopes", reason=str(e))}
    if expires_at is not None and not isinstance(expires_at, str):
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="expires_at", reason="ISO 8601")}

    # Metadata-only update: preserves every secret field and changes only the
    # encrypted usage policy. This is the natural "allow these credentials to
    # read" operation and does not require re-entering the password.
    if fields is None:
        if exists and scopes is None:
            payload = _build_policy_update_dialog(binding)
            result = {
                "ok": True, "decision": "needs_inputs",
                "needs_inputs": payload, "results": [],
                "final_message_hint": payload["title"],
            }
            _assert_no_secrets_in_return(result)
            return result
        if not exists or scopes is None:
            return {"ok": False, "error": _msg(
                "ERR_ARG_MISSING", arg="fields or scopes")}
        try:
            payload_to_store = existing_payload
            if not isinstance(payload_to_store, dict):
                raise ValueError("credential not found")
            payload_to_store["scopes"] = list(scopes)
            if expires_at is not None:
                payload_to_store["expires_at"] = str(expires_at)
            _cred.store(binding, payload_to_store)
        except (OSError, FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": f"store failed: {e}"}
        fields_count = len([k for k in payload_to_store
                            if k not in ("scopes", "expires_at")])
        result = {
            "ok": True, "results": [{
                "binding": binding,
                "fingerprint": _fingerprint_payload(payload_to_store),
                "fields_count": fields_count, "scopes": list(scopes),
                "replaced": True,
            }],
            "final_message_hint": _msg(
                "MSG_CREDENTIAL_MANDATE_SAVED", binding=binding),
        }
        _assert_no_secrets_in_return(result)
        return result

    err = _validate_fields(fields)
    if err is not None:
        return {"ok": False, "error": err}

    # Fix adversarial #7: un record e' un SITO se e' marcato `web` o ha una
    # chiave a dominio (con almeno un campo credenziale), indipendentemente dal
    # fatto che usi `username` o `email`, o sia passwordless — cosi' anche questi
    # ottengono `credential_origins`.
    _cred_fields = ("username", "user", "email", "password", "pwd", "passwd")
    is_site_binding = (
        isinstance(fields, dict)
        and any(key in fields for key in _cred_fields)
        and (fields.get("binding") == "web" or "." in binding))
    if is_site_binding and pending_id is None:
        pid = _stash_pending({
            "fields": fields, "scopes": scopes, "expires_at": expires_at,
            "credential_origins": credential_origins,
        })
        payload = _build_save_policy_dialog(
            binding, sorted(str(k) for k in fields), pid,
            overwrite=exists)
        result = {
            "ok": True, "decision": "needs_inputs",
            "needs_inputs": payload, "results": [],
            "final_message_hint": payload["title"],
        }
        _assert_no_secrets_in_return(result)
        return result

    if overwrite_confirmed is False:
        return {"ok": False, "error_class": "cancelled",
                "error": _msg("MSG_ORCH_STOPPING"), "results": []}
    confirmed = replace or (overwrite_confirmed is True)

    if exists and not confirmed:
        # Stash cleartext fields OUT OF BAND. Il dialog porta solo l'id.
        pid = _stash_pending({
            "fields": fields,
            "scopes": scopes,
            "expires_at": expires_at,
            "credential_origins": credential_origins,
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
    # ADR 0191 P2 (rev. 14/7): origini esplicite = autorita' ESATTA fail-closed.
    # Senza origini la chiave resta ASSENTE: autorita' = STESSO SITO del binding
    # (sottodomini first-party inclusi), risolta a runtime da `origin_authorized`.
    if is_site_binding and origins_norm is not None:
        payload_to_store["credential_origins"] = origins_norm

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
                        if k not in ("scopes", "expires_at",
                                     "credential_origins")])
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
