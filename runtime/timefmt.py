# SPDX-License-Identifier: AGPL-3.0-only
"""timefmt.py — helper timestamp UTC condivisi (§7.2: una sola definizione).

Consolidamento di ~23 copie locali (`_now_iso`/`_utc_now_iso`/`_utc_iso`/
`_today_iso_date`). DUE forme di output, NON intercambiabili — a valle ci sono
schema/parser che confrontano le stringhe, quindi il suffisso e' load-bearing:

  now_iso_z()      -> "YYYY-MM-DDTHH:MM:SSZ"        (suffisso Z, strftime)
  now_iso_offset() -> "YYYY-MM-DDTHH:MM:SS+00:00"   (isoformat, offset esplicito)
  today_iso()      -> "YYYY-MM-DD"

Tutte UTC, secondo-precisione (no microsecondi). Determinismo §7.9: nessuno
stato, output funzione solo dell'istante. Leaf module: importa solo datetime.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_iso_z() -> str:
    """ISO-8601 UTC con suffisso `Z` (== le vecchie `_now_iso`)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso_offset(epoch: float | None = None) -> str:
    """ISO-8601 UTC con offset esplicito `+00:00` (== `_utc_now_iso`/`_utc_iso`).

    Con `epoch` (secondi Unix) formatta quell'istante invece di adesso.
    """
    if epoch is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def today_iso() -> str:
    """Data UTC corrente `YYYY-MM-DD` (== `_today_iso_date`)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
