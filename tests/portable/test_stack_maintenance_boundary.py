"""Exercise maintenance decisions without host services, data or dependencies."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

if sys.platform == "win32":
    pytest.skip("systemd reconciliation is POSIX-only", allow_module_level=True)


@pytest.fixture
def reconciler(monkeypatch, tmp_path):
    # The runtime implementation is real; external configuration and service
    # observations are substitutes, so collection never reads instance data.
    for name in ("config", "services_registry", "service_health_monitor"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    services = sys.modules["services_registry"]
    services.readiness_catalog = lambda: ()
    services.desired_state = lambda key: "running"
    services.key_for_unit = lambda unit: None
    sys.modules["service_health_monitor"].run = lambda: {"ok": True}
    path = Path(__file__).resolve().parents[2] / "runtime/stack_reconcile.py"
    spec = importlib.util.spec_from_file_location("_maintenance_under_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_admin_key", lambda path: "synthetic-key")

    def forbidden(*args, **kwargs):
        pytest.fail("maintenance may neither run a command nor restart the stack")

    rec = module.StackReconciler(report_path=tmp_path / "readiness.json",
                                catalog_names_provider=forbidden)
    monkeypatch.setattr(rec, "restart", forbidden)
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    return module, rec, services


def test_maintenance_is_never_readiness_and_restarts_only_http_once(
        reconciler, monkeypatch, tmp_path):
    module, rec, services = reconciler
    monkeypatch.setattr(sys.modules["config"], "PATH_USER_STATE",
                        tmp_path / "state", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    services.stack_scope = lambda: "user"
    services.service_user = lambda: "metnos"
    http_target = SimpleNamespace(unit="metnos-http.service", scope="system")
    services.get = lambda key: (
        SimpleNamespace(targets=(http_target,)) if key == "http" else None)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    class Units:
        """A configuration fault: every fresh start is maintenance-only."""
        def __init__(self):
            self.invocation, self.runs = 1, []

        def show(self, unit, scope="user"):
            assert (scope, unit) == ("system", "metnos-http.service")
            return {"ActiveState": "active",
                    "InvocationID": f"boot-{self.invocation}"}

        def run(self, scope, *args, timeout_s=120):
            self.runs.append((scope, *args))
            self.invocation += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    rec.systemctl = units = Units()

    def observe(url, *, admin_key=None):
        if url.endswith("/agent/health"):
            return {"ok": True, "operational": False, "maintenance_only": True}
        assert url.endswith("/agent/stack/health")
        assert admin_key == "synthetic-key"
        return {"quiescent": True,
                "durable_workloads": {"known": True, "active_attempts": 0},
                "http": {"operational": False, "active_turns": 0,
                         "startup_failure": "birth_runtime_unavailable",
                         "startup_reason": "trusted_keys_missing"}}

    monkeypatch.setattr(module, "_json_request", observe)
    outcomes = []
    for _ in range(3):
        with pytest.raises(module.StackFailure) as caught:
            rec.watchdog()
        assert caught.value.code == "runtime_maintenance"
        outcomes.append(caught.value.details["automatic_restart"])
        report = json.loads(rec.report_path.read_text())
        assert report["ok"] is False and report["ready"] is False
    assert outcomes == ["failed", "already_attempted", "already_attempted"]
    # Only the HTTP unit, never the stack target or its dependencies.
    assert units.runs == [(
        "system", "restart", "--job-mode=ignore-dependencies",
        "metnos-http.service",
    )]


def test_invalid_service_catalog_cannot_authorize_automatic_restarts(
        reconciler, monkeypatch):
    module, rec, services = reconciler

    def invalid_catalog():
        raise ValueError("unverified service catalog")

    monkeypatch.setattr(services, "readiness_catalog", invalid_catalog)
    with pytest.raises(module.StackFailure) as caught:
        rec.watchdog()
    assert caught.value.code == "service_catalog_unavailable"
    assert not rec.report_path.exists()
