from __future__ import annotations

import json
import stat

import llm_cost_sink
import sites_audit


def test_sites_audit_uses_bounded_private_writer(tmp_path, monkeypatch):
    target = tmp_path / "private" / "sites_audit.jsonl"
    monkeypatch.setattr(sites_audit, "AUDIT_PATH", target)
    sites_audit.record(
        "session_open", owner="test", domain="example.test",
        url="https://example.test/path?token=must-not-survive")

    row = json.loads(target.read_text())
    assert row["event"] == "session_open"
    assert "must-not-survive" not in row["url"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_llm_cost_sink_uses_bounded_writer(tmp_path, monkeypatch):
    target = tmp_path / "llm_usage.jsonl"
    monkeypatch.setattr(llm_cost_sink, "_default_path", lambda: target)
    llm_cost_sink._sink({
        "in_tokens": 10, "out_tokens": 2, "provider": "local",
        "model": "test", "kind": "test", "tier": "fast",
        "latency_ms": 3,
    })

    row = json.loads(target.read_text())
    assert row["in_tokens"] == 10
    assert row["out_tokens"] == 2
    assert "prompt" not in row
