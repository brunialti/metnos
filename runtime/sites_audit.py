# SPDX-License-Identifier: AGPL-3.0-only
"""sites_audit — audit-log append-only per il dominio `sites` (spec §9 [ALTO]).

Il turn-record non basta per l'incident-response di un agente-con-credenziali:
serve una traccia dedicata, append-only, con permessi stretti. Qui registriamo:
sessione aperta (owner, dominio, allowlist), ogni tentativo di login (ESITO, MAI
la credenziale), ogni uso di credenziale (FINGERPRINT sha256[:16], MAI il
valore), ogni azione `act` (F2), ogni modifica di allowlist.

Invarianti di sicurezza (spec §10.6, §4.1):
    - MAI un valore di credenziale nel log: si accetta solo il `fingerprint`.
    - Ogni `url` passa da `sites_url_scrub.scrub_url` prima di essere scritto.
    - File 0600, dir 0700. Append-only (mai riscritto/troncato dal runtime).

Deterministico §7.9. Fail-safe §2.8: un errore di scrittura audit NON deve far
fallire l'operazione utente (best-effort), ma viene loggato su stderr.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from sites_url_scrub import scrub_url

try:
    import config as _C  # §7.11
    _STATE_DIR = _C.PATH_USER_STATE
except Exception:  # pragma: no cover — fallback se config non importabile
    _STATE_DIR = Path.home() / ".local" / "state" / "metnos"

AUDIT_PATH = _STATE_DIR / "sites_audit.jsonl"

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
        entry.update(_sanitize(fields))
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(AUDIT_PATH.parent, 0o700)
        line = json.dumps(entry, ensure_ascii=False)
        # Append atomico: open in append mode, il chmod 0600 avviene una volta
        # (se il file nasce ora). O_APPEND garantisce scritture non-interlacciate.
        new_file = not AUDIT_PATH.exists()
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if new_file:
            os.chmod(AUDIT_PATH, 0o600)
    except Exception as e:  # noqa: BLE001 — best-effort, non deve mai propagare
        print(f"sites_audit: write failed: {e!r}", file=sys.stderr)
