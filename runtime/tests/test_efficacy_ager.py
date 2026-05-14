"""Test del Layer 3 di synth admission: efficacy ager (ADR 0114).

Scenario: synth con success_rate < 0.20 dopo >=100 invocations va a
deprecated; con success_rate < 0.05 dopo altri 30 inv va a archived.
Handcrafted MAI demoted. Idempotente.
"""
from __future__ import annotations

import json
import sys
import sqlite3
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


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


def _seed_stats(db_path: Path, name: str, source: str | None = "synth:reactive"):
    """Inserisce un row in executor_stats (non chiama .register che assume
    schema gia' presente). Apriamo la conn via il modulo per garantire
    creazione schema."""
    import executor_aging
    executor_aging.register(name, source=source or "handcrafted")


class TestSynthLowSuccessDemoted:
    """Synth con success_rate < 0.20 e total >= 100 → deprecated."""

    def test_synth_below_threshold_deprecated(self, isolated_aging_db):
        ea = isolated_aging_db["module"]
        turns_dir = isolated_aging_db["turns_dir"]
        # Crea 100 invocations: 10 ok + 90 fail.
        steps = []
        for i in range(10):
            steps.append({"step_num": i, "chosen_tool": "find_evil_synth",
                          "result": {"ok": True, "entries": [{"x": 1}]},
                          "error": None})
        for i in range(90):
            steps.append({"step_num": i + 10, "chosen_tool": "find_evil_synth",
                          "result": {"ok": False, "entries": []},
                          "error": "exec_fail"})
        _write_turn_log(turns_dir, "2026-05-01.jsonl", steps)
        _seed_stats(isolated_aging_db["db_path"], "find_evil_synth", source="synth:reactive")

        summary = ea.apply_efficacy_ager(turns_dir=turns_dir)
        names = [d["name"] for d in summary["deprecated"]]
        assert "find_evil_synth" in names
        # Stats DB updated
        s = ea.lookup("find_evil_synth")
        assert s.deprecated_at is not None


class TestSynthHighSuccessUnchanged:
    """Synth con success_rate alto resta active anche con tante invocations."""

    def test_synth_above_threshold_kept(self, isolated_aging_db):
        ea = isolated_aging_db["module"]
        turns_dir = isolated_aging_db["turns_dir"]
        steps = []
        for i in range(100):
            steps.append({"step_num": i, "chosen_tool": "find_good_synth",
                          "result": {"ok": True, "entries": [{"x": 1}]},
                          "error": None})
        _write_turn_log(turns_dir, "2026-05-01.jsonl", steps)
        _seed_stats(isolated_aging_db["db_path"], "find_good_synth", source="synth:reactive")

        summary = ea.apply_efficacy_ager(turns_dir=turns_dir)
        assert summary["deprecated"] == []
        s = ea.lookup("find_good_synth")
        assert s.deprecated_at is None


class TestHandcraftedNeverDemoted:
    """Handcrafted con success_rate=0% e 1000 inv MAI deprecated."""

    def test_handcrafted_kept_even_with_zero_success(self, isolated_aging_db):
        ea = isolated_aging_db["module"]
        turns_dir = isolated_aging_db["turns_dir"]
        steps = []
        # Usa nome handcrafted non in PROTECTED_NAMES (es. compute_files_loc).
        for i in range(150):
            steps.append({"step_num": i, "chosen_tool": "compute_files_loc",
                          "result": {"ok": False, "entries": []},
                          "error": "fake_fail"})
        _write_turn_log(turns_dir, "2026-05-01.jsonl", steps)
        _seed_stats(isolated_aging_db["db_path"], "compute_files_loc", source="handcrafted")

        summary = ea.apply_efficacy_ager(turns_dir=turns_dir)
        # Skipped because handcrafted (source NOT starting with synth)
        assert summary["skipped_handcrafted"] >= 1
        assert summary["deprecated"] == []
        s = ea.lookup("compute_files_loc")
        assert s.deprecated_at is None


