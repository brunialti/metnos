#!/usr/bin/env python3
"""i18n_migrate_v2 — migration v2 schema DB i18n.sqlite (6/5/2026).

Estensione ADR 0092 al Layer 3: aggiunge `version_hash` + `source_text_hash`
al table `i18n`, in coerenza col pattern latest-wins simmetrico gia' attivo
sui Layer 1 (prompt files) e Layer 2 (manifest descriptions).

Lo schema viene gestito anche al boot da `runtime/i18n.py::_open()` (idempotente,
la migrazione si auto-applica al primo accesso). Questo CLI esiste per
operatori che vogliono migrare/inspect manualmente senza eseguire una run
del runtime.

Idempotente: ri-eseguibile, non distrugge dati.

Usage:
    python3 -m admin.i18n_migrate_v2                  # apply migration
    python3 -m admin.i18n_migrate_v2 --check          # report-only
    python3 -m admin.i18n_migrate_v2 --db <path>      # custom DB
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11
DEFAULT_DB = _C.DB_I18N


def _sha256_full(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def migrate(db_path: Path, *, check_only: bool = False) -> dict:
    """Migra il DB. Ritorna report dict con stats.

    Steps:
      1. ALTER TABLE add `version_hash` (idempotente)
      2. ALTER TABLE add `source_text_hash` (idempotente)
      3. Backfill version_hash per row con text!=NULL e version_hash=NULL
      4. Verify schema finale
    """
    if not db_path.is_file():
        return {
            "ok": False,
            "error": f"DB not found: {db_path}",
            "db_path": str(db_path),
        }
    conn = sqlite3.connect(str(db_path))
    try:
        c = conn.cursor()
        cols_before = {r[1] for r in c.execute("PRAGMA table_info(i18n)").fetchall()}
        added: list[str] = []
        if not check_only:
            if "version_hash" not in cols_before:
                c.execute("ALTER TABLE i18n ADD COLUMN version_hash TEXT")
                added.append("version_hash")
            if "source_text_hash" not in cols_before:
                c.execute("ALTER TABLE i18n ADD COLUMN source_text_hash TEXT")
                added.append("source_text_hash")
        # Stats backfill
        rows_total = c.execute("SELECT COUNT(*) FROM i18n").fetchone()[0]
        # Recompute cols dopo eventuale ALTER
        cols_after = {r[1] for r in c.execute("PRAGMA table_info(i18n)").fetchall()}
        backfilled = 0
        if "version_hash" in cols_after and not check_only:
            for row in c.execute(
                "SELECT key, lang, text FROM i18n "
                "WHERE version_hash IS NULL AND text IS NOT NULL"
            ).fetchall():
                c.execute(
                    "UPDATE i18n SET version_hash=? WHERE key=? AND lang=?",
                    (_sha256_full(row[2]), row[0], row[1]),
                )
                backfilled += 1
        rows_with_vh = 0
        if "version_hash" in cols_after:
            rows_with_vh = c.execute(
                "SELECT COUNT(*) FROM i18n WHERE version_hash IS NOT NULL"
            ).fetchone()[0]
        if not check_only:
            conn.commit()
        return {
            "ok": True,
            "db_path": str(db_path),
            "check_only": check_only,
            "schema_before": sorted(cols_before),
            "schema_after": sorted(cols_after),
            "added_columns": added,
            "rows_total": rows_total,
            "backfilled": backfilled,
            "rows_with_version_hash": rows_with_vh,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB),
                    help=f"Path al DB i18n (default: {DEFAULT_DB})")
    p.add_argument("--check", action="store_true",
                    help="Report-only: NON modifica il DB, solo verifica")
    args = p.parse_args(argv)

    report = migrate(Path(args.db), check_only=args.check)
    if not report.get("ok"):
        print(f"FAIL: {report.get('error')}", file=sys.stderr)
        return 1
    print(f"db: {report['db_path']}")
    print(f"check_only: {report['check_only']}")
    print(f"schema_before: {report['schema_before']}")
    print(f"schema_after: {report['schema_after']}")
    print(f"added_columns: {report['added_columns']}")
    print(f"rows_total: {report['rows_total']}")
    print(f"backfilled: {report['backfilled']}")
    print(f"rows_with_version_hash: {report['rows_with_version_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
