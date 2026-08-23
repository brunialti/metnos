"""gen_i18n — genera `messages_i18n.json` (repertorio ERR_/WARN_/MSG_ del
catalogo i18n distribuito) che viaggia nel bundle shim così il device
rende i messaggi user-facing (§7.13) invece del codice grezzo. Sul device NON
c'è il DB i18n (invariante §11) → il repertorio si porta con lo shim.

Sync: `test_device_shim_i18n.py` rigenera e confronta col file committato →
un cambio al DB i18n non allineato rompe il baseline (come lo shim
content-addressing). Rigenerare: `python3 runtime/device_shim/gen_i18n.py`.

Prefissi inclusi: ERR_/WARN_/MSG_ (user-facing). Esclusi LOG_ (diagnostica
interna, §7.13). Le lingue sono enumerate dal seed pubblico rilasciato."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_JSON = _HERE / "messages_i18n.json"
_SEED_DB = _HERE.parents[1] / "install" / "data" / "i18n_seed.sqlite"
_PREFIXES = ("ERR_", "WARN_", "MSG_")


def build_templates(
    seed_db: Path | str = _SEED_DB,
    languages: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Return the deterministic device catalog from the released seed.

    The mutable per-user database is intentionally excluded: besides making a
    committed bundle depend on the workstation that generated it, reading it
    here could publish locally synthesized messages or other private wording.
    """
    seed = Path(seed_db).resolve()
    if not seed.is_file():
        raise FileNotFoundError(f"i18n seed not found: {seed}")
    connection = sqlite3.connect(f"file:{seed}?mode=ro", uri=True)
    try:
        like = " OR ".join("key LIKE ?" for _ in _PREFIXES)
        params: list[str] = [f"{p}%" for p in _PREFIXES]
        language_clause = ""
        if languages is not None:
            selected = sorted({
                str(lang).strip().lower()
                for lang in languages if str(lang).strip()
            })
            if not selected:
                return {}
            language_clause = " AND lang IN (%s)" % ",".join(
                "?" for _ in selected
            )
            params.extend(selected)
        rows = connection.execute(
            f"SELECT key, lang, text FROM i18n WHERE ({like}) "
            f"AND text IS NOT NULL{language_clause} ORDER BY key, lang",
            tuple(params),
        ).fetchall()
    finally:
        connection.close()
    out: dict = {}
    for key, lang, text in rows:
        out.setdefault(lang, {})[key] = text
    return out


def write(
    seed_db: Path | str = _SEED_DB,
    output_path: Path | str = _JSON,
) -> int:
    data = build_templates(seed_db)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=0) + "\n",
        encoding="utf-8",
    )
    return sum(len(v) for v in data.values())


if __name__ == "__main__":
    n = write()
    print(f"messages_i18n.json: {n} template scritti in {_JSON}")
