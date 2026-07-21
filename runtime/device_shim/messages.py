"""device_shim.messages — fallback di `runtime.messages` per il device remoto.

Sul device NON esiste il DB i18n (§11 the design guide, invariante «segreti e stato
solo-server»). Gli executor importano `from messages import get`: questo shim
soddisfa il contratto rendendo i messaggi user-facing (§7.13) invece del codice
grezzo. Repertorio ERR_/WARN_/MSG_ in `messages_i18n.json` (en+it), generato
dal DB i18n (SoT) e bundleato con lo shim (`gen_i18n.py` + guardia-drift di
test). Fallback a 4 template embedded (shim vecchio senza JSON) + passthrough
onesto `<code>` per codici ignoti. La lingua la sceglie il server: METNOS_LANG
iniettato nell'env dell'invocazione; default 'en'.

Servito dal server via GET /agent/shim (bundle firmato). NON e' il modulo
di produzione: vive in runtime/device_shim/ e viaggia col client.
"""
from __future__ import annotations

import json as _json
import os
from pathlib import Path as _Path

# Repertorio i18n bundleato (en+it), caricato UNA volta. Assente (shim vecchio)
# → resta il fallback embedded + passthrough. Mai solleva.
_I18N: dict = {}
try:
    _p = _Path(__file__).resolve().parent / "messages_i18n.json"
    if _p.is_file():
        _I18N = _json.loads(_p.read_text(encoding="utf-8"))
except Exception:
    _I18N = {}

_TEMPLATES = {
    "en": {
        "ERR_EMPTY_INPUT": "empty input: expected one JSON object on stdin",
        "ERR_JSON_INVALID": "invalid JSON on stdin",
        "ERR_ARG_NOT_STRING": "argument '{arg}' must be a string",
        "ERR_PACKAGE_NOT_FOUND": "package or command '{name}' not found",
    },
    "it": {
        "ERR_EMPTY_INPUT": "input vuoto: atteso un oggetto JSON su stdin",
        "ERR_JSON_INVALID": "JSON non valido su stdin",
        "ERR_ARG_NOT_STRING": "l'argomento '{arg}' deve essere una stringa",
        "ERR_PACKAGE_NOT_FOUND": "pacchetto o comando '{name}' non trovato",
    },
}


def get(code: str, **kwargs) -> str:
    lang = os.environ.get("METNOS_LANG", "en")
    # 1. repertorio i18n bundleato (en+it): lingua richiesta → fallback en.
    template = ((_I18N.get(lang) or {}).get(code)
                or (_I18N.get("en") or {}).get(code))
    # 2. fallback embedded (shim vecchio senza JSON).
    if template is None:
        table = _TEMPLATES.get(lang) or _TEMPLATES["en"]
        template = table.get(code) or _TEMPLATES["en"].get(code)
    if template is None:
        # 3. passthrough onesto: mai inventare testo, il codice resta leggibile.
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        return f"{code}{(' ' + extra) if extra else ''}"
    try:
        return template.format(**kwargs)
    except Exception:
        return template