class TestInvocationsBelowThreshold:
    """Synth con total < min_invocations: nessuna azione."""

    def test_synth_with_few_invocations_no_action(self, isolated_aging_db):
        ea = isolated_aging_db["module"]
        turns_dir = isolated_aging_db["turns_dir"]
        steps = []
        # 50 invocations, tutte fail → success_rate=0 ma total<100
        for i in range(50):
            steps.append({"step_num": i, "chosen_tool": "find_x_synth",
                          "result": {"ok": False, "entries": []},
                          "error": "fail"})
        _write_turn_log(turns_dir, "2026-05-01.jsonl", steps)
        _seed_stats(isolated_aging_db["db_path"], "find_x_synth", source="synth:reactive")

        summary = ea.apply_efficacy_ager(turns_dir=turns_dir)
        assert summary["skipped_below_threshold"] >= 1
        assert summary["deprecated"] == []
        s = ea.lookup("find_x_synth")
        assert s.deprecated_at is None


class TestArchiveAfterReEval:
    """Deprecated synth con success_rate < 0.05 dopo altri >=30 invocations
    viene archived."""

    def test_already_deprecated_archived_after_re_eval(self, isolated_aging_db):
        ea = isolated_aging_db["module"]
        turns_dir = isolated_aging_db["turns_dir"]
        # 130 invocations: 3 ok + 127 fail = success_rate ~0.023 < 0.05
        steps = []
        for i in range(3):
            steps.append({"step_num": i, "chosen_tool": "find_dead_synth",
                          "result": {"ok": True, "entries": [{"a": 1}]},
                          "error": None})
        for i in range(127):
            steps.append({"step_num": i + 3, "chosen_tool": "find_dead_synth",
                          "result": {"ok": False, "entries": []},
                          "error": "fail"})
        _write_turn_log(turns_dir, "2026-05-01.jsonl", steps)
        # Pre-mark as deprecated
        _seed_stats(isolated_aging_db["db_path"], "find_dead_synth", source="synth:reactive")
        # forza deprecated_at non null
        old_iso = "2026-04-01T00:00:00Z"
        conn = sqlite3.connect(str(isolated_aging_db["db_path"]))
        conn.execute("UPDATE executor_stats SET deprecated_at=? WHERE name=?",
                     (old_iso, "find_dead_synth"))
        conn.commit(); conn.close()

        summary = ea.apply_efficacy_ager(turns_dir=turns_dir)
        names = [d["name"] for d in summary["archived"]]
        assert "find_dead_synth" in names
        s = ea.lookup("find_dead_synth")
        assert s.archived_at is not None


class TestIdempotent:
    """Run 2x → la seconda non aggiunge audit lines per lo stesso state."""

    def test_re_run_does_not_re_demote(self, isolated_aging_db):
        ea = isolated_aging_db["module"]
        turns_dir = isolated_aging_db["turns_dir"]
        steps = []
        for i in range(100):
            steps.append({"step_num": i, "chosen_tool": "find_z_synth",
                          "result": {"ok": False, "entries": []},
                          "error": "fail"})
        _write_turn_log(turns_dir, "2026-05-01.jsonl", steps)
        _seed_stats(isolated_aging_db["db_path"], "find_z_synth", source="synth:reactive")

        s1 = ea.apply_efficacy_ager(turns_dir=turns_dir)
        assert any(d["name"] == "find_z_synth" for d in s1["deprecated"])
        # Re-run subito: gia' deprecated, no re-deprecate. total=100 ancora,
        # quindi NON ci sono i 30 in piu' per archived.
        s2 = ea.apply_efficacy_ager(turns_dir=turns_dir)
        assert all(d["name"] != "find_z_synth" for d in s2["deprecated"])
        assert all(d["name"] != "find_z_synth" for d in s2["archived"])
