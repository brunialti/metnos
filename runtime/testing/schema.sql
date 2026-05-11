-- Metnos test framework v1.1 — schema SQLite.
-- Tre livelli di test: module / cluster / system.
-- Cluster di un modulo X = X + tutti i moduli con cui X si interfaccia.
-- I cluster vengono derivati dal grafo module_dependencies (1 hop).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS modules (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,           -- 'executor' | 'runtime' | 'core'
    source_path  TEXT,                    -- path al file principale
    description  TEXT
);

-- Grafo delle dipendenze fra moduli.
-- (module_id depends_on depends_on_id) significa che module_id usa/chiama depends_on_id.
-- Il cluster di X = X + neighbors(X) sia in entrata sia in uscita.
CREATE TABLE IF NOT EXISTS module_dependencies (
    module_id      INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    depends_on_id  INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    relation       TEXT NOT NULL DEFAULT 'uses',  -- 'uses' | 'extends' | 'sandboxes'
    PRIMARY KEY (module_id, depends_on_id)
);

CREATE TABLE IF NOT EXISTS test_cases (
    id            INTEGER PRIMARY KEY,
    module_id     INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    level         TEXT NOT NULL,    -- 'module' | 'cluster' | 'system'
    category      TEXT NOT NULL,    -- 'happy' | 'edge' | 'failure' | 'security' | 'integration'
    test_kind     TEXT NOT NULL,    -- 'python' | 'shell' | 'birth' | 'e2e'
    setup_code    TEXT DEFAULT '',
    test_code     TEXT NOT NULL,    -- contenuto eseguibile o JSON di config
    teardown_code TEXT DEFAULT '',
    expected      TEXT DEFAULT '',  -- atteso (json o stringa, dipende dal kind)
    enabled       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (module_id, name)
);

CREATE INDEX IF NOT EXISTS idx_cases_module ON test_cases(module_id);
CREATE INDEX IF NOT EXISTS idx_cases_level  ON test_cases(level);

CREATE TABLE IF NOT EXISTS test_runs (
    id             INTEGER PRIMARY KEY,
    case_id        INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    ts             REAL NOT NULL,
    status         TEXT NOT NULL,   -- 'pass' | 'fail' | 'error' | 'skipped'
    duration_ms    INTEGER,
    output         TEXT,
    failure_detail TEXT,
    triggered_by   TEXT             -- 'manual' | 'module:<name>' | 'cluster:<name>' | 'system'
);

CREATE INDEX IF NOT EXISTS idx_runs_case ON test_runs(case_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_runs_ts   ON test_runs(ts DESC);

-- Vista comoda: ultimo run per ogni case
CREATE VIEW IF NOT EXISTS last_run_per_case AS
SELECT
    c.id          AS case_id,
    c.name        AS case_name,
    c.level       AS level,
    c.category    AS category,
    m.name        AS module,
    r.status      AS last_status,
    r.ts          AS last_ts,
    r.duration_ms AS last_duration_ms
FROM test_cases c
JOIN modules m ON m.id = c.module_id
LEFT JOIN test_runs r ON r.case_id = c.id
WHERE r.id IS NULL OR r.id = (
    SELECT MAX(id) FROM test_runs r2 WHERE r2.case_id = c.id
);
