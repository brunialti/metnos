"""Exercise maintenance decisions without host services, data or dependencies."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

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


def test_authenticated_maintenance_is_not_readiness_or_restart_authority(
        reconciler, monkeypatch):
    module, rec, _ = reconciler
    observations = []

    def observe(url, *, admin_key=None):
        observations.append(url)
        if url.endswith("/agent/health"):
            return {"ok": True}
        assert url.endswith("/agent/stack/health")
        assert admin_key == "synthetic-key"
        return {"http": {"operational": False}}

    monkeypatch.setattr(module, "_json_request", observe)
    for _ in range(2):
        with pytest.raises(module.StackFailure) as caught:
            rec.watchdog()
        assert caught.value.code == "runtime_maintenance"
        assert caught.value.details["failed_checks"] == ["http_runtime"]
        report = json.loads(rec.report_path.read_text())
        assert report["ok"] is False and report["ready"] is False
    assert len(observations) == 4


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
