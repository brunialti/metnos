"""Exact conditional undo for safety signature curation."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


def _module(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SAFETY_DB_PATH", str(tmp_path / "safety.db"))
    import safety.storage as storage

    importlib.reload(storage)
    path = (Path(__file__).resolve().parents[3]
            / "executors" / "set_signatures" / "set_signatures.py")
    spec = importlib.util.spec_from_file_location("set_signatures_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, storage


def test_insert_and_update_round_trip_exactly(monkeypatch, tmp_path: Path) -> None:
    module, storage = _module(monkeypatch, tmp_path)
    signature = "tool:run:literal"

    created = module.invoke({
        "kind": "whitelist", "signature": signature, "reason": "first",
    })
    assert created["_undo"]["outcome"] == "reversible"
    assert module.reverse({}, created)["ok"] is True
    store = storage.SafetyStore()
    assert store.snapshot(signature) is None

    original = store.upsert_user(
        signature, "blacklist", severity="dangerous",
        reason="original", created_by="owner",
    )
    original_snapshot = store.snapshot(signature)
    store.close()
    assert original is not None

    changed = module.invoke({
        "kind": "whitelist", "signature": signature, "reason": "changed",
    })
    assert changed["_undo"]["outcome"] == "reversible"
    assert module.reverse({}, changed)["ok"] is True
    store = storage.SafetyStore()
    assert store.snapshot(signature) == original_snapshot
    store.close()


def test_reverse_refuses_concurrent_change(monkeypatch, tmp_path: Path) -> None:
    module, storage = _module(monkeypatch, tmp_path)
    signature = "tool:run:literal"
    changed = module.invoke({
        "kind": "whitelist", "signature": signature, "reason": "forward",
    })
    store = storage.SafetyStore()
    store.upsert_user(
        signature, "blacklist", severity="dangerous",
        reason="later", created_by="other",
    )
    later = store.snapshot(signature)
    store.close()

    result = module.reverse({}, changed)

    assert result["ok"] is False
    store = storage.SafetyStore()
    assert store.snapshot(signature) == later
    store.close()


def test_forbidden_transition_is_irreversible_and_immutable(
        monkeypatch, tmp_path: Path) -> None:
    module, storage = _module(monkeypatch, tmp_path)
    signature = "tool:run:literal"
    forbidden = module.invoke({
        "kind": "blacklist", "signature": signature,
        "severity": "forbidden", "reason": "law",
    })
    assert forbidden["_undo"] == {"outcome": "irreversible"}

    denied = module.invoke({
        "kind": "whitelist", "signature": signature, "reason": "change",
    })
    assert denied["ok"] is False
    assert denied["_undo"] == {"outcome": "no_effect"}
    store = storage.SafetyStore()
    assert store.snapshot(signature)["severity"] == "forbidden"
    store.close()
