"""sqlite.py — SqliteBackend: store.Backend su SQLite (unico backend
PERSISTENTE reale, 16/6/2026).

Fornisce solo gli hook di dialetto; tutto il SQL generico vive in
`SqlDatabaseBackend`. Aggiungere postgres = una sorella con altri hook.

Path risolto a CALL-TIME (seam di test, §7.11): env `METNOS_<NAME>_DB` → arg
`path` → `config.PATH_USER_DATA / "<name>.sqlite"`. I test passano `path=tmp`
o un `MemoryBackend`, niente reload di modulo.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from store import TEXT, INT, REAL, BLOB, JSON
from backends.datastore.sqldatabase import SqlDatabaseBackend


class SqliteBackend(SqlDatabaseBackend):
    placeholder = "?"
    capabilities = frozenset({"json"})

    _TYPE = {TEXT: "TEXT", INT: "INTEGER", REAL: "REAL",
             BLOB: "BLOB", JSON: "TEXT"}

    def __init__(self, path=None, *, default_name: str | None = None,
                 wal: bool = False):
        super().__init__()
        self._path = path
        self._default_name = default_name or "store"
        self._wal = wal

    def _resolve_path(self) -> Path:
        if self._path is not None:
            return Path(self._path)
        env = os.environ.get(f"METNOS_{self._default_name.upper()}_DB")
        if env:
            return Path(env)
        import config as _C
        return _C.PATH_USER_DATA / f"{self._default_name}.sqlite"

    def _connect(self):
        p = self._resolve_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        cx = sqlite3.connect(str(p), check_same_thread=False)
        cx.row_factory = sqlite3.Row
        if self._wal:
            cx.execute("PRAGMA journal_mode=WAL")
        return cx

    def _type_sql(self, abstract_type: str) -> str:
        return self._TYPE[abstract_type]

    def _introspect(self, conn, table: str) -> set:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
