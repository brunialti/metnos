"""Domain gate for read-only introvertive proposal review."""
from __future__ import annotations

import json
import sqlite3
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.get_proposals import get_proposals  # noqa: E402
import sandbox  # noqa: E402


MANIFEST = ROOT / "executors" / "get_proposals" / "manifest.toml"


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def _seed_state(path: Path, dormant_keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE proposals_state (sig_key TEXT PRIMARY KEY, state TEXT)"
        )
        conn.executemany(
            "INSERT INTO proposals_state(sig_key,state) VALUES (?, 'dormant')",
            [(key,) for key in dormant_keys],
        )
        conn.commit()
    finally:
        conn.close()


def test_proposals_declares_exact_read_only_server_resource() -> None:
    manifest = _manifest()
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["revertible"] is False
    assert manifest["platforms"] == ["linux"]
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["capabilities"] == [{
        "name": "metnos:read",
        "hint": ["introvertiva_proposals:local"],
    }]
    assert "schema_inline" in manifest["output"]


def test_missing_storage_is_empty_and_does_not_create_state(
        tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "missing-audit"
    state = tmp_path / "missing-state" / "proposals_state.db"
    monkeypatch.setattr(get_proposals, "AUDIT_DIR", audit)
    monkeypatch.setattr(get_proposals, "STATE_DB", state)

    result = get_proposals.invoke({"kind": "all"})

    assert result["ok"] is True
    assert result["entries"] == []
    assert result["fail_count"] == 0
    assert not audit.exists()
    assert not state.exists()


def test_dormant_filter_is_read_only_and_explicitly_bypassable(
        tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "introvertiva"
    audit.mkdir()
    payload = {
        "kind": "duplicate", "src_executor": "read_files",
        "dst_executor": "read_files_doc", "uses": 4,
    }
    audit_file = audit / "candidates_dedupe_2000000000.jsonl"
    audit_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    key = get_proposals._state_key(
        get_proposals._canonical_key("dedupe", payload))
    state = tmp_path / "state" / "proposals_state.db"
    _seed_state(state, [key])
    before = (audit_file.read_bytes(), state.read_bytes())
    monkeypatch.setattr(get_proposals, "AUDIT_DIR", audit)
    monkeypatch.setattr(get_proposals, "STATE_DB", state)

    hidden = get_proposals.invoke({
        "since_iso": "2026-01-01T00:00:00Z"})
    visible = get_proposals.invoke({
        "since_iso": "2026-01-01T00:00:00Z", "include_dormant": True})

    assert hidden["ok"] is True
    assert hidden["entries"] == []
    assert hidden["filtered_dormant"] == 1
    assert visible["ok"] is True
    assert len(visible["entries"]) == 1
    assert (audit_file.read_bytes(), state.read_bytes()) == before


def test_malformed_audit_record_is_visible_as_partial_coverage(
        tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "introvertiva"
    audit.mkdir()
    valid = {
        "pattern": ["find_files", "read_files"], "uses": 3,
        "distinct_intents": 2, "score": 0.8,
    }
    (audit / "candidates_generalize_2000000001.jsonl").write_text(
        json.dumps(valid) + "\n{not-json}\n", encoding="utf-8")
    monkeypatch.setattr(get_proposals, "AUDIT_DIR", audit)
    monkeypatch.setattr(
        get_proposals, "STATE_DB", tmp_path / "state" / "missing.db")

    result = get_proposals.invoke({
        "kind": "generalize", "since_iso": "2026-01-01T00:00:00Z"})

    assert result["ok"] is True
    assert result["partial"] is True
    assert result["ok_count"] == 1
    assert result["fail_count"] == 1
    assert result["failed"][0]["error_code"] == \
        "proposal_audit_record_invalid"


def test_corrupt_state_and_invalid_arguments_fail_loudly(
        tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "introvertiva"
    audit.mkdir()
    payload = {"executor": "read_files", "arg_name": "mode"}
    (audit / "candidates_specialize_2000000002.jsonl").write_text(
        json.dumps(payload) + "\n", encoding="utf-8")
    state = tmp_path / "state" / "proposals_state.db"
    state.parent.mkdir()
    state.write_bytes(b"not a sqlite database")
    monkeypatch.setattr(get_proposals, "AUDIT_DIR", audit)
    monkeypatch.setattr(get_proposals, "STATE_DB", state)

    corrupt = get_proposals.invoke({
        "since_iso": "2026-01-01T00:00:00Z"})
    invalid = (
        get_proposals.invoke([]),
        get_proposals.invoke({"max_results": True}),
        get_proposals.invoke({"since_iso": "not-a-date"}),
        get_proposals.invoke({"include_dormant": "yes"}),
    )

    assert corrupt["error_class"] == "resource_unavailable"
    assert corrupt["error_code"] == "proposal_state_unavailable"
    assert "sqlite" not in corrupt["error"].lower()
    for result in invalid:
        assert result["ok"] is False
        assert result["error_class"] == "invalid_input"
        assert result["error_code"]


def test_semantic_resource_mounts_only_audit_and_state(
        tmp_path: Path, monkeypatch) -> None:
    import config

    data = tmp_path / "data"
    state_root = tmp_path / "state"
    audit = data / "introvertiva"
    audit.mkdir(parents=True)
    state_root.mkdir()
    state = state_root / "proposals_state.db"
    state.write_bytes(b"x")
    unrelated = data / "credentials.enc"
    unrelated.write_bytes(b"secret")
    monkeypatch.setattr(config, "PATH_AUDIT", audit)
    monkeypatch.setattr(config, "PATH_USER_STATE", state_root)

    paths = sandbox._managed_local_resource_paths(
        ["introvertiva_proposals:local"], writable=False)

    assert audit in paths
    assert state in paths
    assert unrelated not in paths


def test_proposal_summary_and_detail_follow_runtime_language(monkeypatch) -> None:
    import i18n

    seed = ROOT / "install" / "data" / "i18n_seed.sqlite"
    conn = sqlite3.connect(f"file:{seed}?mode=ro&immutable=1", uri=True)
    monkeypatch.setattr(i18n, "_conn", conn)

    entry = {
        "kind": "dedupe",
        "payload": {
            "src_executor": "read_files",
            "dst_executor": "read_files_doc",
            "uses": 4,
        },
    }

    monkeypatch.setenv("METNOS_LANG", "en")
    monkeypatch.setattr(i18n, "_lang_cache", None)
    english = get_proposals._render_detail([entry], "dedupe", max_lines=20)
    assert "Consolidate duplicate" in english
    assert "Cosa propongo" not in english

    monkeypatch.setenv("METNOS_LANG", "it")
    monkeypatch.setattr(i18n, "_lang_cache", None)
    italian = get_proposals._render_detail([entry], "dedupe", max_lines=20)
    assert "Consolida doppione" in italian
    assert "Proposal:" not in italian
    conn.close()
