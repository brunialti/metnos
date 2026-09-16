"""Driver isolation and readiness, without claiming a Metnos routing cycle."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from driver import server as driver


@pytest.mark.parametrize("payload,status", [
    ({"ok": True, "operational": True, "maintenance_only": False}, 200),
    ({"ok": True, "operational": False, "maintenance_only": True}, 200),
    ({"ok": True}, 200),
    ({"ok": True, "operational": 1, "maintenance_only": 0}, 200),
    ({"ok": False, "operational": True, "maintenance_only": False}, 200),
    ([], 200),
    ("invalid-json", 200),
    ("x" * 65537, 200),
    ({"ok": True, "operational": True, "maintenance_only": False}, 503),
    ({"ok": True, "operational": True, "maintenance_only": False}, 302),
], ids=["operational", "maintenance", "missing", "wrong-types", "not-ok",
        "array", "malformed", "oversized", "unavailable", "redirect"])
def test_readiness_requires_operational_http(payload, status):
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/agent/health"
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status == 302:
                self.send_header("Location", "/agent/health")
            self.end_headers()
            body = payload if isinstance(payload, str) else json.dumps(payload)
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Health) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        try:
            result = driver._wait_ready(
                "127.0.0.1", server.server_port, timeout_s=.08,
                process=SimpleNamespace(poll=lambda: None),
            )
            assert result is (status == 200 and isinstance(payload, dict)
                              and payload.get("ok") is True
                              and payload.get("operational") is True
                              and payload.get("maintenance_only") is False)
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_dead_child_is_not_ready():
    assert not driver._wait_ready(
        "127.0.0.1", 1, timeout_s=10, process=SimpleNamespace(poll=lambda: 1),
    )


def test_readiness_waits_for_operational_transition():
    observations = []

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            observations.append(self.path)
            ready = len(observations) > 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True, "operational": ready, "maintenance_only": not ready,
            }).encode())

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Health) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        try:
            assert driver._wait_ready(
                "127.0.0.1", server.server_port, timeout_s=2,
                process=SimpleNamespace(poll=lambda: None),
            )
            assert observations == ["/agent/health", "/agent/health"]
        finally:
            server.shutdown()
            thread.join(timeout=2)


@pytest.fixture
def driver_root(tmp_path, monkeypatch):
    seed = driver._REPO_ROOT / "install/data/i18n_seed.sqlite"
    target = tmp_path / "install/data/i18n_seed.sqlite"
    target.parent.mkdir(parents=True)
    target.write_bytes(seed.read_bytes())
    monkeypatch.setattr(driver, "_REPO_ROOT", tmp_path)
    monkeypatch.delenv("METNOS_E2E_KEEP_TMP", raising=False)
    for name in ("_LIVE_USER_DATA", "_LIVE_USER_STATE", "_LIVE_USER_CONFIG"):
        monkeypatch.setattr(driver, name, tmp_path / "must-not-read-live")
    return tmp_path


def test_default_seed_is_shipped_and_environment_is_fresh(driver_root, monkeypatch):
    monkeypatch.setenv("METNOS_WORKSPACE", "/must-not-use-live-workspace")
    monkeypatch.setenv("METNOS_EXECUTOR_STATS_DB", "/must-not-use-live-db")
    monkeypatch.setenv("METNOS_USER_DATA", "/must-not-use-live-data")
    real_popen = subprocess.Popen

    def spawn_owned_child(*args, **kwargs):
        return real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(driver.subprocess, "Popen", spawn_owned_child)
    monkeypatch.setattr(driver, "_wait_ready", lambda *args, **kwargs: True)
    server = driver.E2EServer.spawn()
    try:
        assert (server.user_data / "i18n.sqlite").read_bytes() == (
            driver_root / "install/data/i18n_seed.sqlite").read_bytes()
        assert not (server.user_config / "keys").exists()
        assert server.runtime_env["METNOS_WORKSPACE"] == str(server.tmp_root / "workspace")
        assert "METNOS_EXECUTOR_STATS_DB" not in server.runtime_env
        assert server.runtime_env["METNOS_USER_DATA"] == str(server.user_data)
        assert server.runtime_env["METNOS_LOADER_VERIFY"] == "1"
    finally:
        server.shutdown()
    assert server.process.poll() is not None
    assert not server.tmp_root.exists()


def test_failed_start_reaps_process_and_removes_fixture(driver_root, monkeypatch):
    children = []
    real_popen = subprocess.Popen

    def spawn_owned_child(*args, **kwargs):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(driver.subprocess, "Popen", spawn_owned_child)
    monkeypatch.setattr(driver, "_wait_ready", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="operational"):
        driver.E2EServer.spawn(ready_timeout_s=.1)
    assert len(children) == 1 and children[0].poll() is not None
    assert not list((driver_root / "tests/e2e/tmp").iterdir())


def test_failed_hook_cleans_fixture_before_any_process(driver_root):
    def broken_hook(env, root):
        raise ValueError("fixture provisioning failed")

    with pytest.raises(ValueError, match="provisioning failed"):
        driver.E2EServer.spawn(pre_spawn_hook=broken_hook)
    assert not list((driver_root / "tests/e2e/tmp").iterdir())


def test_realistic_seeding_is_refused_as_root_before_any_io(driver_root, monkeypatch):
    monkeypatch.setattr(driver.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(driver, "_seed_realistic_into", lambda *args: pytest.fail("seed reached"))
    with pytest.raises(RuntimeError, match="unprivileged"):
        driver.E2EServer.spawn(seed_realistic=True)


@pytest.mark.skipif(sys.platform != "linux", reason="observes real Linux process-group lifetime")
def test_shutdown_removes_child_even_when_parent_exits_first():
    child_code = (
        "import os, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print(os.getpid(), flush=True); time.sleep(30)"
    )
    parent_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]]); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", parent_code, child_code],
        stdout=subprocess.PIPE, text=True, start_new_session=True,
    )
    child_pid = None
    try:
        import select
        assert select.select([process.stdout], [], [], 3)[0], "child did not become ready"
        child_pid = int(process.stdout.readline())
        driver._stop_process(process, timeout_s=.2)
        assert process.poll() is not None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text().split(") ", 1)[1][0]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            time.sleep(.01)
        else:
            pytest.fail("owned descendant survived shutdown")
    finally:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
        process.stdout.close()
