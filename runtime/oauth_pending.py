"""oauth_pending — in-memory store dei flow OAuth in attesa di callback.

Lifecycle: `start_oauth_redirect_flow` (orchestration) -> put -> ritorna
authorization_url; quando Google reindirizza a `/oauth/callback`, l'handler
HTTP -> pop -> esegue `gworkspace_oauth.finish_flow` + ri-invoca executor.

Thread-safe via threading.Lock. TTL 600s (10 min: Google tipicamente
completa OAuth in <60s). Cleanup lazy a ogni accesso.

Determinismo §7.9: solo dict + lock + timestamp. Niente persistenza
(se metnos-http riparte durante OAuth, l'utente rifa il flow — costo basso).
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Optional


_TTL_S = 600

_lock = threading.Lock()
_store: dict = {}


def _cleanup_expired_locked() -> None:
    """Rimuove le entry scadute. Caller deve avere il lock."""
    now = time.time()
    expired = [k for k, v in _store.items() if v.get("expires_at", 0) < now]
    for k in expired:
        _store.pop(k, None)


def put(payload: dict, *, ttl_s: int = _TTL_S) -> str:
    """Salva `payload` con uno state UUID. Ritorna lo state da usare come
    OAuth state param + chiave di lookup nel callback.

    `payload` tipicamente contiene:
      - flow_state    (dict da gworkspace_oauth.start_flow)
      - executor      (str: nome executor da ri-invocare)
      - args_base     (dict: args originali da rilanciare)
      - binding       (str: skill name, es. 'google-workspace')
      - sender_id     (str: per push notification opzionale)
      - channel       (str: 'http'|'telegram'|... per routing del risultato)
      - dialog_id     (str: per audit)
    """
    state = secrets.token_urlsafe(24)
    entry = dict(payload)
    entry["expires_at"] = time.time() + ttl_s
    with _lock:
        _cleanup_expired_locked()
        _store[state] = entry
    return state


def pop(state: str) -> Optional[dict]:
    """Recupera ed elimina la entry con quel state. Ritorna None se
    sconosciuto/scaduto. Pop atomico = ogni callback si esegue al massimo
    una volta (anti replay)."""
    with _lock:
        _cleanup_expired_locked()
        return _store.pop(state, None)


def peek(state: str) -> Optional[dict]:
    """Lookup senza pop (per diagnostica)."""
    with _lock:
        _cleanup_expired_locked()
        return _store.get(state)


def size() -> int:
    """Numero di flow pendenti (lazy-cleaned)."""
    with _lock:
        _cleanup_expired_locked()
        return len(_store)
