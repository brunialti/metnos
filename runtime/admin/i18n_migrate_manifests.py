#!/usr/bin/env python3
"""i18n_migrate_manifests — migra description/affinity da manifest TOML al DB i18n.

Fase 2 i18n (1/5/2026 sera).

Per ogni manifest.toml in <install_root>/executors/* + ~/.local/share/metnos/executors/*:
- Se `description` in manifest e' stringa flat → INSERT in DB con (key=<name>.description, lang=it, text=<value>)
- Se `description` e' dict {it,en,...} → INSERT N rows (una per lingua)
- Idempotente: usa INSERT OR REPLACE

Convention chiavi:
- <executor_name>.description
- <executor_name>.affinity (lista serializzata JSON)

Uso:
    python3 -m admin.i18n_migrate_manifests [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import i18n

# ADR 0148 rename-resilient
import config as _C  # noqa: E402
EXEC_DIRS = [
    _C.PATH_EXECUTORS,
    _C.PATH_USER_DATA / "executors",
]
DEFAULT_LANG = "it"


def _norm_value(val):
    """Normalizza description/affinity: ritorna dict {lang: text} sempre.
    Stringa flat → {DEFAULT_LANG: val}. Lista (per affinity) → JSON-serialize.
    """
    if val is None:
        return None
    if isinstance(val, str):
        return {DEFAULT_LANG: val}
    if isinstance(val, list):
        return {DEFAULT_LANG: json.dumps(val, ensure_ascii=False)}
    if isinstance(val, dict):
        # Già nested {it, en, ...} oppure dict con sub-list (affinity bilingue)
        out = {}
        for k, v in val.items():
            if isinstance(v, str):
                out[k] = v
            elif isinstance(v, list):
                out[k] = json.dumps(v, ensure_ascii=False)
        return out or None
    return None


def migrate_manifest(manifest_path: Path, dry_run: bool = False) -> dict:
    """Legge un manifest e migra description+affinity. Ritorna dict counts."""
    try:
        manifest = tomllib.loads(manifest_path.read_text())
    except Exception as e:
        return {"error": f"toml parse: {e}", "rows": 0}
    name = manifest.get("name")
    if not name:
        return {"error": "no name", "rows": 0}
    counts = {"name": name, "rows": 0, "fields": []}
    for field in ("description", "affinity"):
        raw = manifest.get(field)
        norm = _norm_value(raw)
        if not norm:
            continue
        key = f"{name}.{field}"
        for lang, text in norm.items():
            if dry_run:
                print(f"  [dry-run] would set [{key}, {lang}] = {text[:60]!r}")
            else:
                i18n.set(key, lang, text)
            counts["rows"] += 1
        counts["fields"].append(field)
    return counts


def main():
    p = argparse.ArgumentParser(prog="metnos-i18n-migrate-manifests")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    total = 0
    for d in EXEC_DIRS:
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            mf = sub / "manifest.toml"
            if not mf.is_file():
                continue
            res = migrate_manifest(mf, dry_run=args.dry_run)
            if "error" in res:
                print(f"  SKIP {sub.name}: {res['error']}")
            else:
                print(f"  {res['name']}: +{res['rows']} rows ({', '.join(res['fields'])})")
                total += res["rows"]
    print(f"\nTotal: {total} rows {'[dry-run]' if args.dry_run else 'migrated'}")


if __name__ == "__main__":
    main()
