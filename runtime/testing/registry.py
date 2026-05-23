#!/usr/bin/env python3
"""
registry.py — gestione del database test (modules, cases, dependencies, runs).

API principali:
    Registry.open(db_path)           apre/crea il DB con lo schema
    Registry.add_module(...)         dichiara un modulo
    Registry.add_dependency(a, b)    dichiara che a usa b (cluster derivato)
    Registry.add_case(...)           inserisce/aggiorna un test case
    Registry.cluster_of(name)        ritorna {set di moduli del cluster}
    Registry.cases_for_module(name)  ritorna [test_cases]
    Registry.cases_for_cluster(name) ritorna [test_cases dei moduli del cluster]
    Registry.cases_at_level(level)   ritorna tutti i case a un certo livello
    Registry.record_run(case_id, ...)  salva un run
    Registry.summary()               statistiche
"""
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB = Path(__file__).resolve().parents[2] / "runtime/testing/tests.db"


@dataclass
class TestCase:
    id: int
    module_id: int
    module_name: str
    name: str
    level: str
    category: str
    test_kind: str
    setup_code: str
    test_code: str
    teardown_code: str
    expected: str
    enabled: bool


class Registry:
    def __init__(self, conn):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    @classmethod
    def open(cls, db_path=DEFAULT_DB):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        conn.commit()
        return cls(conn)

    # --- Moduli ---

    def add_module(self, name, kind, source_path="", description=""):
        cur = self.conn.execute(
            "INSERT INTO modules (name, kind, source_path, description) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, source_path=excluded.source_path, description=excluded.description",
            (name, kind, source_path, description),
        )
        self.conn.commit()
        return self._module_id(name)

    def _module_id(self, name):
        row = self.conn.execute("SELECT id FROM modules WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None

    def add_dependency(self, module_name, depends_on_name, relation="uses"):
        a = self._module_id(module_name)
        b = self._module_id(depends_on_name)
        if a is None or b is None:
            raise KeyError(f"unknown module: {module_name if a is None else depends_on_name}")
        self.conn.execute(
            "INSERT OR IGNORE INTO module_dependencies (module_id, depends_on_id, relation) VALUES (?,?,?)",
            (a, b, relation),
        )
        self.conn.commit()

    def cluster_of(self, name):
        """Cluster = il modulo + tutti i vicini diretti (in entrambe le direzioni)."""
        mid = self._module_id(name)
        if mid is None:
            return set()
        rows = self.conn.execute(
            """
            SELECT DISTINCT m.name FROM modules m
            WHERE m.id = ?
               OR m.id IN (SELECT depends_on_id FROM module_dependencies WHERE module_id = ?)
               OR m.id IN (SELECT module_id     FROM module_dependencies WHERE depends_on_id = ?)
            """,
            (mid, mid, mid),
        ).fetchall()
        return {r["name"] for r in rows}

    def list_modules(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM modules ORDER BY name").fetchall()]

    # --- Test cases ---

    def add_case(self, module_name, name, level, category, test_kind,
                 test_code, setup_code="", teardown_code="", expected="", enabled=True):
        mid = self._module_id(module_name)
        if mid is None:
            raise KeyError(f"unknown module: {module_name}")
        self.conn.execute(
            """
            INSERT INTO test_cases (module_id, name, level, category, test_kind,
                                    setup_code, test_code, teardown_code, expected, enabled)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(module_id, name) DO UPDATE SET
                level=excluded.level, category=excluded.category, test_kind=excluded.test_kind,
                setup_code=excluded.setup_code, test_code=excluded.test_code,
                teardown_code=excluded.teardown_code, expected=excluded.expected,
                enabled=excluded.enabled
            """,
            (mid, name, level, category, test_kind, setup_code, test_code, teardown_code, expected, int(enabled)),
        )
        self.conn.commit()

    def _row_to_case(self, row):
        return TestCase(
            id=row["id"], module_id=row["module_id"], module_name=row["module_name"],
            name=row["name"], level=row["level"], category=row["category"],
            test_kind=row["test_kind"], setup_code=row["setup_code"] or "",
            test_code=row["test_code"], teardown_code=row["teardown_code"] or "",
            expected=row["expected"] or "", enabled=bool(row["enabled"]),
        )

    def _select_cases(self, where, params):
        sql = f"""
            SELECT c.*, m.name AS module_name
            FROM test_cases c
            JOIN modules m ON m.id = c.module_id
            WHERE {where}
            ORDER BY c.level, m.name, c.name
        """
        return [self._row_to_case(r) for r in self.conn.execute(sql, params).fetchall()]

    def cases_for_module(self, name, only_enabled=True):
        clauses = ["m.name = ?"]
        params = [name]
        if only_enabled:
            clauses.append("c.enabled = 1")
        return self._select_cases(" AND ".join(clauses), params)

    def cases_for_cluster(self, name, only_enabled=True):
        cluster = self.cluster_of(name)
        if not cluster:
            return []
        placeholders = ",".join("?" * len(cluster))
        clauses = [f"m.name IN ({placeholders})"]
        params = list(cluster)
        if only_enabled:
            clauses.append("c.enabled = 1")
        return self._select_cases(" AND ".join(clauses), params)

    def cases_at_level(self, level, only_enabled=True):
        clauses = ["c.level = ?"]
        params = [level]
        if only_enabled:
            clauses.append("c.enabled = 1")
        return self._select_cases(" AND ".join(clauses), params)

    def all_cases(self, only_enabled=True):
        clauses = []
        params = []
        if only_enabled:
            clauses.append("c.enabled = 1")
        where = " AND ".join(clauses) if clauses else "1=1"
        return self._select_cases(where, params)

    # --- Runs ---

    def record_run(self, case_id, status, duration_ms, output="", failure_detail="", triggered_by="manual"):
        self.conn.execute(
            "INSERT INTO test_runs (case_id, ts, status, duration_ms, output, failure_detail, triggered_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (case_id, time.time(), status, duration_ms, output[:5000], failure_detail[:5000], triggered_by),
        )
        self.conn.commit()

    def summary(self):
        n_modules = self.conn.execute("SELECT COUNT(*) c FROM modules").fetchone()["c"]
        n_cases = self.conn.execute("SELECT COUNT(*) c FROM test_cases WHERE enabled=1").fetchone()["c"]
        by_level = {
            r["level"]: r["c"] for r in
            self.conn.execute("SELECT level, COUNT(*) c FROM test_cases WHERE enabled=1 GROUP BY level").fetchall()
        }
        last_status = {
            r["last_status"] or "(never run)": r["c"] for r in
            self.conn.execute("SELECT last_status, COUNT(*) c FROM last_run_per_case GROUP BY last_status").fetchall()
        }
        return {
            "modules": n_modules,
            "cases_total": n_cases,
            "cases_by_level": by_level,
            "last_status": last_status,
        }


def main():
    r = Registry.open()
    s = r.summary()
    print(json.dumps(s, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
