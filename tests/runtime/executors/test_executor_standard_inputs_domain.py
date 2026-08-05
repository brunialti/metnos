"""Domain gate for the runtime-owned structured-input executor."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.get_inputs import get_inputs  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


MANIFEST = ROOT / "executors" / "get_inputs" / "manifest.toml"


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False,
                           is_synthesized=False)
    return value


def test_inputs_declares_server_owned_dialog_authority() -> None:
    manifest = _manifest()
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["platforms"] == ["linux"]
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["capabilities"] == [{
        "name": "dialog.user_input", "hint": []}]
    assert "schema_inline" in manifest["output"]
    properties = manifest["args"]["properties"]
    for name in ("actor", "channel", "entries"):
        assert properties[name]["runtime_resolved"] is True
    assert "dialog_id" in properties
    assert manifest["args"]["required"] == []


def test_invalid_root_and_public_types_fail_with_typed_envelopes() -> None:
    results = (
        get_inputs.invoke([]),
        get_inputs.invoke({"dialog_id": 7}),
        get_inputs.invoke({"title": "x", "dialog": [], "channel": []}),
        get_inputs.invoke({"title": "x", "dialog": [], "timeout_s": True}),
    )
    for result in results:
        assert result["ok"] is False
        assert result["error_class"]
        assert result["error_code"]
        assert result["error"]


def test_create_and_retrieve_dialog_preserves_runtime_identity(
        tmp_path: Path, monkeypatch) -> None:
    import dialog_pending

    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "dialogs")
    created = get_inputs.invoke({
        "title": "Scelta",
        "dialog": [{
            "var": "answer", "prompt": "Valore?",
            "schema": {"kind": "text"},
        }],
        "actor": "domain-user", "channel": "http",
    })
    pending = get_inputs.invoke({
        "dialog_id": created["dialog_id"],
        "actor": "domain-user", "channel": "http",
    })

    assert created["ok"] is True
    assert created["decision"] == "input_required"
    assert pending == {
        "ok": True,
        "decision": "input_required",
        "dialog_id": created["dialog_id"],
        "step_index": 0,
        "step_total": 1,
        "values": {},
    }


def test_missing_dialog_and_storage_failure_are_honest(
        tmp_path: Path, monkeypatch) -> None:
    import dialog_pending

    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "dialogs")
    missing = get_inputs.invoke({"dialog_id": "does-not-exist"})

    def fail(*_args):
        raise OSError("private-path-detail")

    monkeypatch.setattr(dialog_pending, "save_pending", fail)
    unavailable = get_inputs.invoke({
        "title": "Input",
        "dialog": [{
            "var": "value", "prompt": "Valore?",
            "schema": {"kind": "text"},
        }],
    })

    assert missing["error_class"] == "not_found"
    assert missing["error_code"] == "dialog_not_found"
    assert unavailable["error_class"] == "io_error"
    assert unavailable["error_code"] == "dialog_save_failed"
    assert "private-path-detail" not in unavailable["error"]


def test_inputs_paraphrases_remain_present_in_catalog_ranking() -> None:
    entries = list(_catalog().executors.values())
    for query in (
        "fammi compilare un modulo con due valori",
        "chiedimi username e password in un dialogo",
    ):
        names = [item.name for item in rank(query, entries, k=10, min_score=1)]
        assert "get_inputs" in names, (query, names)
