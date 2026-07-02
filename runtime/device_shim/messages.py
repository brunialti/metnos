"""device_shim.messages — fallback di `runtime.messages` per il device remoto.

Sul device NON esiste il DB i18n (§11 CLAUDE.md, invariante «segreti e stato
solo-server»). Gli executor importano `from messages import get`: questo shim
soddisfa il contratto con un repertorio minimo embedded (le chiavi che il
boilerplate `run_stdio` usa) + passthrough onesto `<code>` per il resto.
La lingua la sceglie il server: METNOS_LANG viene iniettato nell'env
dell'invocazione; default 'en'.

Servito dal server via GET /agent/shim (bundle firmato). NON e' il modulo
di produzione: vive in runtime/device_shim/ e viaggia col client.
"""
from __future__ import annotations

import os

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
    table = _TEMPLATES.get(lang) or _TEMPLATES["en"]
    template = table.get(code) or _TEMPLATES["en"].get(code)
    if template is None:
        # Passthrough onesto: mai inventare testo, il codice resta leggibile.
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        return f"{code}{(' ' + extra) if extra else ''}"
    try:
        return template.format(**kwargs)
    except Exception:
        return template
