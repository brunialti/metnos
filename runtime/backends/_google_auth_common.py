"""runtime/backends/_google_auth_common.py — prologo OAuth condiviso google-workspace.

Estratto da `backends/files/google_workspace.py` (spec Google Photos §3.3): il
trio OAuth — credenziali presenti, token fresco (refresh automatico), payload
`needs_inputs` di setup — e' identico per ogni backend che parla con le API
Google via la skill `google-workspace` (Drive/Sheets/Docs e ora Photos). Un
solo posto (§7.2 regola-del-3, §7.9 deterministico), usato da ENTRAMBI i moduli
`files/google_workspace` e `images/google_photos`.

NB: contacts/events/messages hanno ancora una loro copia locale di
`_auth_needs_inputs` (duplicazione PRE-esistente, fuori dallo scope di questa
spec): candidati alla stessa unificazione in un secondo momento.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from skill_wrapper import (  # noqa: E402
    _skill_home, _needs_inputs_oauth_setup,
    _get_oauth_provider_for_skill,
)
from messages import get as _msg  # noqa: E402

SKILL_NAME = "google-workspace"


def has_creds() -> bool:
    return (_skill_home(SKILL_NAME) / "google_token.json").is_file()


def ensure_fresh_token() -> bool:
    """Fallback di refresh OAuth (resilienza): l'access token google scade ~1h,
    quindi senza refresh ogni op fallirebbe dopo un'ora. Se il token e' scaduto
    ma ha `refresh_token`, lo rinnova e lo RISALVA (cosi' il subprocess skill
    riceve un token fresco). Ritorna:
      - True  → token utilizzabile (valido o rinnovato con successo);
      - False → assente / scaduto-senza-refresh / refresh fallito (rete o
        refresh_token revocato) → il chiamante ritorna needs_inputs (no traceback).
    Determinismo §7.9. Robusto: qualunque errore → False (mai eccezione propagata)."""
    tok = _skill_home(SKILL_NAME) / "google_token.json"
    if not tok.is_file():
        return False
    try:
        import json as _json
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        info = _json.loads(tok.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(info, info.get("scopes"))
        if creds.valid:
            return True
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            out = _json.loads(creds.to_json())
            # preserva i campi extra non gestiti da to_json()
            for k in ("account", "type", "universe_domain"):
                if k in info and k not in out:
                    out[k] = info[k]
            tmp = tok.parent / (tok.name + ".tmp")
            tmp.write_text(_json.dumps(out, indent=2), encoding="utf-8")
            os.replace(tmp, tok)  # atomico
            log.info("google OAuth token rinnovato (scadenza %s)", out.get("expiry"))
            return True
        return False  # scaduto senza refresh_token
    except Exception as ex:  # rete assente, refresh_token revocato, lib mancante
        log.warning("refresh token google fallito: %s", ex)
        return False


def auth_needs_inputs(args_base: dict, *, executor: str,
                      result_kind: str = "entries") -> dict:
    """Payload `needs_inputs` per il setup OAuth (re-consent guidato). `result_kind`
    determina la shape del return (entries vs results) coerente col verb canonical."""
    try:
        payload = _needs_inputs_oauth_setup(
            skill_name=SKILL_NAME, executor=executor,
            args_base=args_base,
            **_get_oauth_provider_for_skill(SKILL_NAME),
        )
    except Exception as ex:
        out = {"ok": False, "error_class": "auth_required",
               "error_code": "ERR_OAUTH_SETUP",
               "error": _msg("ERR_OAUTH_SETUP", reason=str(ex))}
        if result_kind == "entries":
            out["entries"] = []; out["used"] = 0
        else:
            out["results"] = []; out["used"] = 0
        return out
    out = {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": payload,
        "error_class": "auth_required",
        "final_message_hint": payload.get("title", ""),
    }
    if result_kind == "entries":
        out["entries"] = []; out["used"] = 0
    else:
        out["results"] = []; out["used"] = 0
    return out
