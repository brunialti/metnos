# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 3 — Metnos code & workspace skeleton.

By the time this runs, ``$METNOS_REPO_DIR`` is set (by bootstrap.sh)
and points at a checked-out Metnos source tree. Phase 3:

- verifies the source tree has what we need (sentinel files)
- creates the empty sqlite databases the runtime expects
- copies a baseline ``i18n.sqlite`` with the MSG_* keys (placeholder
  here; the release pipeline will bundle the actual one).

No personal data is involved — this phase is fully replayable.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .. import ui


_EXPECTED_SOURCE_DIRS = (
    "install",
    # Future commits will land 'runtime/' and 'executors/'. While they
    # are absent (early stub state), we don't fail — we just warn so
    # the user knows phase 6 first-boot will be a no-op until the
    # actual runtime code lands in the repo.
)

_SQLITE_FILES = (
    # filename, schema-init SQL (minimal — runtime migrations bring up to date)
    ("scratchpad.db",     "CREATE TABLE IF NOT EXISTS scratchpad (turn_id TEXT, key TEXT, value BLOB, PRIMARY KEY (turn_id, key));"),
    ("scheduler_v2.sqlite", "CREATE TABLE IF NOT EXISTS schedule_entries (id INTEGER PRIMARY KEY, name TEXT, trigger TEXT, callback TEXT, args TEXT, next_fire_at REAL, enabled INTEGER DEFAULT 1);"),
    ("persons.sqlite",    "CREATE TABLE IF NOT EXISTS persons (slug TEXT PRIMARY KEY, display_name TEXT, embedding_face BLOB);"),
    ("users.sqlite",      "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT, created_at REAL, user_channels TEXT);"),
    ("host_health.json",  None),   # not sqlite, just empty placeholder
)


def _data_dir() -> Path:
    return Path(os.environ.get("METNOS_USER_DATA", Path.home() / ".local" / "share" / "metnos"))


def _init_sqlite(path: Path, schema: str | None) -> bool:
    if path.exists():
        ui.info(f"{path.name}: exists, leaving in place")
        return False
    if schema is None:
        path.write_text("{}\n")
        path.chmod(0o600)
        ui.ok(f"created placeholder: {path}")
        return True
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(schema)
        conn.commit()
    path.chmod(0o600)
    ui.ok(f"initialised sqlite: {path}")
    return True


def _init_i18n(data: Path) -> bool:
    """Seed ``i18n.sqlite`` from the bundled catalog.

    The runtime (``runtime/i18n.py``) reads the ``i18n`` table (key, lang,
    text, needs_translation, source_lang, …) and resolves MSG_*/ERR_*/WARN_*
    by key+lang. A fresh install MUST seed the full catalog or user-facing
    strings render as ``<missing:MSG_*>``. We copy the bundled seed
    (``install/data/i18n_seed.sqlite``). If it is absent, create an empty
    ``i18n`` table with the correct schema and WARN (the background i18n
    translator can fill it later, but coverage is incomplete until then).
    """
    import shutil
    p = data / "i18n.sqlite"
    if p.exists():
        ui.info("i18n.sqlite: exists, leaving in place")
        return False
    repo = os.environ.get("METNOS_INSTALL_ROOT", "")
    seed = Path(repo) / "install" / "data" / "i18n_seed.sqlite" if repo else None
    if seed and seed.exists():
        shutil.copyfile(seed, p)
        p.chmod(0o600)
        try:
            n = sqlite3.connect(str(p)).execute(
                "SELECT count(DISTINCT key) FROM i18n").fetchone()[0]
        except sqlite3.Error:
            n = "?"
        ui.ok(f"i18n.sqlite seeded from bundled catalog ({n} keys, en+it)")
        return True
    # Fallback: correct schema, empty — never the wrong `messages` table.
    with sqlite3.connect(str(p)) as conn:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS i18n ("
            "  key TEXT NOT NULL, lang TEXT NOT NULL, text TEXT,"
            "  needs_translation INTEGER NOT NULL DEFAULT 0, source_lang TEXT,"
            "  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),"
            "  PRIMARY KEY (key, lang));")
        conn.commit()
    p.chmod(0o600)
    ui.warn("i18n seed catalog not bundled — created an empty i18n table. "
            "User-facing strings may show <missing:KEY> until the translator "
            "runs. Release pipeline should ship install/data/i18n_seed.sqlite.")
    return True


def _verify_source() -> dict[str, Any]:
    """Check $METNOS_INSTALL_ROOT has the expected layout."""
    repo = os.environ.get("METNOS_INSTALL_ROOT")
    if not repo:
        ui.warn("METNOS_INSTALL_ROOT not set — did bootstrap.sh complete?")
        return {"source_ok": False}
    root = Path(repo)
    missing = [d for d in _EXPECTED_SOURCE_DIRS if not (root / d).exists()]
    if missing:
        ui.warn(f"source tree missing: {', '.join(missing)}")
    else:
        ui.ok(f"source tree complete at {root}")
    return {"source_ok": not missing, "repo_dir": str(root), "missing_dirs": missing}


def _sign_executors() -> dict[str, Any]:
    """Genera la keypair locale 'author' e firma TUTTI gli executor.

    Indispensabile: gli `.sig` spediti nel repo sono firmati con la chiave
    dell'autore upstream, NON trusted sulla macchina dell'utente → senza questo
    passo il loader rifiuta tutti gli executor handcrafted e il catalogo resta
    ai soli builtin (server vuoto). Idempotente.
    """
    repo = os.environ.get("METNOS_INSTALL_ROOT")
    venv = os.environ.get("METNOS_VENV")
    if not repo or not venv:
        ui.warn("METNOS_INSTALL_ROOT/METNOS_VENV non settati — salto la firma executor")
        return {"signed": False}
    py = str(Path(venv) / "bin" / "python")
    sign_py = str(Path(repo) / "runtime" / "sign.py")
    env = dict(os.environ)
    pp = f"{repo}:{repo}/runtime"
    env["PYTHONPATH"] = pp + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("METNOS_INSTALL_ROOT", repo)
    try:
        r = subprocess.run([py, sign_py, "sign-all"], env=env,
                           capture_output=True, text=True, timeout=300)
    except Exception as e:  # noqa: BLE001
        ui.warn(f"firma executor fallita: {e}")
        return {"signed": False, "error": str(e)}
    if r.returncode != 0:
        ui.warn(f"sign-all rc={r.returncode}: {(r.stderr or r.stdout)[:200]}")
        return {"signed": False, "rc": r.returncode}
    line = next((l for l in r.stdout.splitlines() if "sign-all:" in l),
                r.stdout.strip()[:120])
    ui.ok(line or "executor firmati")
    return {"signed": True, "report": line}


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner("Phase 3 — Metnos code & workspace", "Verify source, prepare empty databases")

    # 1. Source tree verification
    ui.step("Verifying source tree")
    notes.update(_verify_source())

    # 2. SQLite skeletons
    ui.step("Initialising empty workspace databases")
    data = _data_dir()
    data.mkdir(parents=True, exist_ok=True)
    created = 0
    for fname, schema in _SQLITE_FILES:
        if _init_sqlite(data / fname, schema):
            created += 1
    notes["sqlite_created"] = created

    # 3. i18n bootstrap
    ui.step("Bootstrapping i18n message store")
    _init_i18n(data)

    # 4. Firma degli executor (chiave locale) — senza questo il catalogo e' vuoto
    ui.step("Signing executors with a local key (sign-all)")
    notes["sign"] = _sign_executors()

    return notes
