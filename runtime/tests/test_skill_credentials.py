"""Test skill_credentials: dormancy detection per imported executor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import skill_credentials as sc  # noqa: E402


def test_parse_skill_from_provenance_canonical():
    prov = {"imported_from": "agentskills.io/local/google-workspace"}
    assert sc.parse_skill_from_provenance(prov) == "google-workspace"


def test_parse_skill_from_provenance_missing():
    assert sc.parse_skill_from_provenance(None) is None
    assert sc.parse_skill_from_provenance({}) is None
    assert sc.parse_skill_from_provenance({"foo": "bar"}) is None


def test_parse_skill_from_provenance_short_path():
    assert sc.parse_skill_from_provenance({"imported_from": "abc"}) is None
    assert sc.parse_skill_from_provenance({"imported_from": "abc/def"}) is None


def test_is_credentials_available_unknown_skill_is_ok():
    ok, reason = sc.is_credentials_available("nonexistent-skill")
    assert ok is True
    assert reason == ""


def test_is_credentials_available_google_missing_token(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_SKILLS_ROOT", tmp_path)
    ok, reason = sc.is_credentials_available("google-workspace")
    assert ok is False
    assert "google_token.json missing" in reason


def test_is_credentials_available_google_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_SKILLS_ROOT", tmp_path)
    gw = tmp_path / "google-workspace"
    gw.mkdir()
    (gw / "google_token.json").write_text("not-valid-json{{")
    ok, reason = sc.is_credentials_available("google-workspace")
    assert ok is False
    assert "invalid" in reason


def test_is_credentials_available_google_missing_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_SKILLS_ROOT", tmp_path)
    gw = tmp_path / "google-workspace"
    gw.mkdir()
    (gw / "google_token.json").write_text(json.dumps({"token": "x"}))
    ok, reason = sc.is_credentials_available("google-workspace")
    assert ok is False
    assert "refresh_token" in reason


def test_is_credentials_available_google_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_SKILLS_ROOT", tmp_path)
    gw = tmp_path / "google-workspace"
    gw.mkdir()
    (gw / "google_token.json").write_text(
        json.dumps({"token": "x", "refresh_token": "y"})
    )
    ok, reason = sc.is_credentials_available("google-workspace")
    assert ok is True
    assert reason == ""


def test_compute_dormancy_no_provenance():
    dormant, _ = sc.compute_dormancy(None)
    assert dormant is False


def test_compute_dormancy_unknown_skill_not_dormant():
    dormant, _ = sc.compute_dormancy(
        {"imported_from": "registry/scope/unknown-skill"}
    )
    assert dormant is False


def test_compute_dormancy_google_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_SKILLS_ROOT", tmp_path)
    dormant, reason = sc.compute_dormancy(
        {"imported_from": "agentskills.io/local/google-workspace"}
    )
    assert dormant is True
    assert "google_token.json" in reason


def test_compute_dormancy_google_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_SKILLS_ROOT", tmp_path)
    gw = tmp_path / "google-workspace"
    gw.mkdir()
    (gw / "google_token.json").write_text(
        json.dumps({"token": "x", "refresh_token": "y"})
    )
    dormant, reason = sc.compute_dormancy(
        {"imported_from": "agentskills.io/local/google-workspace"}
    )
    assert dormant is False


def test_check_fail_open_on_exception(monkeypatch):
    """Se il check function raise, considera credentials presenti
    (graceful degrade, non castrare per errore)."""
    def _bad():
        raise RuntimeError("boom")
    monkeypatch.setitem(sc._CHECKS, "fragile-skill", _bad)
    ok, _ = sc.is_credentials_available("fragile-skill")
    assert ok is True


def test_prefilter_filter_dormant():
    """_filter_dormant skips executor with .dormant=True."""
    from prefilter import _filter_dormant

    class _Mock:
        def __init__(self, name, dormant=False):
            self.name = name
            self.dormant = dormant

    cat = [_Mock("a"), _Mock("b", dormant=True), _Mock("c")]
    out = _filter_dormant(cat)
    assert [e.name for e in out] == ["a", "c"]
