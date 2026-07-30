"""utf8_safe — sanitizzazione UTF-8 al boundary di serializzazione.

Rimuove i code point surrogate UTF-16 (U+D800..U+DFFF) che sono
invalidi in UTF-8 e fanno rifiutare le request da Anthropic API,
OpenAI API, llama-server e altri provider. RFC 8259 6.2.1 lo proibisce.

Sorgenti tipiche di surrogate in Metnos:
- Filename con encoding storico rotto (iconv su FAT/NTFS, mac roman).
- Output LLM con token sbagliato (raro ma documentato).
- Web crawl: title HTML decodificato male.
- File descrittori ricevuti via JSON da subsystem esterni.

Convenzione: sanitize SEMPRE prima di `json.dumps()` verso un endpoint
esterno O prima di scrivere su JSONL persistente. Mai modificare i
dati in-memory upstream — solo al confine di trasporto.

Uso:
    from utf8_safe import strip_surrogates, safe_json_dumps

    body = safe_json_dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, ...)

    # oppure su una singola stringa:
    clean = strip_surrogates(some_external_text)
"""
from __future__ import annotations

import json
from typing import Any


_SURROGATE_LO = 0xD800
_SURROGATE_HI = 0xDFFF


def strip_surrogates(s: str) -> str:
    """Rimuove i code point surrogate da `s`. Idempotente.

    Performance: ~5MB/s su CPython 3.12, accettabile per payload tipici
    (<1MB). Per stringhe grandi (>10MB) considerare la versione bytes.
    """
    if not isinstance(s, str):
        return s
    if not any(_SURROGATE_LO <= ord(c) <= _SURROGATE_HI for c in s):
        return s  # fast path: nessun surrogate
    return "".join(c for c in s
                   if not (_SURROGATE_LO <= ord(c) <= _SURROGATE_HI))


def clean_obj(obj: Any) -> Any:
    """Sanitize ricorsiva di un oggetto serializzabile JSON.

    Visita stringhe in dict/list/tuple, applica `strip_surrogates`.
    NON copia profondamente (mutazioni in-place per dict/list); per i
    tuple (immutabili) crea nuovo tuple. Tipi primitivi (int/float/
    bool/None) ritornati invariati.
    """
    if isinstance(obj, str):
        return strip_surrogates(obj)
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            obj[k] = clean_obj(obj[k])
        return obj
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            obj[i] = clean_obj(v)
        return obj
    if isinstance(obj, tuple):
        return tuple(clean_obj(v) for v in obj)
    return obj


def safe_json_dumps(obj: Any, **kwargs) -> str:
    """`json.dumps()` con sanitizzazione surrogati.

    Equivalente a `json.dumps(clean_obj(obj), ensure_ascii=False, **kwargs)`.
    Default ensure_ascii=False per preservare caratteri unicode validi
    (es. accenti italiani) senza escape inutile.
    """
    kwargs.setdefault("ensure_ascii", False)
    return json.dumps(clean_obj(obj), **kwargs)
