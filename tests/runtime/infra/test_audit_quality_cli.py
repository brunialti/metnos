"""Test per `metnos-prompts audit-quality`.

Coperture:
  - cmd_audit_quality dry-run end-to-end
  - --apply persiste TOML in path mockato
  - threshold logic: tutti above → fidelity; alcuni lagging → frontier
  - sample size handling (all vs N)
  - _audit_decide aggregate stats
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[3] / "runtime") / "admin"))

from admin import prompts_cli as pc  # type: ignore


def _make_args(**kw):
    base = {
        "to": "en",
        "sample": "all",
        "apply": False,
        "report": False,
        "dry_run": True,
        "threshold_pct": 0.80,
        "individual_pct": 0.95,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ── _audit_decide ────────────────────────────────────────────────────────


def test_decide_all_above_recommends_fidelity():
    per_prompt = [
        {"role": "a", "ok": True,
         "fidelity": {"score": 0.90}, "frontier": {"score": 0.92}},
        {"role": "b", "ok": True,
         "fidelity": {"score": 0.88}, "frontier": {"score": 0.90}},
    ]
    d = pc._audit_decide(per_prompt, individual_pct=0.95, threshold_pct=0.80)
    assert d["recommended_tier"] == "fidelity"
    assert d["n_individual_above"] == 2
    assert d["threshold_satisfied"] is True


def test_decide_lagging_recommends_frontier():
    per_prompt = [
        {"role": "a", "ok": True,
         "fidelity": {"score": 0.50}, "frontier": {"score": 0.95}},
        {"role": "b", "ok": True,
         "fidelity": {"score": 0.60}, "frontier": {"score": 0.95}},
        {"role": "c", "ok": True,
         "fidelity": {"score": 0.55}, "frontier": {"score": 0.95}},
    ]
    d = pc._audit_decide(per_prompt, individual_pct=0.95, threshold_pct=0.80)
    assert d["recommended_tier"] == "frontier"
    assert d["n_individual_above"] == 0
    assert "a" in d["lagging_prompts"]


def test_decide_mixed_at_threshold():
    # 4 above, 1 below => 80% above => threshold soddisfatta
    per_prompt = [
        {"role": "a", "ok": True,
         "fidelity": {"score": 0.95}, "frontier": {"score": 0.95}},
        {"role": "b", "ok": True,
         "fidelity": {"score": 0.95}, "frontier": {"score": 0.95}},
        {"role": "c", "ok": True,
         "fidelity": {"score": 0.95}, "frontier": {"score": 0.95}},
        {"role": "d", "ok": True,
         "fidelity": {"score": 0.95}, "frontier": {"score": 0.95}},
        {"role": "e", "ok": True,
         "fidelity": {"score": 0.50}, "frontier": {"score": 0.95}},
    ]
    d = pc._audit_decide(per_prompt, individual_pct=0.95, threshold_pct=0.80)
    assert d["individual_above_pct"] == pytest.approx(0.80)
    assert d["recommended_tier"] == "fidelity"  # >= 0.80 → soddisfatta


def test_decide_empty_input():
    d = pc._audit_decide([], individual_pct=0.95, threshold_pct=0.80)
    assert d["recommended_tier"] == "fidelity"  # default safe
    assert d["n_valid"] == 0


# ── _audit_pick_prompts ──────────────────────────────────────────────────


def test_pick_prompts_all_returns_all():
    roles = pc._audit_pick_prompts("all")
    assert isinstance(roles, list)
    assert len(roles) >= 1  # almeno qualche .j2 in prompts/it/


def test_pick_prompts_sample_n():
    roles_all = pc._audit_pick_prompts("all")
    if len(roles_all) >= 3:
        roles3 = pc._audit_pick_prompts("3")
        assert len(roles3) == 3
        assert set(roles3).issubset(set(roles_all))


# ── cmd_audit_quality dry-run ────────────────────────────────────────────


def test_cmd_audit_quality_dry_run(capsys):
    args = _make_args(sample="3", dry_run=True)
    rc = pc.cmd_audit_quality(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Audit qualita'" in out or "Audit qualit" in out
    assert "RACCOMANDAZIONE" in out
    assert "tier=" in out


def test_cmd_audit_quality_apply_writes_toml(tmp_path, monkeypatch):
    """`--apply` salva config in path corretto (override `PATH_USER_CONFIG`).

    `runtime/config.py` deriva `PATH_USER_CONFIG` da `METNOS_USER_CONFIG`
    a import-time (non da `XDG_CONFIG_HOME`). Per testare overrida il
    valore gia' risolto sul modulo `_C` importato da `prompts_cli`.
    """
    monkeypatch.setattr(pc._C, "PATH_USER_CONFIG", tmp_path)
    args = _make_args(sample="2", apply=True, dry_run=True)
    rc = pc.cmd_audit_quality(args)
    assert rc == 0
    expected = tmp_path / "translator_tier.toml"
    assert expected.is_file()
    body = expected.read_text(encoding="utf-8")
    assert "[translator]" in body
    assert "tier =" in body
    assert "audit_date" in body
    assert "recommended_tier" in body
    # mode 0600 (best-effort, non sempre verificabile in tmp)
    mode = expected.stat().st_mode & 0o777
    assert mode in (0o600, 0o644)  # tolerant in tmpfs


def test_cmd_audit_quality_report_dumps_json(tmp_path, monkeypatch):
    """`--report` dumpa JSON in cwd."""
    monkeypatch.chdir(tmp_path)
    args = _make_args(sample="2", report=True, dry_run=True)
    rc = pc.cmd_audit_quality(args)
    assert rc == 0
    rep = tmp_path / "audit_quality_en.json"
    assert rep.is_file()
    import json
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert "decision" in data
    assert "per_prompt" in data
    assert data["decision"]["recommended_tier"] in ("fidelity", "frontier")


# ── _audit_apply_config ──────────────────────────────────────────────────


def test_apply_config_format(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    decision = {
        "recommended_tier": "fidelity",
        "mean_fidelity": 0.873,
        "mean_frontier": 0.911,
        "n_valid": 26,
        "n_individual_above": 22,
    }
    p = pc._audit_apply_config(decision, threshold_pct=0.80,
                                 individual_pct=0.95)
    body = p.read_text(encoding="utf-8")
    # Verifica TOML parsable
    import tomllib
    parsed = tomllib.loads(body)
    assert parsed["translator"]["tier"] == "fidelity"
    assert parsed["translator"]["audit_n_prompts"] == 26
    assert parsed["translator"]["audit_individual_pct"] == 0.95
