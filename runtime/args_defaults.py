# SPDX-License-Identifier: AGPL-3.0-only
"""args_defaults — memoria deterministica dell'ULTIMO valore inserito per gli
arg di SCOPE (l'«oggetto» di una CRUD: repo, path, calendar, account…).

Pattern (Roberto): una funzione CRUD ha bisogno di un oggetto; se non ce l'ha
deve poterlo (a) ricevere inline nella query, (b) chiederlo via form get_inputs;
in ENTRAMBI i casi l'ultimo valore diventa il default per il giro dopo.

Questo modulo è SOLO lo store + la derivazione del dominio. La risoluzione
(precedenza arg-esplicito → inline → ricordato → config → chiedi) vive in
`args_resolver.py`; il form/resume nel runtime dialog. Determinismo §7.9:
zero LLM, lookup tabellare.

Granularità (decisione Roberto): per OGGETTO/SKILL. La chiave `domain` è il
qualifier (skill/provider, es. `github`) se presente, altrimenti l'object §2.2
(es. `files`). Così `repo` è condiviso da tutti i `*_github`, `path` dai tool
file. Derivato via `naming_grammar.parse_name` (sorgente unica, no parsing
ad-hoc).

Storage: sqlite `$METNOS_USER_DATA/args_defaults.sqlite`.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Optional

import config as _C  # §7.11
from timefmt import now_iso_z

DB_PATH = _C.PATH_USER_DATA / "args_defaults.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS args_defaults (
  actor       TEXT NOT NULL,
  domain      TEXT NOT NULL,
  arg_name    TEXT NOT NULL,
  last_value  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  uses        INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (actor, domain, arg_name)
);
"""

_lock = threading.Lock()
_conn_cache: dict[str, sqlite3.Connection] = {}


def _conn() -> sqlite3.Connection:
    key = str(DB_PATH)
    c = _conn_cache.get(key)
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False,
                            isolation_level=None)
        c.row_factory = sqlite3.Row
        c.executescript(_SCHEMA)
        _conn_cache[key] = c
    return c


# Arg di SCOPE = l'«oggetto/contenitore» che una CRUD richiede (NON il contenuto).
# Vocabolario CHIUSO §2.2-style (curato, estendibile, NON ad-hoc): solo arg che
# identificano un CONTENITORE/target stabile e ricordabile. Esclude i content
# (title/body/query_text/content) che cambiano a ogni chiamata.
SCOPE_ARGS: frozenset = frozenset({
    "repo", "calendar", "account", "board", "project", "workspace",
    "base_path", "org", "owner",
})


def is_scope_arg(arg_name: str) -> bool:
    return arg_name in SCOPE_ARGS


def _provider_qualifiers() -> set:
    """Set dei qualifier PROVIDER (skill/backend, es. github/google_workspace).
    SoT = chiavi del concept `provider.markers` in detection_lexicon (no
    hardcoding; union it/en delle suffix dichiarate)."""
    try:
        import detection_lexicon as _dl
        return {k.lstrip("_") for k in _dl.mapping("provider.markers")}
    except Exception:
        return set()


def domain_for(executor_name: str) -> Optional[str]:
    """Dominio di raggruppamento per la granularità per-oggetto/skill.

    qualifier se PROVIDER (github/google_workspace → raggruppa tutta la skill),
    altrimenti l'object §2.2 (un qualifier di FORMATO come `pdf` NON separa:
    read_files_pdf condivide il dominio `files` con find_files). Sorgente unica =
    naming_grammar (no split ad-hoc)."""
    try:
        from naming_grammar import parse_name
        nc = parse_name(executor_name)
    except Exception:
        nc = None
    if nc is None:
        return None
    qual = getattr(nc, "qualifier", None)
    obj = getattr(nc, "obj", None) or getattr(nc, "object", None)
    if qual and qual in _provider_qualifiers():
        return qual
    return obj or qual or None


def get_default(actor: str, domain: str, arg_name: str) -> Optional[str]:
    """Ultimo valore ricordato per (actor, domain, arg) o None."""
    if not (actor and domain and arg_name):
        return None
    with _lock:
        row = _conn().execute(
            "SELECT last_value FROM args_defaults "
            "WHERE actor=? AND domain=? AND arg_name=?",
            (actor, domain, arg_name),
        ).fetchone()
    return row["last_value"] if row else None


def set_default(actor: str, domain: str, arg_name: str, value: str) -> None:
    """Memorizza/aggiorna l'ultimo valore (UPSERT, incrementa uses)."""
    if not (actor and domain and arg_name) or value is None:
        return
    v = str(value).strip()
    if not v:
        return
    now = now_iso_z()
    with _lock:
        _conn().execute(
            "INSERT INTO args_defaults (actor, domain, arg_name, last_value, "
            "updated_at, uses) VALUES (?,?,?,?,?,1) "
            "ON CONFLICT(actor, domain, arg_name) DO UPDATE SET "
            "last_value=excluded.last_value, updated_at=excluded.updated_at, "
            "uses=uses+1",
            (actor, domain, arg_name, v, now),
        )


def sweep_unused(days: int = 90) -> int:
    """Elimina i default non aggiornati da > `days` giorni. Ritorna n. righe."""
    import datetime as _dt
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=max(1, days))).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _lock:
        cur = _conn().execute(
            "DELETE FROM args_defaults WHERE updated_at < ?", (cutoff,))
        return cur.rowcount or 0
