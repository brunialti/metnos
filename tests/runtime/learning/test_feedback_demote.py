"""E12 feedback→demote (24/5/2026).

Test del meccanismo che demota executor synth dopo N ✗ consecutive
sulla stessa pipeline. Vincoli ADR 0114 L3:
  - handcrafted MAI demoted
  - PROTECTED_NAMES MAI demoted
  - idempotente (gia' deprecated → no-op)
  - soglia configurabile via env METNOS_FEEDBACK_DEMOTE_THRESHOLD

Run: python3 -m pytest tests/runtime/learning/test_feedback_demote.py -v
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isola FEEDBACK_PATH, TURNS_DIR, executor_stats DB, audit dir.

    Ricarica i moduli interessati cosi' che le path top-level (DB_PATH,
    EFFICACY_AUDIT_DIR) puntino a tmp_path.
    """
    fb_path = tmp_path / "turn_feedback.jsonl"
    turns_dir = tmp_path / "turns"
    turns_dir.mkdir()
    db_path = tmp_path / "executor_stats.db"
    audit_dir = tmp_path / "audit"

    monkeypatch.setenv("METNOS_EXECUTOR_STATS_DB", str(db_path))

    import executor_aging
    importlib.reload(executor_aging)
    monkeypatch.setattr(executor_aging, "EFFICACY_AUDIT_DIR", audit_dir)

    import turn_feedback
    importlib.reload(turn_feedback)
    monkeypatch.setattr(turn_feedback, "FEEDBACK_PATH", fb_path)
    monkeypatch.setattr(turn_feedback, "TURNS_DIR", turns_dir)

    yield {
        "fb_path": fb_path,
        "turns_dir": turns_dir,
        "db_path": db_path,
        "audit_dir": audit_dir,
        "TF": turn_feedback,
        "EA": executor_aging,
    }

    # ripristina default DB per altri test
    importlib.reload(executor_aging)
    importlib.reload(turn_feedback)


def _write_turn(turns_dir: Path, turn_id: str, user_query: str,
                tools: list[str]) -> None:
    steps = [{"chosen_tool": t, "canonical_query":
              user_query.lower() if i == 0 else "",
              "llm_in_tokens": 1000, "llm_latency_ms": 500}
             for i, t in enumerate(tools)]
    fp = turns_dir / "today.jsonl"
    existing = fp.read_text() if fp.exists() else ""
    fp.write_text(
        existing + json.dumps({
            "turn_id": turn_id, "user_query": user_query, "steps": steps,
        }) + "\n",
        encoding="utf-8",
    )


def _trigger_errors(env, *, tool: str, query: str, n: int) -> list[dict]:
    """Genera n turni che usano `tool` e applica n feedback ✗."""
    TF = env["TF"]
    recs = []
    for i in range(n):
        tid = f"t{i:04d}"
        _write_turn(env["turns_dir"], tid, query, [tool, "final_answer"])
        recs.append(TF.apply_feedback(tid, "error"))
    return recs


class TestSynthDemoted:
    """3 ✗ consecutive su synth → deprecated."""

    def test_synth_below_threshold_kept(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "3")
        env["EA"].register("find_evil_synth", source="synth:reactive")
        recs = _trigger_errors(env, tool="find_evil_synth",
                                query="bad q", n=2)
        # Nessun demote effect sui 2 record
        for rec in recs:
            assert all(e.get("type") != "feedback_demote"
                       for e in rec["effects"])
        stat = env["EA"].lookup("find_evil_synth")
        assert stat.deprecated_at is None

    def test_synth_at_threshold_demoted(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "3")
        env["EA"].register("find_evil_synth", source="synth:reactive")
        recs = _trigger_errors(env, tool="find_evil_synth",
                                query="bad q", n=3)
        # Il 3° record ha l'effect feedback_demote
        demote_effects = [
            e for rec in recs for e in rec["effects"]
            if e.get("type") == "feedback_demote"
        ]
        assert len(demote_effects) == 1
        assert demote_effects[0]["action"] == "demoted"
        assert demote_effects[0]["name"] == "find_evil_synth"
        assert demote_effects[0]["consecutive_errors"] == 3
        stat = env["EA"].lookup("find_evil_synth")
        assert stat.deprecated_at is not None


