"""gen_i18n — genera `messages_i18n.json` (repertorio ERR_/WARN_/MSG_ del DB
i18n, en+it) che viaggia nel bundle shim così il device rende i messaggi
user-facing (§7.13) invece del codice grezzo. Sul device NON c'è il DB i18n
(invariante §11) → il repertorio si porta con lo shim, rigenerato dal DB (SoT).

Sync: `test_device_shim_i18n.py` rigenera e confronta col file committato →
un cambio al DB i18n non allineato rompe il baseline (come lo shim
content-addressing). Rigenerare: `python3 runtime/device_shim/gen_i18n.py`.

Prefissi inclusi: ERR_/WARN_/MSG_ (user-facing). Esclusi LOG_ (diagnostica
interna, §7.13). Lingue: en+it (scope corrente; il debito 3° locale è a monte,
nel DB i18n — vedi project-i18n-lexicon-debt)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_JSON = _HERE / "messages_i18n.json"
_PREFIXES = ("ERR_", "WARN_", "MSG_")
_LANGS = ("en", "it")


def build_templates() -> dict:
    """{lang: {code: template}} per i codici user-facing, dal DB i18n. Puro
    read-only; ordinato per diff stabile."""
    sys.path.insert(0, str(_HERE.parent))  # runtime/ sul path
    import i18n
    conn = i18n._open()
    like = " OR ".join("key LIKE ?" for _ in _PREFIXES)
    rows = conn.execute(
        f"SELECT key, lang, text FROM i18n WHERE ({like}) "
        "AND text IS NOT NULL AND lang IN (?, ?) ORDER BY key, lang",
        (*[f"{p}%" for p in _PREFIXES], *_LANGS),
    ).fetchall()
    out: dict = {}
    for key, lang, text in rows:
        out.setdefault(lang, {})[key] = text
    return out


def write() -> int:
    data = build_templates()
    _JSON.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=0) + "\n",
        encoding="utf-8",
    )
    return sum(len(v) for v in data.values())


if __name__ == "__main__":
    n = write()
    print(f"messages_i18n.json: {n} template scritti in {_JSON}")
