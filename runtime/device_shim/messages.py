"""device_shim.messages — fallback di `runtime.messages` per il device remoto.

Sul device NON esiste il DB i18n (§11 the design guide, invariante «segreti e stato
solo-server»). Gli executor importano `from messages import get`: questo shim
soddisfa il contratto rendendo i messaggi user-facing (§7.13) invece del codice
grezzo. Il repertorio ERR_/WARN_/MSG_ in `messages_i18n.json` viene generato
esclusivamente dal corpus pubblico ammesso e bundleato con lo shim
(`gen_i18n.py` + guardia-drift di test). Se il bundle o una chiave non
esistono, resta il passthrough onesto del codice: nessun testo operativo e'
incorporato qui. METNOS_LANG sceglie la lingua e METNOS_SOURCE_LANG dichiara
il fallback bootstrap del bundle.

Servito dal server via GET /agent/shim (bundle firmato). NON e' il modulo
di produzione: vive in runtime/device_shim/ e viaggia col client.
"""
from __future__ import annotations

import json as _json
import os
from pathlib import Path as _Path

# Repertorio i18n bundleato, caricato UNA volta. Mai solleva.
_I18N: dict = {}
try:
    _p = _Path(__file__).resolve().parent / "messages_i18n.json"
    if _p.is_file():
        _I18N = _json.loads(_p.read_text(encoding="utf-8"))
except Exception:
    _I18N = {}

def get(code: str, /, **kwargs) -> str:
    source_lang = os.environ.get("METNOS_SOURCE_LANG", "en")
    lang = os.environ.get("METNOS_LANG", source_lang)
    # Repertorio ammesso: lingua richiesta → fallback bootstrap dichiarato.
    template = ((_I18N.get(lang) or {}).get(code)
                or (_I18N.get(source_lang) or {}).get(code))
    if template is None:
        # Passthrough onesto: mai inventare testo, il codice resta leggibile.
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        return f"{code}{(' ' + extra) if extra else ''}"
    try:
        return template.format(**kwargs)
    except Exception:
        return template
