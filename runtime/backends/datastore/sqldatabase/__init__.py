"""sqldatabase — base SQL condivisa per i backend di database relazionali.

`SqlDatabaseBackend` implementa il contratto `store.Backend` costruendo SQL
GENERICO (CREATE/SELECT/INSERT…ON CONFLICT/UPDATE/DELETE) parametrizzato da
pochi hook di DIALETTO che ogni motore fornisce:
  - `_connect()`        → connessione DB-API
  - `_type_sql(t)`      → tipo del motore per un tipo astratto store
  - `_introspect(conn, table)` → colonne esistenti (per migrazione additiva)
  - `placeholder`       → segnaposto parametri ("?" sqlite, "%s"/"$N" pg)

Aggiungere postgres = una sottoclasse con questi hook, ZERO modifiche allo
Store o agli executor (regola «stessi nomi, backend diversi»). §7.9.
"""
from __future__ import annotations

import json
import threading
from typing import Optional, Sequence

from store import Backend, Schema


class SqlDatabaseBackend(Backend):
    """Base relazionale: SQL generico + hook di dialetto. Una connessione per
    istanza (lazy), lock per istanza (thread-safe), schema garantito una volta
    per tabella."""

    placeholder: str = "?"
    capabilities = frozenset({"json"})

    def __init__(self):
        self._connection = None
        self._lock = threading.RLock()
        self._ensured: set[str] = set()

    # ── hook di dialetto (le sottoclassi forniscono) ────────────────────
    def _connect(self):
        raise NotImplementedError

    def _type_sql(self, abstract_type: str) -> str:
        raise NotImplementedError

    def _introspect(self, conn, table: str) -> set:
        raise NotImplementedError

    # ── connessione ─────────────────────────────────────────────────────
    def _cx(self):
        if self._connection is None:
            self._connection = self._connect()
        return self._connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
                self._ensured.clear()

    # ── schema + migrazione additiva ────────────────────────────────────
    def ensure_schema(self, schema: Schema) -> None:
        with self._lock:
            if schema.table in self._ensured:
                return
            cx = self._cx()
            cols = ", ".join(f"{n} {self._type_sql(t)}"
                             for n, t in schema.columns.items())
            pk = (f", PRIMARY KEY ({', '.join(schema.primary_key)})"
                  if schema.primary_key else "")
            cx.execute(f"CREATE TABLE IF NOT EXISTS {schema.table} ({cols}{pk})")
            existing = self._introspect(cx, schema.table)
            for n, t in schema.columns.items():
                if n not in existing:
                    cx.execute(f"ALTER TABLE {schema.table} "
                               f"ADD COLUMN {n} {self._type_sql(t)}")
            for idx in schema.indexes:
                iname = f"idx_{schema.table}_{'_'.join(idx)}"
                cx.execute(f"CREATE INDEX IF NOT EXISTS {iname} "
                           f"ON {schema.table} ({', '.join(idx)})")
            cx.commit()
            self._ensured.add(schema.table)

    # ── helper SQL ──────────────────────────────────────────────────────
    def _where(self, where: Optional[dict]):
        if not where:
            return "", []
        parts, params = [], []
        for col, val in where.items():
            if isinstance(val, (list, tuple, set)):
                vals = list(val)
                if not vals:
                    parts.append("0=1")          # IN vuoto → nessun match
                    continue
                ph = ", ".join(self.placeholder for _ in vals)
                parts.append(f"{col} IN ({ph})")
                params.extend(vals)
            else:
                parts.append(f"{col} = {self.placeholder}")
                params.append(val)
        return " WHERE " + " AND ".join(parts), params

    @staticmethod
    def _order(order: Optional[Sequence]) -> str:
        if not order:
            return ""
        parts = []
        for spec in order:
            if isinstance(spec, (tuple, list)):
                col, d = spec[0], (spec[1] if len(spec) > 1 else "asc")
            else:
                col, d = spec, "asc"
            d = "DESC" if str(d).lower().startswith("desc") else "ASC"
            parts.append(f"{col} {d}")
        return " ORDER BY " + ", ".join(parts)

    @staticmethod
    def _encode(value, is_json: bool):
        if is_json and value is not None and not isinstance(value, (str, bytes)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def _row_to_dict(self, schema: Schema, row) -> dict:
        d = dict(row)
        for c in schema.json_columns():
            v = d.get(c)
            if isinstance(v, str) and v:
                try:
                    d[c] = json.loads(v)
                except Exception:
                    pass
        return d

    # ── CRUD ────────────────────────────────────────────────────────────
    def find(self, schema, where=None, order=None, limit=None) -> list[dict]:
        sql = f"SELECT {', '.join(schema.columns)} FROM {schema.table}"
        clause, params = self._where(where)
        sql += clause + self._order(order)
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            cur = self._cx().execute(sql, params)
            return [self._row_to_dict(schema, r) for r in cur.fetchall()]

    def write(self, schema, rows, key) -> int:
        cols = list(schema.columns)
        jcols = schema.json_columns()
        ph = ", ".join(self.placeholder for _ in cols)
        collist = ", ".join(cols)
        if key:
            non_key = [c for c in cols if c not in key]
            if non_key:
                # Upsert PARZIALE (clobber-preserve, 20/6): un valore in arrivo
                # NULL (campo assente nella riga del producer) NON sovrascrive il
                # valore esistente → COALESCE(excluded, esistente). Un re-ingest
                # che porta solo alcuni campi (es. detect github: number/title,
                # senza status/reply) conserva status/accepted_reply già fissati.
                # Parità con MemoryBackend (merge che salta i None). Per AZZERARE
                # un campo si usa update() (SET esplicito), non l'upsert.
                upd = ", ".join(
                    f"{c}=COALESCE(excluded.{c}, {schema.table}.{c})"
                    for c in non_key)
                conflict = (f" ON CONFLICT({', '.join(key)}) "
                            f"DO UPDATE SET {upd}")
            else:
                conflict = f" ON CONFLICT({', '.join(key)}) DO NOTHING"
        else:
            conflict = ""
        sql = f"INSERT INTO {schema.table} ({collist}) VALUES ({ph}){conflict}"
        with self._lock:
            cx = self._cx()
            for row in rows:
                vals = [self._encode(row.get(c), c in jcols) for c in cols]
                cx.execute(sql, vals)
            cx.commit()
        return len(rows)

    def update(self, schema, values, where) -> int:
        jcols = schema.json_columns()
        sets = ", ".join(f"{c} = {self.placeholder}" for c in values)
        params = [self._encode(v, c in jcols) for c, v in values.items()]
        clause, wparams = self._where(where)
        with self._lock:
            cur = self._cx().execute(
                f"UPDATE {schema.table} SET {sets}{clause}", params + wparams)
            self._cx().commit()
            return cur.rowcount

    def delete(self, schema, where=None) -> int:
        clause, params = self._where(where)
        with self._lock:
            cur = self._cx().execute(
                f"DELETE FROM {schema.table}{clause}", params)
            self._cx().commit()
            return cur.rowcount

    def raw(self, query, params=()) -> list[dict]:
        with self._lock:
            cur = self._cx().execute(query, params)
            try:
                rows = cur.fetchall()
            except Exception:
                rows = []
            self._cx().commit()
            return [dict(r) for r in rows]
