"""Public-install and supervision contract for the Playwright sidecar."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "runtime"))


def test_public_playwright_files_do_not_reference_suprastructure():
    paths = [
        ROOT / "systemd" / "metnos-playwright.service",
        ROOT / "install" / "units" / "metnos-playwright.service.tmpl",
        ROOT / "install" / "playwright_sidecar.py",
        ROOT / "runtime" / "playwright_sidecar" / "install.sh",
        ROOT / "runtime" / "playwright_sidecar" / "README.md",
    ]
    for path in paths:
        assert "/opt/suprastructure" not in path.read_text(), path


def test_runtime_venv_belongs_to_installation_not_user_data():
    bootstrap = (ROOT / "install" / "bootstrap.sh").read_text()
    phase5 = (ROOT / "install" / "phases" / "phase5_systemd.py").read_text()
    assert 'METNOS_VENV="${METNOS_VENV:-$REPO_DIR/.venv}"' in bootstrap
    assert "$METNOS_USER_DATA/.venv" not in bootstrap
    assert 'return _repo_dir() / ".venv"' in phase5


def test_installer_uses_metnos_owned_venv_and_browser_directory(tmp_path):
    from install import playwright_sidecar as installer

    data = tmp_path / "metnos-data"
    root = tmp_path / "metnos-install"
    with mock.patch.dict(os.environ, {
        "METNOS_INSTALL_ROOT": str(root),
        "METNOS_USER_DATA": str(data),
    }, clear=False):
        os.environ.pop("METNOS_VENV", None)
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        assert installer._venv_python() == str(root / ".venv" / "bin" / "python")
        assert installer._browsers_base() == data / "playwright-browsers"


def test_unit_has_process_and_event_loop_recovery():
    unit = (ROOT / "install" / "units" /
            "metnos-playwright.service.tmpl").read_text()
    assert "Type=notify" in unit
    assert "WatchdogSec=" in unit
    assert "Restart=always" in unit
    assert "PLAYWRIGHT_BROWSERS_PATH=@BROWSERS_DIR@" in unit


def test_playwright_version_is_pinned_consistently():
    expected = "playwright==1.61.0"
    assert expected in (ROOT / "requirements-optional.txt").read_text()
    assert expected in (ROOT / "runtime" / "playwright_sidecar" /
                        "requirements.txt").read_text()
    assert expected in (ROOT / "runtime" / "playwright_sidecar" /
                        "install.sh").read_text()
    assert expected in (ROOT / "install" / "playwright_sidecar.py").read_text()


def test_first_party_workspace_dependencies_are_in_runtime_environment():
    requirements = (ROOT / "requirements.txt").read_text()
    optional = (ROOT / "requirements-optional.txt").read_text()
    assert "google-api-python-client==2.196.0" in requirements
    assert "google-auth-oauthlib==1.4.0" in requirements
    assert "google-api-python-client" not in optional


def test_health_rejects_disconnected_browser():
    from playwright_sidecar import server

    class Browser:
        def is_connected(self):
            return False

    old = server._browser
    try:
        server._browser = Browser()
        assert server._browser_connected() is False
    finally:
        server._browser = old


def test_broker_exception_is_always_a_structured_json_failure():
    import asyncio
    import json
    from playwright_sidecar import server

    class Browser:
        def is_connected(self):
            return True

    class Request:
        headers = {}

        async def json(self):
            return {}

    async def broken(_broker, _body):
        raise RuntimeError("page supplied secret must stay in local logs")

    old = server._browser
    try:
        server._browser = Browser()
        response = asyncio.run(server._broker_call(Request(), broken))
        payload = json.loads(response.body)
        assert response.status == 500
        assert payload == {
            "ok": False,
            "error": "session broker internal failure",
            "error_class": "sidecar_internal",
        }
        assert "secret" not in response.text
    finally:
        server._browser = old


def test_login_timeout_layers_are_strictly_ordered():
    from playwright_sidecar import server, session_broker, session_client

    assert session_broker._LOGIN_TIMEOUT_S < server._BROKER_LOGIN_TIMEOUT_S
    assert server._BROKER_LOGIN_TIMEOUT_S < session_client.LOGIN_TIMEOUT_S
    assert server._BROKER_REQUEST_TIMEOUT_S < server._BROKER_LOGIN_TIMEOUT_S
