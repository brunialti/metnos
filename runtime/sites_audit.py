# SPDX-License-Identifier: MIT
"""sites_audit — audit-log append-only per il dominio `sites` (spec §9 [ALTO]).

Il turn-record non basta per l'incident-response di un agente-con-credenziali:
serve una traccia dedicata, append-only, con permessi stretti. Qui registriamo:
sessione aperta (owner, dominio, allowlist), ogni tentativo di login (ESITO, MAI
la credenziale), ogni uso di credenziale (FINGERPRINT sha256[:16], MAI il
valore), ogni azione `act` (F2), ogni modifica di allowlist.

Invarianti di sicurezza (spec §10.6, §4.1):
    - MAI un valore di credenziale nel log: si accetta solo il `fingerprint`.
    - Ogni `url` passa da `sites_url_scrub.scrub_url` prima di essere scritto.
    - File 0600, dir 0700. Generazioni append-only: nessuna riscrittura o
      troncamento; segmenti da 16 MB (override via env), conservati fino alla raccolta F6.

Deterministico §7.9. Fail-safe §2.8: un errore di scrittura audit NON deve far
fallire l'operazione utente (best-effort), ma viene loggato su stderr.
"""
from __future__ import annotations

import os
import sys
import time
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from sites_url_scrub import scrub_url

try:
    import config as _C  # §7.11
    _STATE_DIR = _C.PATH_USER_STATE
except Exception:  # pragma: no cover — fallback se config non importabile
    _STATE_DIR = Path.home() / ".local" / "state" / "metnos"

AUDIT_PATH = _STATE_DIR / "sites_audit.jsonl"

# This describes the command path, never the physical person at a browser.
# ContextVars isolate concurrent sessions and propagate to asyncio.to_thread.
_ACTION_ORIGIN = ContextVar("sites_action_origin", default=None)
_ACTOR_FIELDS = frozenset({
    "executor", "source", "role", "provider", "model", "principal_id",
    "device_id", "authorization", "dialog_id", "request_id",
})


def actor_metadata(value) -> dict:
    """Closed, bounded attribution. No arbitrary client/model prose or secrets."""
    if not isinstance(value, dict):
        return {"actor_type": "unknown"}
    kind = value.get("actor_type")
    if not isinstance(kind, str) or kind not in {"executor", "model", "browser_control", "unknown"}:
        return {"actor_type": "unknown"}
    result = {"actor_type": kind}
    for key in _ACTOR_FIELDS:
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.:/@+-]{1,160}", item):
            result[key] = item
    if kind == "browser_control":
        result["human_identity"] = "unverified"
    return result


@contextmanager
def action_context(origin: dict):
    token = _ACTION_ORIGIN.set(actor_metadata(origin))
    try:
        yield
    finally:
        _ACTION_ORIGIN.reset(token)


def model_actor(role: str, response=None) -> dict:
    """Use the actual response's transport identity; do not guess from tier."""
    return actor_metadata({
        "actor_type": "model", "role": role,
        "provider": getattr(response, "provider", "") or "unknown",
        "model": getattr(response, "model", "") or "unknown",
    })


def browser_control_origin(request, *, dialog_id: str, owner_user_id: str) -> dict:
    """Called after authorization by HTTP, before signing the exact command.

    An authenticated account and a dialog capability identify authorization,
    not whether a human or an API client initiated a click. Body fields and
    User-Agent/Sec-Fetch headers cannot establish that fact.
    """
    principal = str(request.get("authenticated_user_id") or "")
    return actor_metadata({
        "actor_type": "browser_control", "executor": "playwright_sidecar",
        "source": "http.dialog_browser", "dialog_id": dialog_id,
        "principal_id": principal, "device_id": request.get("device_id") or "",
        "authorization": "owner_identity" if principal and principal == owner_user_id
                         else "dialog_capability",
    })

# Nomi di campo il cui valore non deve MAI finire nell'audit (difesa in
# profondità: se un chiamante passa per sbaglio una credenziale, la droppiamo).
_FORBIDDEN_FIELDS = frozenset({
    "password", "passwd", "secret", "token", "value", "form_data",
    "credential", "credentials", "otp", "api_key",
})


def _sanitize_value(value):
    if isinstance(value, dict):
        return _sanitize(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v) for v in value]
    return value


def _sanitize(payload: dict) -> dict:
    """Rimuove campi proibiti e scruba gli URL. Difesa in profondità: l'audit
    non è mai il punto in cui un segreto sfugge."""
    out = {}
    for k, v in payload.items():
        kl = str(k).lower()
        if kl in _FORBIDDEN_FIELDS:
            continue  # drop silenzioso: mai loggare il segreto
        if (isinstance(v, str) and (kl in (
                "url", "final_url", "login_url", "action_url", "target")
                or v.lower().startswith(("http://", "https://")))):
            out[k] = scrub_url(v)
        else:
            out[k] = _sanitize_value(v)
    return out


def record(event: str, *, owner: str = "", session_id: str = "",
           domain: str = "", **fields) -> None:
    """Scrive UNA riga JSON nell'audit-log. `event` = tipo evento
    (session_open|login_attempt|credential_use|act|allowlist_change|
    session_close|origin_mismatch|kill_switch). `fields` extra sanitizzati.

    Best-effort: un errore non propaga (l'operazione utente non deve fallire
    per un problema di logging), ma viene segnalato su stderr per diagnostica.
    """
    try:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": str(event),
            "owner": str(owner or ""),
            "session_id": str(session_id or ""),
            "domain": str(domain or ""),
        }
        clean = _sanitize(fields)
        origin = clean.pop("action_origin", _ACTION_ORIGIN.get())
        if "decision_actor" in clean:
            clean["decision_actor"] = actor_metadata(clean["decision_actor"])
        entry.update(clean)
        entry["action_origin"] = actor_metadata(origin)
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(AUDIT_PATH.parent, 0o700)
        from audit_jsonl import append_bounded_jsonl
        max_mb = int(os.environ.get("METNOS_SITES_AUDIT_MAX_MB", "16"))
        append_bounded_jsonl(
            AUDIT_PATH, entry, max_bytes=max_mb * 1024 * 1024,
            backup_count=None, mode=0o600)
    except Exception as e:  # noqa: BLE001 — best-effort, non deve mai propagare
        print(f"sites_audit: write failed: {e!r}", file=sys.stderr)
