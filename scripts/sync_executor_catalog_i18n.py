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
    "LIST_HELP": (
        "Tutti gli strumenti installati: versione, ciclo di vita, appartenenza al prodotto, origine, trasporto e se ciò che fanno si può annullare.",
        "Every installed tool: version, lifecycle, product membership, origin, transport and whether what it does can be undone.",
    ),
    "COL_NAME": ("nome", "name"),
    "COL_VERSION": ("versione", "version"),
    "COL_LIFECYCLE": ("ciclo di vita", "lifecycle"),
    "COL_MEMBERSHIP": ("appartenenza", "membership"),
    "COL_ORIGIN": ("origine", "origin"),
    "COL_TRANSPORT": ("trasporto", "transport"),
    "COL_STANDARD": ("standard", "standard"),
    "COL_CAPABILITIES": ("permessi", "capabilities"),
    "COL_REVERTIBLE": ("annullabile", "undoable"),
    "COL_PATH": ("percorso", "path"),
    "COL_REASON": ("motivo", "reason"),
    "YES": ("sì", "yes"),
    "NO": ("no", "no"),
    "REJECTED_TITLE": ("Rifiutati al caricamento", "Refused at load"),
    "REJECTED_HELP": (
        "Questi strumenti non sono entrati nel catalogo. Il motivo è mostrato perché nessuno sparisca in silenzio.",
        "These tools did not enter the catalog. The reason is shown so that none disappears silently.",
    ),
    "RETIRED_TITLE": ("Ritirati di proposito", "Retired on purpose"),
    "RETIRED_HELP": (
        "Strumenti ritirati con una decisione: non sono errori. Resta soltanto la loro sorgente, come storia; nessuno la usa.",
        "Tools retired by a decision: they are not errors. Only their source remains, as history; nothing uses it.",
    ),
    "TOTAL": ("Totale", "Total"),
    "ORIGIN_HANDCRAFTED": ("Manuali", "Handcrafted"),
    "ORIGIN_BUILTIN": ("Integrati", "Built-in"),
    "ORIGIN_SYNTHESIZED": ("Generati", "Synthesized"),
    "ORIGIN_IMPORTED": ("Importati", "Imported"),
    "DEPRECATED": ("Deprecati", "Deprecated"),
    "VIEW_ALL": ("Vedi tutti", "View all"),
    "REVIVE": ("Riattiva", "Reactivate"),
    "REVIVE_CONFIRM": (
        "Riattivare «{name}»? Tornerà fra gli strumenti che Metnos può scegliere.",
        "Reactivate “{name}”? It will be among the tools Metnos can choose again.",
    ),
    "REVIVE_NOT_RESTRICTED": (
        "«{name}» non è fermo: non c’è niente da riattivare.",
        "“{name}” is not stopped: there is nothing to reactivate.",
    ),
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
    # The whole description of the two pages the Tutor explains from here.
    updated_surfaces = ("UI_SURFACE_EXECUTORS_", "UI_SURFACE_CHANGES_")
    for lang in ("it", "en"):
        for key, text in ui_surfaces.localization_inventory(lang):
            if key in updated_registry_keys or key.startswith(updated_surfaces):
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
