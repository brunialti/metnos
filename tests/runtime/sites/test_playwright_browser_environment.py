"""A protected service home must not prevent full Chromium from starting."""
from __future__ import annotations

import asyncio
import os
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config as C
from playwright_sidecar import server


@pytest.fixture
def browser_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(C, "PATH_USER_CONFIG", tmp_path / "config")
    monkeypatch.setattr(C, "PATH_USER_CACHE", tmp_path / "cache")
    monkeypatch.setenv("HOME", str(tmp_path / "protected-home"))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("CHROME_CONFIG_HOME", "/unwritable-config")
    return tmp_path


def test_child_environment_uses_private_app_roots_only(browser_roots):
    before = dict(os.environ)
    env = server._browser_environment()
    assert dict(os.environ) == before
    assert env["HOME"] == before["HOME"]
    assert env["DISPLAY"] == ":99"
    assert env["XDG_CONFIG_HOME"] == env["CHROME_CONFIG_HOME"] == str(
        browser_roots / "config/browser")
    assert env["XDG_CACHE_HOME"] == str(browser_roots / "cache/browser")
    assert not (browser_roots / "protected-home").exists()
    for root in ("config", "cache"):
        assert stat.S_IMODE((browser_roots / root / "browser").stat().st_mode) == 0o700
    assert server._browser_environment() == env


@pytest.mark.parametrize("mode,stealth", [("headless", True), ("side", False),
                                          ("side", True)])
def test_lazy_variants_receive_the_same_environment(browser_roots, monkeypatch,
                                                   mode, stealth):
    launch = AsyncMock(return_value=SimpleNamespace(is_connected=lambda: True))
    monkeypatch.setattr(server, "_playwright", SimpleNamespace(
        chromium=SimpleNamespace(launch=launch)))
    for name in ("_browser_stealth", "_browser_side", "_browser_side_stealth"):
        monkeypatch.setattr(server, name, None)
    monkeypatch.setattr(server, "_stealth_launch_lock", asyncio.Lock())
    assert asyncio.run(server._get_browser(mode, stealth)).is_connected()
    kwargs = launch.call_args.kwargs
    assert kwargs["env"] == server._browser_environment()
    assert kwargs["headless"] is (mode == "headless")


def test_non_linux_preserves_platform_environment(monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "win32")
    assert server._browser_environment() == dict(os.environ)


def test_invalid_config_root_is_not_ignored(browser_roots):
    C.PATH_USER_CONFIG.write_text("not a directory")
    with pytest.raises(NotADirectoryError):
        server._browser_environment()
