"""install_resume_state — storage cross-turn per il pattern
install-on-demand (binary_missing → admin install → resume executor).

Quando un executor fallisce con `error_class="binary_missing"` e
agent_runtime inietta automaticamente uno step `admin(cmd=apt install)`,
admin chiede consent HMAC e termina T1 con CARD approval. Il T2 inizia
quando l'utente clicca "approva" sulla card: admin re-invocato con
token valido, esegue, e qui leggiamo `pending_install_resume` per
ri-eseguire l'executor originale con gli args_base, chiudendo il loop.

Storage: 1 file JSON per pending in
`~/.local/share/metnos/pending_install_resume/<sha16(signature)>.json`.
TTL allineato al consent_token (600s, ADR 0088). Cleanup lazy al load.

Key = sha256(admin_signature)[:16]. `admin_signature` e' la canonical
argv ("/usr/bin/apt install libheif-examples") prodotta dal vaglio,
NON il consent_token (che e' segreto). Sicuro da loggare.

Determinismo §7.9: zero LLM, zero negotiation, accept-or-fail.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


import config as _C  # §7.11
_STATE_DIR = _C.PATH_USER_DATA / "pending_install_resume"
_TTL_S = 600  # uguale a admin consent_token TTL (ADR 0088)


def _key_of(signature: str) -> str:
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


def save(*, admin_signature: str, executor: str, args_base: dict,
            actor: str, channel: str | None) -> None:
    """Persisti pending_install_resume per il consent in volo.

    Chiamato in T1 subito dopo che agent_runtime ha iniettato l'admin
    step e admin ha ritornato approval_required + signature.
    """
    if not admin_signature or not executor:
        return
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {
        "admin_signature": admin_signature,
        "executor": executor,
        "args_base": args_base,
        "actor": actor or "host",
        "channel": channel or "",
        "ts": time.time(),
    }
    fp = _STATE_DIR / f"{_key_of(admin_signature)}.json"
    fp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def load(admin_signature: str) -> dict | None:
    """Carica pending_install_resume associato all'admin signature.

    Chiamato in T2 quando admin completa con ok=True. Se pending
    esiste e non scaduto, ritorna lo state per ri-esecuzione. None
    altrimenti (pending mai creato OR scaduto OR file corrotto).
    """
    if not admin_signature:
        return None
    fp = _STATE_DIR / f"{_key_of(admin_signature)}.json"
    if not fp.exists():
        return None
    try:
        st = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(st, dict):
        return None
    if time.time() - float(st.get("ts", 0)) > _TTL_S:
        # Scaduto: cleanup lazy
        try:
            fp.unlink()
        except OSError:
            pass
        return None
    return st


def delete(admin_signature: str) -> None:
    """Rimuovi pending_install_resume dopo resume completato.

    Chiamato in T2 subito dopo che l'executor originale e' stato
    ri-eseguito (success o fail, non importa: il pending e' consumato).
    """
    if not admin_signature:
        return
    fp = _STATE_DIR / f"{_key_of(admin_signature)}.json"
    try:
        fp.unlink()
    except OSError:
        pass


def cleanup_expired() -> int:
    """Cleanup esplicito dei pending scaduti. Ritorna n rimossi.

    Chiamabile da task scheduler v2 daily, o lazy al boot.
    """
    if not _STATE_DIR.exists():
        return 0
    now = time.time()
    n = 0
    for fp in _STATE_DIR.glob("*.json"):
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
            if now - float(st.get("ts", 0)) > _TTL_S:
                fp.unlink()
                n += 1
        except (json.JSONDecodeError, OSError):
            try:
                fp.unlink()
                n += 1
            except OSError:
                pass
    return n