class TestHandcraftedNeverDemoted:
    """Handcrafted con N ✗ consecutive → skip (ADR 0114 L3)."""

    def test_handcrafted_skipped(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "3")
        # compute_files_loc e' handcrafted ma NON in PROTECTED_NAMES
        env["EA"].register("compute_files_loc", source="handcrafted")
        recs = _trigger_errors(env, tool="compute_files_loc",
                                query="loc q", n=4)
        demote_effects = [
            e for rec in recs for e in rec["effects"]
            if e.get("type") == "feedback_demote"
        ]
        # L'ager e' stato chiamato dalla 3a in poi, ma ha skipped
        assert len(demote_effects) >= 1
        assert all(e["action"] == "skip_handcrafted"
                   for e in demote_effects)
        stat = env["EA"].lookup("compute_files_loc")
        assert stat.deprecated_at is None


class TestProtectedNeverDemoted:
    """PROTECTED_NAMES → skip."""

    def test_protected_name_skipped(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "3")
        # find_files e' in PROTECTED_NAMES
        env["EA"].register("find_files", source="handcrafted")
        recs = _trigger_errors(env, tool="find_files",
                                query="find q", n=3)
        demote_effects = [
            e for rec in recs for e in rec["effects"]
            if e.get("type") == "feedback_demote"
        ]
        assert len(demote_effects) == 1
        assert demote_effects[0]["action"] == "skip_protected"
        stat = env["EA"].lookup("find_files")
        assert stat.deprecated_at is None


class TestOkResetsCounter:
    """Un ✓ sullo stesso tool resetta il counter consecutivo."""

    def test_ok_in_middle_resets(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "3")
        TF = env["TF"]
        env["EA"].register("find_evil_synth", source="synth:reactive")
        # 2 ✗ su tool
        _trigger_errors(env, tool="find_evil_synth", query="q1", n=2)
        # 1 ✓ esplicito sullo stesso tool (scritto direttamente nel file
        # per simulare reinforce_path/noop neutral senza fast-path)
        import time as _time
        with env["fb_path"].open("a") as fh:
            fh.write(json.dumps({
                "turn_id": "tok", "action": "ok", "by": "user",
                "ts": _time.time(),
                "user_query": "q2",
                "approved_pipeline": ["find_evil_synth"],
            }) + "\n")
        # Ora 2 ✗ aggiuntivi (totale post-reset = 2, sotto soglia)
        recs = _trigger_errors(env, tool="find_evil_synth", query="q3", n=2)
        demote_effects = [
            e for rec in recs for e in rec["effects"]
            if e.get("type") == "feedback_demote"
        ]
        assert demote_effects == []
        stat = env["EA"].lookup("find_evil_synth")
        assert stat.deprecated_at is None


class TestIdempotent:
    """Re-trigger sopra soglia su gia'-deprecated → skip_already_deprecated."""

    def test_re_trigger_idempotent(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "3")
        env["EA"].register("find_evil_synth", source="synth:reactive")
        _trigger_errors(env, tool="find_evil_synth", query="q", n=3)
        stat1 = env["EA"].lookup("find_evil_synth")
        assert stat1.deprecated_at is not None
        first_iso = stat1.deprecated_at
        # 2 ✗ ulteriori: l'ager viene richiamato ma deve essere no-op
        recs = _trigger_errors(env, tool="find_evil_synth", query="q", n=2)
        demote_effects = [
            e for rec in recs for e in rec["effects"]
            if e.get("type") == "feedback_demote"
        ]
        assert len(demote_effects) == 2
        for e in demote_effects:
            assert e["action"] == "skip_already_deprecated"
        # deprecated_at non e' cambiato
        stat2 = env["EA"].lookup("find_evil_synth")
        assert stat2.deprecated_at == first_iso


class TestEnvOverrideThreshold:
    """METNOS_FEEDBACK_DEMOTE_THRESHOLD override il default."""

    def test_threshold_lowered_to_2(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "2")
        env["EA"].register("find_evil_synth", source="synth:reactive")
        recs = _trigger_errors(env, tool="find_evil_synth",
                                query="q", n=2)
        demote_effects = [
            e for rec in recs for e in rec["effects"]
            if e.get("type") == "feedback_demote"
        ]
        assert len(demote_effects) == 1
        assert demote_effects[0]["action"] == "demoted"
        assert demote_effects[0]["consecutive_errors"] == 2

    def test_threshold_zero_disables(self, env, monkeypatch):
        monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "0")
        env["EA"].register("find_evil_synth", source="synth:reactive")
        recs = _trigger_errors(env, tool="find_evil_synth",
                                query="q", n=5)
        demote_effects = [
            e for rec in recs for e in rec["effects"]
            if e.get("type") == "feedback_demote"
        ]
        assert demote_effects == []
        stat = env["EA"].lookup("find_evil_synth")
        assert stat.deprecated_at is None
