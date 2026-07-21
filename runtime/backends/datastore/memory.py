"""memory.py — MemoryBackend: store.Backend in RAM (dict), NON persistente.

Stesso contratto di SqliteBackend, dati solo in-processo. Ruoli: (1) prova che
il contratto `Backend` non è SQL-shaped → "multi-backend" reale; (2) test di
parità istantanei (stessa suite vs sqlite); (3) store effimeri (scratch/cache).
NON è un bersaglio di query: il backend si sceglie nel registro, mai dalla
query. §7.9.
"""
from __future__ import annotations

import threading
from typing import Optional, Sequence

from store import Backend, Schema


def _matches(row: dict, where: Optional[dict]) -> bool:
    if not where:
        return True
    for col, val in where.items():
        rv = row.get(col)
        if isinstance(val, (list, tuple, set)):
            if rv not in val:
                return False
        elif rv != val:
            return False
    return True


def _order_rows(rows: list[dict], order: Optional[Sequence]) -> list[dict]:
    if not order:
        return rows
    for spec in reversed(order):           # stabile: ultima chiave applicata 1ª
        if isinstance(spec, (tuple, list)):
            col, d = spec[0], (spec[1] if len(spec) > 1 else "asc")
        else:
            col, d = spec, "asc"
        rev = str(d).lower().startswith("desc")
        rows = sorted(rows, key=lambda r: (r.get(col) is None, r.get(col)),
                      reverse=rev)
    return rows


class MemoryBackend(Backend):
    capabilities = frozenset({"json"})     # i dict restano nativi, niente serial.

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}
        self._lock = threading.RLock()

    def ensure_schema(self, schema: Schema) -> None:
        with self._lock:
            self._tables.setdefault(schema.table, [])

    def find(self, schema, where=None, order=None, limit=None) -> list[dict]:
        with self._lock:
            rows = [dict(r) for r in self._tables.get(schema.table, [])
                    if _matches(r, where)]
        rows = _order_rows(rows, order)
        if limit is not None:
            rows = rows[:max(0, int(limit))]
        return rows

    def write(self, schema, rows, key) -> int:
        with self._lock:
            tbl = self._tables.setdefault(schema.table, [])
            for row in rows:
                row = dict(row)
                if key:
                    idx = next(
                        (i for i, r in enumerate(tbl)
                         if all(r.get(k) == row.get(k) for k in key)), None)
                    if idx is not None:
                        # upsert PARZIALE (clobber-preserve, 20/6): un valore in
                        # arrivo None non sovrascrive l'esistente — parità con
                        # SqliteBackend COALESCE(excluded, esistente).
                        tbl[idx] = {**tbl[idx],
                                    **{kk: vv for kk, vv in row.items()
                                       if vv is not None}}
                        continue
                tbl.append(row)
        return len(rows)

    def update(self, schema, values, where) -> int:
        n = 0
        with self._lock:
            for r in self._tables.get(schema.table, []):
                if _matches(r, where):
                    r.update(values)
                    n += 1
        return n

    def delete(self, schema, where=None) -> int:
        with self._lock:
            tbl = self._tables.get(schema.table, [])
            keep = [r for r in tbl if not _matches(r, where)]
            n = len(tbl) - len(keep)
            self._tables[schema.table] = keep
        return n

    def close(self) -> None:
        with self._lock:
            self._tables.clear()
