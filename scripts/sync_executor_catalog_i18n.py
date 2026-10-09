#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Update only executor inventory labels in the candidate i18n seed."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = {
    "CATALOG_TITLE": ("Catalogo executor", "Executor catalog"),
    "CATALOG_HELP": (
        "Conteggi del catalogo attuale, riscontrabili nell’elenco completo. I deprecati sono un sottoinsieme del totale.",
        "Current catalog counts, matching the full list. Deprecated executors are included in the total.",
    ),
    "TOTAL": ("Totale", "Total"),
    "ORIGIN_HANDCRAFTED": ("Manuali", "Handcrafted"),
    "ORIGIN_BUILTIN": ("Integrati", "Built-in"),
    "ORIGIN_SYNTHESIZED": ("Generati", "Synthesized"),
    "ORIGIN_IMPORTED": ("Importati", "Imported"),
    "DEPRECATED": ("Deprecati", "Deprecated"),
    "VIEW_ALL": ("Vedi tutti", "View all"),
    "STATISTICS": ("Statistiche", "Statistics"),
    "STATS_HELP": (
        "I conteggi usano lo stesso catalogo attuale dell’elenco executor. Il grafico degli eventi conserva la storia degli ultimi 30 giorni.",
        "Counts use the same current catalog as the executor list. The events chart preserves the history of the last 30 days.",
    ),
    "COUNTS_TITLE": ("Catalogo attuale per origine e stato", "Current catalog by origin and state"),
    "EVENTS_TITLE": ("Eventi registrati negli ultimi 30 giorni", "Events recorded in the last 30 days"),
    "EVENTS_HELP": (
        "Una linea per origine e tipo di evento, con l’origine registrata all’epoca. Questi eventi storici non indicano quanti executor sono disponibili oggi. Passa il puntatore sui punti per i dettagli.",
        "One line per origin and event type, using the origin recorded at the time. These historical events do not count the executors available today. Hover over points for details.",
    ),
}


def sync(path: Path) -> int:
    sys.path.insert(0, str(ROOT / "runtime"))
    import i18n
    import ui_surfaces

    i18n.DB_PATH = path
    i18n._SEED_DB_PATH = path
    i18n._conn = None
    conn = i18n._open()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS i18n_seed_history ("
        "key TEXT NOT NULL, lang TEXT NOT NULL, version_hash TEXT NOT NULL, "
        "PRIMARY KEY (key, lang, version_hash))"
    )
    catalog = {f"UI_EXECUTOR_{suffix}": {"it": it, "en": en}
               for suffix, (it, en) in TRANSLATIONS.items()}
    updated_registry_keys = {"UI_SURFACE_SETTINGS_VISIBLE_3"} | {
        f"UI_SURFACE_EXECUTOR_STATS_VISIBLE_{index}" for index in range(3)
    }
    for lang in ("it", "en"):
        for key, text in ui_surfaces.localization_inventory(lang):
            if key in updated_registry_keys:
                catalog.setdefault(key, {})[lang] = text
    for key, translations in sorted(catalog.items()):
        for lang, previous in conn.execute(
            "SELECT lang, text FROM i18n WHERE key=? AND text IS NOT NULL", (key,),
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO i18n_seed_history (key,lang,version_hash) VALUES (?,?,?)",
                (key, lang, i18n._sha256_full(previous)),
            )
        i18n.set_catalog_translations(key, translations, source_lang="it")
    conn.commit()
    i18n._checkpoint(conn)
    conn.close()
    i18n._conn = None
    return len(catalog) * 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    print(f"Executor catalog: {sync(args.db)} translations in {args.db}")


if __name__ == "__main__":
    main()
