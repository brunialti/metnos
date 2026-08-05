"""Domain gate for the runtime-managed human approval executor."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.get_approval import get_approval  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from policy import CAPABILITY_REGISTRY, is_allowed  # noqa: E402
from prefilter import rank  # noqa: E402


def _manifest() -> dict:
    path = ROOT / "executors" / "get_approval" / "manifest.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False,
                           is_synthesized=False)
    return value


def test_approval_declares_server_owned_dialog_authority() -> None:
    manifest = _manifest()
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["platforms"] == ["linux"]
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["capabilities"] == [{
        "name": "dialog.user_input", "hint": []}]
    assert "schema_inline" in manifest["output"]

    capability = CAPABILITY_REGISTRY["dialog.user_input"]
    assert capability.target_kind == "none"
    assert capability.default_approval == "none"
    assert is_allowed("ReadOnly", capability.name) == "allowed"


def test_invalid_root_and_branches_fail_with_typed_envelopes() -> None:
    root = get_approval.invoke([])
    branch = get_approval.invoke({"prompt": "Approvi?", "on_approve": []})
    timeout = get_approval.invoke({
        "prompt": "Approvi?",
        "on_approve": {"tool": "final_answer", "args": {}},
        "timeout_s": True,
    })
    for result in (root, branch, timeout):
        assert result["ok"] is False
        assert result["error_class"] == "invalid_input"
        assert result["error_code"]
        assert result["error"]


def test_runtime_pass_through_never_creates_a_dialog(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("dialog_pending.save_pending",
                        lambda *_args: calls.append(True))
    branch = {"tool": "final_answer", "args": {}}

    resumed = get_approval.invoke({"_pre_approved": True})
    under_threshold = get_approval.invoke({
        "prompt": "Approvi?", "on_approve": branch,
        "guard_count": 5, "guard_threshold": 20,
    })

    assert resumed == {
        "ok": True, "decision": "approved", "final_message_hint": ""}
    assert under_threshold["decision"] == "approved"
    assert calls == []


def test_interactive_gate_persists_exact_branch_without_executing_it(
        tmp_path: Path, monkeypatch) -> None:
    import dialog_pending

    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "dialogs")
    monkeypatch.setenv("METNOS_ACTOR", "standard-user")
    monkeypatch.setenv("METNOS_CHANNEL", "http")
    monkeypatch.setenv("METNOS_OWNER_USER_ID", "pytest-runtime-owner")
    untouched = tmp_path / "must-not-exist"
    branch = {"tool": "write_files", "args": {"paths": [str(untouched)]}}

    result = get_approval.invoke({
        "prompt": "Approvi questa modifica?", "on_approve": branch,
    })
    state = dialog_pending.load_pending(
        "http:standard-user", result["dialog_id"],
        owner_user_id="pytest-runtime-owner")

    assert result["ok"] is True
    assert result["decision"] == "input_required"
    assert result["fmt"] == "form"
    assert state is not None
    assert state["on_complete"] == {
        "type": "gate_dispatch", "approve_value": "approve",
        "on_approve": branch,
        "owner_user_id": "pytest-runtime-owner",
    }
    assert not untouched.exists()


def test_dialog_storage_failure_is_honest(monkeypatch) -> None:
    def fail(*_args):
        raise OSError("unavailable")

    monkeypatch.setattr("dialog_pending.save_pending", fail)
    result = get_approval.invoke({
        "prompt": "Approvi?",
        "on_approve": {"tool": "final_answer", "args": {}},
    })
    assert result["ok"] is False
    assert result["error_class"] == "io_error"
    assert result["error_code"] == "dialog_save_failed"
    assert "unavailable" not in result["error"]


def test_approval_paraphrases_remain_present_in_catalog_ranking() -> None:
    entries = list(_catalog().executors.values())
    for query in (
        "chiedimi conferma prima di procedere",
        "domanda il mio consenso e poi continua",
    ):
        names = [item.name for item in rank(query, entries, k=10, min_score=1)]
        assert "get_approval" in names, (query, names)
