# SPDX-License-Identifier: AGPL-3.0-only
"""audit_jsonl.py — scrittura append-only JSONL per i log di audit (§7.2: una
sola definizione del primitivo, era duplicato ~6 volte in synt/promoter/i18n/
change-intent/verifier/review).

§2.8 (no silent failure): `fsync=True` di DEFAULT — un record di audit scritto
DEVE sopravvivere a un crash, altrimenti l'audit mentirebbe ("ho loggato" ma la
riga è persa). Chi ha un motivo di performance per non-durabilità passa
`fsync=False` esplicitamente.

`json.dumps` canonico: `ensure_ascii=False` (UTF-8 leggibile), `sort_keys=True`
(deterministico §7.9), `default=str` (un valore non serializzabile diventa
stringa invece di sollevare e far perdere la riga — sempre §2.8).

`'a'` + fsync su POSIX è atomico per linee < PIPE_BUF (~4KB): le righe di audit
sono brevi, quindi safe-by-construction (niente interleaving fra processi).
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def append_jsonl(path, records, *, fsync: bool = True) -> Path:
    """Appende uno o più record (dict o lista di dict) come righe JSONL a `path`.

    Crea la dir se manca. Ritorna il Path scritto. NON cattura le OSError: il
    chiamante decide se l'audit è best-effort (try/except attorno) o meno.
    """
    if isinstance(records, dict):
        records = (records,)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True,
                               default=str) + "\n")
        if fsync:
            f.flush()
            os.fsync(f.fileno())
    return p
