from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executor_standard import STANDARD_ID, validate_for_lifecycle  # noqa: E402
from naming_grammar import validate_name  # noqa: E402


def test_login_urls_is_canonical_and_not_a_session_alias() -> None:
    assert validate_name("login_urls").ok
    assert not validate_name("login_session").ok
    assert not (ROOT / "executors" / "login_session").exists()


def test_login_urls_manifest_has_closed_server_authority() -> None:
    manifest = tomllib.loads(
        (ROOT / "executors" / "login_urls" / "manifest.toml").read_text(
            encoding="utf-8",
        ),
    )
    assert manifest["executor_standard"] == STANDARD_ID
    assert validate_for_lifecycle(manifest) == []
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert {c["name"] for c in manifest["capabilities"]} == {
        "network:http", "auth.password_storage", "fs:read", "fs:write",
    }

