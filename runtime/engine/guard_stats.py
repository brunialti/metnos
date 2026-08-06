"""guard_stats — quante volte ogni guardia deterministica spara, sul serio.

Perche' esiste (6/8/2026). La GUARD_PIPELINE cresce di una guardia per ogni
caso che sbaglia e non ne ha mai persa una: `dispatch.py` e' passato da 283 a
oltre 7000 righe in due mesi, e il 54% sono le guardie. Il contatore c'era gia'
(`METNOS_GUARD_FIRE_COUNT`) ma viveva in un dizionario di processo ed era spento
in produzione: nessuno sapeva quali guardie sparassero ancora, quindi nessuna si
poteva ritirare, quindi il numero poteva solo salire. Persistere il conteggio e'
la condizione perche' esista un «meno uno».

Che cosa NON dice, e va detto: zero spari ha due letture opposte — (a) il
modello non produce piu' quell'errore, la guardia si ritira; (b) la guardia sta
PREVENENDO l'errore e il piano arriva sano proprio perche' lei c'e'. Nessun dato
qui distingue le due. Il ritiro richiede la quarantena osservata: si spegne la
guardia e si verifica che il tasso d'errore non risalga. Questo modulo fornisce
la misura, non il verdetto.

Costo misurato: 0,03 ms per piano (0,369 -> 0,399 ms sull'intera pipeline),
quindi acceso di default. Scritture bufferizzate: si accumula in memoria e si
riversa ogni `METNOS_GUARD_STATS_FLUSH` piani (default 20) e all'uscita.
"""
from __future__ import annotations

import atexit
import logging
import os
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

import config as _C  # §7.11 — rispetta METNOS_USER_STATE
from timefmt import now_iso_z

log = logging.getLogger("metnos.guard_stats")

DB_PATH = Path(os.environ.get("METNOS_GUARD_STATS_DB",
                              str(_C.PATH_USER_STATE / "guard_stats.db")))
# Il demone e' longevo: con una soglia alta i conteggi restano invisibili per
# ore. Cinque piani sono pochi turni, e il riversamento e' una manciata di
# upsert su una tabella di 34 righe.
FLUSH_EVERY = int(os.environ.get("METNOS_GUARD_STATS_FLUSH", "5"))

_FIRES: Counter = Counter()
_SEEN: Counter = Counter()
_LAST: dict[str, str] = {}
_PLANS = [0]


def _conn():
    """Lo schema si assicura a OGNI connessione, non una volta per processo:
    sotto test la radice di stato e' una cartella temporanea che sparisce fra
    un caso e l'altro, e una cache direbbe «tabella gia' creata» su un file
    appena rinato vuoto."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=5.0)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS guard_fire (
        name         TEXT PRIMARY KEY,
        fires        INTEGER NOT NULL DEFAULT 0,
        seen         INTEGER NOT NULL DEFAULT 0,
        first_seen   TEXT,
        last_fire_at TEXT
    );
    """)
    c.commit()
    return c


def _sotto_test() -> bool:
    """La suite attraversa la pipeline migliaia di volte con piani costruiti a
    mano: sommarli al traffico reale falserebbe l'unica misura che serve a
    decidere se una guardia si puo' ritirare. Misurato: una sola esecuzione
    della suite aggiungeva 5621 attraversamenti."""
    return "PYTEST_CURRENT_TEST" in os.environ


def record(name: str, fired: bool) -> None:
    """Un attraversamento della guardia `name`; `fired` = ha mutato il piano."""
    if _sotto_test():
        return
    _SEEN[name] += 1
    if fired:
        _FIRES[name] += 1
        _LAST[name] = now_iso_z()


def end_plan() -> None:
    """Un piano ha attraversato la pipeline: riversa ogni FLUSH_EVERY piani."""
    _PLANS[0] += 1
    if FLUSH_EVERY > 0 and _PLANS[0] % FLUSH_EVERY == 0:
        flush()


def flush() -> None:
    """Riversa i contatori accumulati e azzera il buffer. Best-effort: una
    statistica non deve mai far fallire un turno."""
    if not _SEEN:
        return
    seen, fires, last = dict(_SEEN), dict(_FIRES), dict(_LAST)
    _SEEN.clear()
    _FIRES.clear()
    _LAST.clear()
    ts = now_iso_z()
    try:
        with closing(_conn()) as c:
            for name, n in seen.items():
                c.execute(
                    "INSERT INTO guard_fire(name, fires, seen, first_seen, "
                    "last_fire_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "fires = fires + excluded.fires, "
                    "seen = seen + excluded.seen, "
                    "last_fire_at = COALESCE(excluded.last_fire_at, last_fire_at)",
                    (name, fires.get(name, 0), n, ts, last.get(name)))
            c.commit()
    except Exception as ex:  # noqa: BLE001
        log.warning("guard_stats: riversamento fallito (%d guardie): %r",
                    len(seen), ex)


def stats() -> list[dict]:
    """Righe (name, fires, seen, first_seen, last_fire_at), piu' sparanti in
    testa. Include il buffer non ancora riversato."""
    try:
        with closing(_conn()) as c:
            rows = [{"name": r[0], "fires": r[1], "seen": r[2],
                     "first_seen": r[3], "last_fire_at": r[4]}
                    for r in c.execute(
                        "SELECT name, fires, seen, first_seen, last_fire_at "
                        "FROM guard_fire")]
    except Exception as ex:  # noqa: BLE001
        log.warning("guard_stats: lettura fallita: %r", ex)
        rows = []
    by_name = {r["name"]: r for r in rows}
    for name, n in _SEEN.items():
        r = by_name.setdefault(name, {"name": name, "fires": 0, "seen": 0,
                                      "first_seen": None, "last_fire_at": None})
        r["seen"] += n
        r["fires"] += _FIRES.get(name, 0)
        r["last_fire_at"] = _LAST.get(name) or r["last_fire_at"]
    return sorted(by_name.values(), key=lambda r: (-r["fires"], r["name"]))


def _flush_at_exit() -> None:
    """All'uscita del processo il logging puo' avere gia' chiuso i suoi flussi:
    un contatore non deve stampare un errore mentre tutto si spegne."""
    try:
        flush()
    except Exception:  # noqa: BLE001
        pass


atexit.register(_flush_at_exit)
