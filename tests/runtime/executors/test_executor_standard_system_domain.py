from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
for name in ("consult_frontier", "get_location", "get_now", "undo_last_turn"):
    path = ROOT / "executors" / name
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import consult_frontier  # noqa: E402
import get_location  # noqa: E402
import get_now  # noqa: E402
import undo_last_turn  # noqa: E402
from executor_standard import STANDARD_ID, validate_for_lifecycle  # noqa: E402
from naming_grammar import validate_name  # noqa: E402


SYSTEM_EXECUTORS = ("consult_frontier", "get_location", "get_now", "undo_last_turn")


def _manifest(name: str) -> dict:
    return tomllib.loads(
        (ROOT / "executors" / name / "manifest.toml").read_text(encoding="utf-8"),
    )


def test_closed_runtime_names_are_valid_but_do_not_open_prefixes() -> None:
    for name in SYSTEM_EXECUTORS:
        assert validate_name(name).ok, name
    for invented in (
        "consult_messages", "consult_local", "undo_files", "undo_other_turn",
        "get_locations", "get_nowish",
    ):
        assert not validate_name(invented).ok, invented


def test_all_runtime_domain_manifests_pass_standard() -> None:
    for name in SYSTEM_EXECUTORS:
        manifest = _manifest(name)
        assert manifest["executor_standard"] == STANDARD_ID
        assert validate_for_lifecycle(manifest) == [], name


def test_get_now_success_and_typed_invalid_zone() -> None:
    import config

    default = get_now.invoke({})
    ok = get_now.invoke({"timezone": "Europe/Rome"})
    bad = get_now.invoke({"timezone": "Mars/Olympus"})
    assert ok["ok"] is True
    assert {"now", "time", "date"} <= set(ok)
    assert default["metadata"]["timezone"] == config.DEFAULT_TIMEZONE
    assert bad["ok"] is False
    assert bad["error_class"] == "invalid_args"
    assert bad["error_code"] == "ERR_ARG_INVALID"
    manifest = _manifest("get_now")
    assert manifest["placement"] == {"scope": "server", "device_ok": False}


def test_get_location_success_not_found_and_invalid_actor(monkeypatch) -> None:
    monkeypatch.setenv("METNOS_OWNER_USER_ID", "owner-a")
    monkeypatch.setattr(get_location, "get_last_location", lambda *, owner_user_id: (
        {"lat": 45.0, "lon": 9.0, "ts": 1.0, "channel": "test"}
        if owner_user_id == "owner-a" else None
    ))
    assert get_location.invoke({"actor": "host"})["location"]["lat"] == 45.0
    monkeypatch.setenv("METNOS_OWNER_USER_ID", "owner-b")
    missing = get_location.invoke({"actor": "other"})
    invalid = get_location.invoke({"actor": 7})
    assert missing["error_class"] == "not_found"
    assert invalid["error_class"] == "invalid_args"


def test_consult_validation_never_contacts_provider() -> None:
    missing = consult_frontier.invoke({"output_spec": {"format": "markdown"}})
    invalid = consult_frontier.invoke({
        "role": "reviewer", "output_spec": {"format": "invalid"},
    })
    assert missing["ok"] is False
    assert missing["error_class"] == "invalid_args"
    assert invalid["ok"] is False
    assert invalid["error_class"] == "invalid_args"


def test_undo_empty_owned_log_is_idempotent(tmp_path: Path) -> None:
    out = undo_last_turn.invoke({
        "log_path": str(tmp_path / "undo.jsonl"), "_actor": "host",
    })
    assert out["ok"] is True
    assert out["undone_count"] == 0
    assert out["skipped_count"] == 0
