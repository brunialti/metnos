"""Closed native adapter boundary. Native activation also requires a real E2E."""
import base64
import json
import subprocess
from types import SimpleNamespace

import pytest
import windows_desktop_apps as desktop

PACKAGE = "desktop:" + "a" * 64


@pytest.mark.parametrize("name", ["Éditeur", "$(command); 'quote'", "日本語"])
def test_names_remain_stdin_data_in_a_constant_script(monkeypatch, name):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"entries":[]}')

    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    monkeypatch.setattr(desktop.subprocess, "run", run)
    assert desktop.find(name) == {"ok": True, "entries": []}
    argv, options = calls[0]
    assert base64.b64decode(argv[-1]).decode("utf-16-le") == desktop._SCRIPT
    assert len(" ".join(argv)) < 30000
    assert json.loads(options["input"]) == {"operation": "find", "name": name}
    assert options["shell"] is False and options["timeout"] == 15


@pytest.mark.parametrize("entries", [None, [{}], [{"name": "App", "resolved_id": "cmd.exe"}],
                                    [{"name": "App", "resolved_id": PACKAGE}] * 65])
def test_malformed_inventory_never_becomes_an_installed_identity(monkeypatch, entries):
    monkeypatch.setattr(desktop, "_call", lambda _: {"ok": True, "entries": entries})
    assert desktop.find("App")["error_code"] == "package_inventory_invalid"


@pytest.mark.parametrize(("package", "operation", "args"), [
    ("desktop:cmd.exe", "query", ()),
    (PACKAGE, "start", ("--lifetime", "persistent")),
    (PACKAGE, "query", ("--command", "cmd.exe")),
    (PACKAGE, "stop", ("--pid", "1")),
    (PACKAGE, "stop", ("--pid", "1", "--creation-time", "20", "--activation-boundary", "21")),
    (PACKAGE, "stop", ("--pid", "1", "--creation-time", "20", "--activation-boundary", "19",
                       "--preexisting-process", "1:20")),
    (PACKAGE, "stop", ("--pid", "1", "--creation-time", "20", "--activation-boundary", "19",
                       "--path", "anything")),
])
def test_untyped_launch_or_unsafe_stop_is_rejected_before_native_call(monkeypatch, package, operation, args):
    monkeypatch.setattr(desktop, "_call", lambda _: pytest.fail("must not execute"))
    assert desktop.call(package, operation, *args)["ok"] is False


def test_stop_carries_only_the_owned_receipt(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_call", lambda request: calls.append(request) or {"ok": True})
    assert desktop.call(PACKAGE, "stop", "--pid", "1", "--creation-time", "20",
                        "--activation-boundary", "19", "--preexisting-process", "2:18")["ok"]
    assert calls == [{"operation": "stop", "package_id": PACKAGE, "pid": 1,
                      "creation_time": 20, "activation_boundary": 19,
                      "preexisting_processes": [{"pid": 2, "creation_time": 18}]}]


@pytest.mark.parametrize("operation", ["start", "query"])
def test_timeout_does_not_claim_that_a_start_had_no_effect(monkeypatch, operation):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("powershell", 15)
    monkeypatch.setattr(desktop.subprocess, "run", timeout)
    result = desktop._call({"operation": operation, "package_id": PACKAGE})
    assert result["ok"] is False
    assert result["effects_attempted"] is (operation == "start")
