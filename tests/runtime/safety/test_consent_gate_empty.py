"""test_consent_gate_empty — §2.11/§2.8: il consent-gate outbound su 0 elementi
NON deve chiedere approvazione (bug live 22/6 «approvo 0 elementi?»). Il gate
runtime inietta guard_count=${stepN.@count}; risolto a 0 → get_approval passa
trasparente senza dialog. Risolto a N>0 / non risolto → gate normale."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "executors" / "get_approval"))
from engine import executor as E  # noqa: E402
from engine.types import StepRun  # noqa: E402
import get_approval as G  # noqa: E402


def _sr(count):
    return StepRun(tool="find_issues_github", args={}, step_idx=0, ok=True,
                   latency_ms=1,
                   result={"ok": True, "ok_count": count, "entries": [{}] * count})


def test_guard_count_resolves_to_int():
    out = E._resolve_stepref({"guard_count": "${step1.@count}"}, [_sr(0)])
    assert out["guard_count"] == 0 and isinstance(out["guard_count"], int)
    out5 = E._resolve_stepref({"guard_count": "${step1.@count}"}, [_sr(5)])
    assert out5["guard_count"] == 5


def test_short_circuit_on_zero():
    r = G.invoke({"guard_count": 0, "prompt": "x",
                  "on_approve": {"tool": "final_answer"}})
    assert r["ok"] is True and r["decision"] == "approved"


def test_normal_gate_when_nonzero():
    r = G.invoke({"guard_count": 3, "prompt": "Approvo?",
                  "on_approve": {"tool": "send_messages"},
                  "actor": "roberto", "channel": "telegram"})
    assert r.get("decision") == "input_required"


def test_normal_gate_when_unresolved_or_absent():
    # placeholder non risolto (literal) → int() fallisce → gate normale
    r = G.invoke({"guard_count": "${step1.@count}", "prompt": "Approvo?",
                  "on_approve": {"tool": "send_messages"},
                  "actor": "r", "channel": "t"})
    assert r.get("decision") == "input_required"
    r2 = G.invoke({"prompt": "Approvo?", "on_approve": {"tool": "send_messages"},
                   "actor": "r", "channel": "t"})
    assert r2.get("decision") == "input_required"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-q"])
