#!/usr/bin/env python3
"""find_credentials — elenco metadata-only delle credenziali configurate.

Vettoriale §2.1: ritorna SEMPRE `entries: list` (N=0/1/molti).
Verbo `find` §2.2: input primario = query/pattern (substring); senza query
restituisce snapshot completo.

Invariante di sicurezza (capability `metnos:credentials_metadata_only`):
il return contiene SOLO metadati. Nessun valore cleartext puo' transitare
verso l'osservazione del PLANNER. La validazione e' enforced via
`_assert_no_secrets_in_return` chiamata prima del return finale.

Determinismo §7.9: niente LLM. Stato letto via `credentials.list_domains()`
+ `load()` (lettura interna isolata; i valori non escono dal modulo).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
import credentials as _cred  # noqa: E402


# Invariante credentials_metadata_only centralizzata in runtime/credentials.py
# (ADR 0123, regola del 3 §7.2).
from credentials import assert_no_secrets_in_return as _assert_no_secrets_in_return  # noqa: E402


def _override_cred_dir() -> None:
    """Test isolation: se METNOS_USER_DATA e' settato, sposta CRED_DIR
    sotto $METNOS_USER_DATA/credentials/. Altrimenti usa il default di
    `credentials.py` (~/.config/metnos/credentials)."""
    v = os.environ.get("METNOS_USER_DATA")
    if v:
        _cred.CRED_DIR = Path(v) / "credentials"


def _fingerprint_payload(payload: dict) -> str:
    """sha256[:16] del payload canonicalizzato (JSON sorted keys).

    Distinta da `credentials.fingerprint(domain)` che e' specifico per il
    password field: qui vogliamo un'impronta opaca dell'intera entry per
    rilevamento drift / audit.
    """
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()[:16]


def _compute_age_days(path: Path) -> int:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return -1
    now = datetime.now(timezone.utc).timestamp()
    return max(0, int((now - mtime) // 86400))


def _extract_scopes(payload: dict) -> list:
    raw = payload.get("scopes")
    if isinstance(raw, list):
        return [str(s) for s in raw if isinstance(s, (str, int, float))]
    return []


def _expires_at(payload: dict):
    """Ritorna datetime di scadenza o None. Tollerante ai formati ISO."""
    raw = payload.get("expires_at")
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _compute_status(payload: dict, fields_present: list) -> str:
    """configured | expired | incomplete.

    - incomplete: payload non ha alcun campo dato (tipico stato bootstrap).
    - expired:    presente `expires_at` ISO ed e' passato.
    - configured: caso felice.
    """
    exp = _expires_at(payload)
    if exp is not None and exp <= datetime.now(timezone.utc):
        return "expired"
    data_fields = [f for f in fields_present if f not in ("scopes", "expires_at")]
    if not data_fields:
        return "incomplete"
    return "configured"


def _build_entry(binding: str):
    """Costruisce l'entry metadata-only per un binding.

    Se il file e' illeggibile (decrypt fallito) ritorna comunque un'entry
    con status='incomplete' e fields_present=[] per non nascondere
    l'esistenza del binding (§2.8 no silent failure).
    """
    path = _cred._file_for(binding)
    age = _compute_age_days(path)
    try:
        payload = _cred.load(binding)
    except ValueError:
        return {
            "binding": binding,
            "fingerprint": "",
            "scopes": [],
            "age_days": age,
            "status": "incomplete",
            "fields_present": [],
        }
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return {
            "binding": binding,
            "fingerprint": "",
            "scopes": [],
            "age_days": age,
            "status": "incomplete",
            "fields_present": [],
        }
    # `fields_present` = lista NOMI delle chiavi presenti, senza valori.
    fields_present = sorted(str(k) for k in payload.keys())
    scopes = _extract_scopes(payload)
    fp = _fingerprint_payload(payload)
    status = _compute_status(payload, fields_present)
    return {
        "binding": binding,
        "fingerprint": fp,
        "scopes": scopes,
        "age_days": age,
        "status": status,
        "fields_present": fields_present,
    }


def _matches_query(entry: dict, query: str) -> bool:
    q = query.lower()
    if q in entry["binding"].lower():
        return True
    for s in entry["scopes"]:
        if q in str(s).lower():
            return True
    return False


def invoke(args):
    _override_cred_dir()

    query = args.get("query")
    top_k = args.get("top_k")

    if query is not None:
        if not isinstance(query, str):
            return {"ok": False, "error": _msg("ERR_ARG_NOT_STRING", arg="query")}
        if not query.strip():
            return {"ok": False,
                    "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="query")}
    if top_k is not None:
        if not isinstance(top_k, int) or isinstance(top_k, bool):
            return {"ok": False, "error": _msg("ERR_ARG_NOT_INT", arg="top_k")}
        if top_k <= 0:
            return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="top_k")}

    bindings = _cred.list_domains()
    entries = []
    for b in bindings:
        e = _build_entry(b)
        if e is None:
            continue
        if query is not None and not _matches_query(e, query):
            continue
        entries.append(e)

    entries.sort(key=lambda e: e["binding"])
    available_total = len(entries)
    truncated = False
    if top_k is not None and available_total > top_k:
        entries = entries[:top_k]
        truncated = True

    result = {
        "ok": True,
        "entries": entries,
        "n_entries": len(entries),
    }
    if truncated:
        result["truncated"] = True
        result["truncated_what"] = "entries"
        result["used"] = len(entries)
        result["available_total"] = available_total
        result["cap_field"] = "top_k"
        result["cap_value"] = int(top_k)

    _assert_no_secrets_in_return(result)
    return result


def main():
    run_stdio(invoke, default=str)


if __name__ == "__main__":
    main()
