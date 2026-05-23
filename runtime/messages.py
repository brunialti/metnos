"""Repertorio centralizzato di messaggi (Metnos) — facade verso DB i18n.

Single source of truth: `~/.local/share/metnos/i18n.sqlite` (vedi `i18n.py`).
Quattro famiglie di chiavi:
  ERR_*   errori utente
  WARN_*  warning soft
  MSG_*   messaggi informativi/status
  LOG_*   template per audit/debug

Uso tipico:
    from messages import get
    return {"ok": False, "error_code": "ERR_EXT_SVC_LIMIT",
            "error": get("ERR_EXT_SVC_LIMIT")}

Risoluzione lingua + fallback chain (`current_lang → en → it → <missing:CODE>`)
delegata a `i18n.get`. Per la lista delle chiavi e per l'editing testi: tool
admin `python3 -m admin.i18n_cli`.
"""

import os as _os
import sys as _sys
from pathlib import Path as _Path

from logging_setup import get_logger
log = get_logger(__name__)

_RUNTIME = _os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in _Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in _sys.path:
    _sys.path.insert(0, _RUNTIME)
import i18n as _i18n


def get(code: str, **kwargs) -> str:
    """Lookup template con substitution kwargs.

    Wrapper su `i18n.get`. Il dict MESSAGES legacy e' stato rimosso il
    5/5/2026 a consolidamento (39/39 chiavi nel DB IT+EN, verified).
    """
    return _i18n.get(code, **kwargs)
