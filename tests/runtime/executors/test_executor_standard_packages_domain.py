"""Domain gate for executable discovery on the server or a paired device."""
from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

from executors.find_packages import find_packages  # noqa: E402


MANIFEST = ROOT / "executors" / "find_packages" / "manifest.toml"


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def test_packages_declares_read_only_executable_discovery_authority() -> None:
    manifest = _manifest()
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["platforms"] == ["linux", "windows"]
    assert manifest["placement"] == {
        "scope": "any", "device_ok": True,
        "min_sandbox": "appcontainer",
    }
    assert manifest["capabilities"] == [{
        "name": "system:read", "hint": ["executables"]}]
    assert "schema_inline" in manifest["output"]


def test_found_and_absent_commands_are_distinct_valid_results() -> None:
    found = find_packages.invoke({"package_name": "python3"})
    absent = find_packages.invoke({
        "package_name": "metnos-command-that-does-not-exist-7f09"})

    assert found["ok"] is True
    assert found["found"] is True
    assert found["entries"][0]["path"]
    assert absent == {
        "ok": True,
        "ok_count": 0,
        "fail_count": 0,
        "entries": [],
        "failed": [],
        "found": False,
    }


def test_invalid_names_and_lookup_failure_are_typed(monkeypatch) -> None:
    roots = (
        find_packages.invoke([]),
        find_packages.invoke({"package_name": "/usr/bin/python3"}),
        find_packages.invoke({"package_name": ""}),
    )
    for result in roots:
        assert result["ok"] is False
        assert result["error_class"] == "invalid_input"
        assert result["error_code"]

    def fail(_name):
        raise OSError("private PATH detail")

    monkeypatch.setattr(find_packages.shutil, "which", fail)
    unavailable = find_packages.invoke({"package_name": "python3"})
    assert unavailable["error_class"] == "resource_unavailable"
    assert unavailable["error_code"] == "executable_lookup_failed"
    assert "private PATH detail" not in unavailable["error"]
