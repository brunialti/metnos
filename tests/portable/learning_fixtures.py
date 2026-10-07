"""Synthetic learning fixtures shared by portable and runtime tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_aging_db(tmp_path, monkeypatch):
    """Isola DB executor_stats e turns_dir in tmp_path."""
    db_path = tmp_path / "executor_stats.db"
    turns_dir = tmp_path / "turns"
    turns_dir.mkdir()
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("METNOS_EXECUTOR_STATS_DB", str(db_path))
    # reload modulo per applicare env var (DB_PATH e' top-level)
    import importlib
    import executor_aging
    importlib.reload(executor_aging)
    monkeypatch.setattr(executor_aging, "EFFICACY_AUDIT_DIR", audit_dir)
    yield {
        "db_path": db_path,
        "turns_dir": turns_dir,
        "audit_dir": audit_dir,
        "module": executor_aging,
    }
    # restore default DB_PATH for other tests
    importlib.reload(executor_aging)


def _write_turn_log(turns_dir: Path, fname: str, steps: list[dict]):
    """Crea un turn JSONL con 1 turn contenente i step passati."""
    turn = {
        "ts_start": 0.0,
        "ts_end": 1.0,
        "user_query": "test",
        "turn_id": "abc123",
        "mode": "local",
        "candidates": [],
        "steps": steps,
        "final_message": "",
        "final_kind": "answer",
    }
    (turns_dir / fname).write_text(json.dumps(turn) + "\n", encoding="utf-8")


def _make_catalog_with(executors_by_name):
    """Costruisce un Catalog in-memory popolato. Bypass tomllib parse."""
    import loader
    catalog = loader.Catalog()
    for name, info in executors_by_name.items():
        ex = loader.Executor(
            name=name,
            version="0.1.0",
            description=f"stub {name}",
            affinity=list(info["affinity"]),
            args_schema={},
            capabilities=[],
            tests=[],
            code_path=info.get("manifest_path"),
            manifest_path=info["manifest_path"],
            signed_by="(test)",
            lifecycle="active",
            # Come il caricatore la calcola dal manifest: un sintetizzato
            # dichiara `origin`, un nativo no.
            source=info.get("source", "handcrafted"),
        )
        catalog.executors[name] = ex
    return catalog
