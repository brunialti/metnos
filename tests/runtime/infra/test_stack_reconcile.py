"""Transactional contract for metnos.target reconciliation."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import stack_reconcile as sr


@pytest.fixture(autouse=True)
def _legacy_readiness_catalog(monkeypatch):
    import services_registry

    monkeypatch.setattr(
        services_registry, "readiness_catalog", lambda: services_registry.SERVICES,
        raising=False,
    )


def test_catalog_lifecycle_guard_acquires_catalog_before_reconcile(
    monkeypatch,
) -> None:
    import contract_store

    events: list[str] = []

    @contextlib.contextmanager
    def catalog_lock(*, timeout):
        assert timeout == 0.25
        events.append("catalog_enter")
        try:
            yield
        finally:
            events.append("catalog_exit")

    class LifecycleLock:
        def acquire(self, *, wait_s):
            assert wait_s == 0.25
            assert events == ["catalog_enter"]
            events.append("lifecycle_enter")

        def release(self):
            events.append("lifecycle_exit")

    monkeypatch.setattr(contract_store, "catalog_admission_lock", catalog_lock)
    with sr.catalog_reconcile_lock(lock=LifecycleLock(), wait_s=0.25):
        events.append("body")

    assert events == [
        "catalog_enter", "lifecycle_enter", "body",
        "lifecycle_exit", "catalog_exit",
    ]


class FakeSystemctl:
    def __init__(self, *, target_loaded: bool = True,
                 playwright_loaded: bool = True,
                 system_http_active: bool = False):
        self.target_loaded = target_loaded
        self.playwright_loaded = playwright_loaded
        self.system_http_active = system_http_active
        self.calls: list[tuple] = []

    def show(self, unit: str, scope: str = "user"):
        self.calls.append(("show", scope, unit))
        if scope == "system" and unit == "metnos-http.service":
            return {
                "LoadState": "loaded",
                "ActiveState": "active" if self.system_http_active else "inactive",
            }
        loaded = True
        if unit == sr.TARGET_UNIT:
            loaded = self.target_loaded
        if unit == "metnos-playwright.service":
            loaded = self.playwright_loaded
        return {
            "LoadState": "loaded" if loaded else "not-found",
            "ActiveState": "active" if loaded else "inactive",
        }

    def run(self, scope: str, *args: str, timeout_s: float = 120):
        self.calls.append(("run", scope, *args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _composite(*, names=None, quiescent=True, sidecar_ok=True):
    return {
        "ok": True,
        "quiescent": quiescent,
        "http": {"contract_aligned": True, "active_turns": 0 if quiescent else 1},
        "sidecar": {
            "available": True,
            "ok": sidecar_ok,
            "contract_aligned": sidecar_ok,
            "active_sessions": 0 if quiescent else 1,
            "approval_pending_sessions": 0,
            "factor_pending_sessions": 0,
            "pending_opens": 0,
        },
        "catalog": {"names": names or ["delete_files", "read_sites"]},
    }


def _signed_authoring_executor(directory: Path, name: str) -> None:
    directory.mkdir(parents=True)
    (directory / "manifest.toml").write_text(
        f'name = "{name}"\n'
        '[code]\nfiles = ["main.py"]\n'
        f'digest = "sha256:{"0" * 64}"\n',
        encoding="utf-8",
    )
    (directory / "manifest.lang_state.json").write_text(
        '{"version":1}\n', encoding="utf-8",
    )
    (directory / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (directory / "manifest.toml.sig").write_bytes(b"previous-signature")


def _wire(monkeypatch, composite):
    def request(url, **_kwargs):
        if url.endswith("/agent/health"):
            return {"ok": True}
        if url.endswith("/agent/stack/health"):
            return composite
        raise AssertionError(url)

    monkeypatch.setattr(sr, "_json_request", request)
    monkeypatch.setattr(sr, "_admin_key", lambda *_args: "test-key")
    monkeypatch.setattr(sr, "_catalog_names", lambda: {"delete_files", "read_sites"})
    monkeypatch.setattr(
        sr, "_watched_service_snapshot",
        lambda key, **_kwargs: {
            "key": key, "installed": True, "load_state": "loaded",
            "active_state": "active", "healthy": True,
            "process_stopped": False, "observation_error": "",
        },
    )


def test_check_requires_catalog_contract_and_quiescence(monkeypatch, tmp_path):
    _wire(monkeypatch, _composite())
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(), report_path=tmp_path / "report.json",
    )
    report = rec.check(require_quiescent=True)
    assert report["ok"] is True
    persisted = json.loads((tmp_path / "report.json").read_text())
    assert persisted["ready"] is True
    assert {row["name"] for row in persisted["checks"]} == {
        "http_health", "http_contract", "catalog_parity",
        "sidecar_contract", "managed_components", "quiescent",
        "service_health:searxng", "service_health:llm",
        "service_health:durable_workloads",
    }


def test_check_accepts_an_identity_scoped_catalog_provider(monkeypatch, tmp_path):
    _wire(monkeypatch, _composite())
    monkeypatch.setattr(
        sr, "_catalog_names",
        lambda: (_ for _ in ()).throw(AssertionError("global loader used")),
    )
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(),
        report_path=tmp_path / "report.json",
        catalog_names_provider=lambda: {"delete_files", "read_sites"},
    )
    assert rec.check()["ok"] is True


def test_watchdog_keeps_authenticated_maintenance_available(monkeypatch, tmp_path):
    import service_health_monitor

    composite = _composite(names=[])
    composite["http"].update(operational=False, startup_failure="birth")
    composite["catalog"]["names"] = []
    _wire(monkeypatch, composite)
    monkeypatch.setattr(service_health_monitor, "run", lambda: {"ok": True})

    def unavailable(*args, **kwargs):
        pytest.fail("maintenance must not load or restart the broken runtime")

    monkeypatch.setattr(sr, "CircuitBreaker", unavailable)
    systemctl = FakeSystemctl()
    report_path = tmp_path / "maintenance.json"
    rec = sr.StackReconciler(systemctl=systemctl, report_path=report_path,
                            catalog_names_provider=unavailable)
    monkeypatch.setattr(rec, "restart", unavailable)
    for _ in range(2):
        with pytest.raises(sr.StackFailure) as failure:
            rec.watchdog()
        assert failure.value.code == "runtime_maintenance"
        report = json.loads(report_path.read_text())
        assert report["ready"] is False and report["ok"] is False
        assert failure.value.details["failed_checks"] == ["http_runtime"]
    assert not systemctl.calls


def test_check_rejects_functionally_unhealthy_searx(monkeypatch, tmp_path):
    _wire(monkeypatch, _composite())

    def snapshot(key, **_kwargs):
        return {
            "key": key, "installed": True, "load_state": "loaded",
            "active_state": "active", "healthy": key != "searxng",
            "process_stopped": False, "observation_error": "",
        }

    monkeypatch.setattr(sr, "_watched_service_snapshot", snapshot)
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(), report_path=tmp_path / "report.json",
    )

    with pytest.raises(sr.StackFailure) as caught:
        rec.check()

    assert caught.value.details["failed_checks"] == [
        "service_health:searxng",
    ]


def _watchdog_test_guards(monkeypatch, tmp_path):
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(
        sr, "ReconcileLock", lambda: lock_type(tmp_path / "watchdog.lock"))
    monkeypatch.setattr(
        sr, "CircuitBreaker",
        lambda: type("Breaker", (), {
            "success": lambda self: None,
            "failure": lambda self: None,
            "assert_closed": lambda self: None,
        })(),
    )


def test_watchdog_resumes_only_verified_stopped_llm(monkeypatch, tmp_path):
    _watchdog_test_guards(monkeypatch, tmp_path)
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(), report_path=tmp_path / "report.json",
    )
    monkeypatch.setattr(
        rec, "check",
        lambda **_kwargs: (_ for _ in ()).throw(sr.StackFailure(
            "stack_not_ready", "dependency stopped",
            details={"failed_checks": ["service_health:llm"]},
        )),
    )
    monkeypatch.setattr(rec, "wait_ready", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(
        sr, "_watched_service_snapshot",
        lambda _key, **_kwargs: {
            "key": "llm", "scope": "system", "unit": "llama-server.service",
            "installed": True, "load_state": "loaded",
            "active_state": "active", "main_pid": "4242",
            "process_stopped": True, "healthy": None,
            "observation_error": "",
        },
    )
    monkeypatch.setattr(sr, "_read_process_uid", lambda _pid: 1000)
    monkeypatch.setattr(sr, "_service_uid", lambda: 1000)
    monkeypatch.setattr(sr, "_read_process_state", lambda _pid: "T")
    monkeypatch.setattr(
        rec, "_wait_watched_healthy", lambda _key: {"healthy": True})
    signals = []
    monkeypatch.setattr(sr.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    result = rec.watchdog()

    assert result["ok"] is True
    assert result["repaired"] == [{
        "service": "llm", "action": "sigcont", "pid": 4242,
        "scope": "system", "unit": "llama-server.service",
    }]
    assert signals == [(4242, sr.signal.SIGCONT)]
    assert not any(call[0] == "run" for call in rec.systemctl.calls)


def test_watchdog_refuses_llm_pid_owned_by_another_user(monkeypatch, tmp_path):
    _watchdog_test_guards(monkeypatch, tmp_path)
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(), report_path=tmp_path / "report.json",
    )
    monkeypatch.setattr(
        rec, "check",
        lambda **_kwargs: (_ for _ in ()).throw(sr.StackFailure(
            "stack_not_ready", "dependency stopped",
            details={"failed_checks": ["service_health:llm"]},
        )),
    )
    monkeypatch.setattr(
        sr, "_watched_service_snapshot",
        lambda _key, **_kwargs: {
            "key": "llm", "scope": "system", "unit": "llama-server.service",
            "installed": True, "load_state": "loaded",
            "active_state": "active", "main_pid": "4242",
            "process_stopped": True, "observation_error": "",
        },
    )
    monkeypatch.setattr(sr, "_read_process_uid", lambda _pid: 2000)
    monkeypatch.setattr(sr, "_service_uid", lambda: 1000)
    monkeypatch.setattr(sr, "_read_process_state", lambda _pid: "T")
    monkeypatch.setattr(
        sr.os, "kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unsafe signal")),
    )

    with pytest.raises(sr.StackFailure) as caught:
        rec.watchdog()

    assert caught.value.code == "unsafe_process_resume"


def test_watchdog_restarts_exact_resolved_searx_unit(monkeypatch, tmp_path):
    _watchdog_test_guards(monkeypatch, tmp_path)
    fake = FakeSystemctl()
    rec = sr.StackReconciler(
        systemctl=fake, report_path=tmp_path / "report.json",
    )
    monkeypatch.setattr(
        rec, "check",
        lambda **_kwargs: (_ for _ in ()).throw(sr.StackFailure(
            "stack_not_ready", "search degraded",
            details={"failed_checks": ["service_health:searxng"]},
        )),
    )
    monkeypatch.setattr(
        sr, "_watched_service_snapshot",
        lambda _key, **_kwargs: {
            "key": "searxng", "scope": "system", "unit": "searxng.service",
            "installed": True, "load_state": "loaded",
            "active_state": "active", "healthy": False,
            "observation_error": "",
        },
    )
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(
        rec, "_wait_watched_healthy", lambda _key: {"healthy": True})
    monkeypatch.setattr(rec, "wait_ready", lambda **_kwargs: {"ok": True})

    result = rec.watchdog()

    assert result["ok"] is True
    assert ("run", "system", "restart", "searxng.service") in fake.calls
    assert result["repaired"][0] == {
        "service": "searxng", "action": "restart",
        "scope": "system", "unit": "searxng.service",
    }


@pytest.mark.parametrize(
    ("state", "enabled", "worker_available", "reason", "expected"),
    (
        ("degraded", False, False, "feature_disabled", True),
        ("recovering", True, True, "recovery_incomplete", True),
        ("ready", True, True, "none", True),
        ("degraded", True, True, "execution_deadline_exceeded", False),
        ("degraded", True, False, "health_stale", False),
    ),
)
def test_durable_watchdog_distinguishes_operational_and_failed_states(
    state,
    enabled,
    worker_available,
    reason,
    expected,
):
    row = {
        "installed": True,
        "load_state": "loaded",
        "active_state": "active",
        "observation_error": "",
        "application_state": state,
        "application_enabled": enabled,
        "application_worker_available": worker_available,
        "health_detail": reason,
        "healthy": state == "ready",
    }
    assert sr._watched_service_ok("durable_workloads", row) is expected


def test_watchdog_restarts_only_the_overdue_durable_worker(
    monkeypatch,
    tmp_path,
):
    _watchdog_test_guards(monkeypatch, tmp_path)
    fake = FakeSystemctl()
    rec = sr.StackReconciler(
        systemctl=fake, report_path=tmp_path / "report.json",
    )
    monkeypatch.setattr(
        rec,
        "check",
        lambda **_kwargs: (_ for _ in ()).throw(sr.StackFailure(
            "stack_not_ready",
            "LRE execution exceeded its deadline",
            details={
                "failed_checks": ["service_health:durable_workloads"],
            },
        )),
    )
    monkeypatch.setattr(
        sr,
        "_watched_service_snapshot",
        lambda _key, **_kwargs: {
            "key": "durable_workloads",
            "scope": "user",
            "unit": "metnos-durable-worker.service",
            "installed": True,
            "load_state": "loaded",
            "active_state": "active",
            "healthy": False,
            "health_detail": "execution_deadline_exceeded",
            "application_state": "degraded",
            "application_enabled": True,
            "application_worker_available": True,
            "observation_error": "",
        },
    )
    monkeypatch.setattr(
        rec,
        "require_quiescent",
        lambda: (_ for _ in ()).throw(
            AssertionError("an isolated LRE restart must not stop user turns")
        ),
    )
    monkeypatch.setattr(
        rec, "_wait_watched_healthy", lambda _key: {"healthy": True},
    )
    monkeypatch.setattr(rec, "wait_ready", lambda **_kwargs: {"ok": True})

    result = rec.watchdog()

    assert result["repaired"] == [{
        "service": "durable_workloads",
        "action": "restart",
        "scope": "user",
        "unit": "metnos-durable-worker.service",
    }]
    assert ("run", "user", "restart", "metnos-durable-worker.service") in (
        fake.calls
    )


def test_watchdog_does_not_loop_on_an_incompatible_durable_schema(
    monkeypatch,
    tmp_path,
):
    _watchdog_test_guards(monkeypatch, tmp_path)
    fake = FakeSystemctl()
    rec = sr.StackReconciler(
        systemctl=fake, report_path=tmp_path / "report.json",
    )
    monkeypatch.setattr(
        rec,
        "check",
        lambda **_kwargs: (_ for _ in ()).throw(sr.StackFailure(
            "stack_not_ready",
            "LRE schema is incompatible",
            details={
                "failed_checks": ["service_health:durable_workloads"],
            },
        )),
    )
    monkeypatch.setattr(
        sr,
        "_watched_service_snapshot",
        lambda _key, **_kwargs: {
            "key": "durable_workloads",
            "scope": "user",
            "unit": "metnos-durable-worker.service",
            "installed": True,
            "load_state": "loaded",
            "active_state": "active",
            "healthy": False,
            "health_detail": "schema_incompatible",
            "application_state": "degraded",
            "application_enabled": True,
            "application_worker_available": False,
            "observation_error": "",
        },
    )

    with pytest.raises(sr.StackFailure) as caught:
        rec.watchdog()

    assert caught.value.code == "service_repair_unsafe"
    assert not any(call[0] == "run" for call in fake.calls)


def test_root_user_manager_actions_run_as_explicit_service_user(monkeypatch):
    seen = {}
    monkeypatch.setattr(sr.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        sr.pwd, "getpwnam", lambda _name: type("Identity", (), {"pw_uid": 1234})(),
    )

    def run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(sr.subprocess, "run", run)
    result = sr.Systemctl(service_user="metnos-test").run(
        "user", "show", "metnos.target",
    )
    assert result.returncode == 0
    assert seen["command"] == [
        "runuser", "--user", "metnos-test", "--",
        "systemctl", "--user", "show", "metnos.target",
    ]
    assert seen["env"]["XDG_RUNTIME_DIR"] == "/run/user/1234"
    assert seen["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1234/bus"


def test_explicit_unknown_service_user_fails_closed(monkeypatch):
    monkeypatch.setattr(
        sr.pwd, "getpwnam", lambda _name: (_ for _ in ()).throw(KeyError()),
    )
    with pytest.raises(sr.StackFailure) as caught:
        sr.Systemctl(service_user="missing").run(
            "user", "show", "metnos.target",
        )
    assert caught.value.code == "service_user_invalid"


def test_systemctl_show_accepts_closed_control_plane_units(monkeypatch):
    monkeypatch.setattr(
        sr.Systemctl, "run",
        lambda _self, _scope, *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout="LoadState=loaded\nActiveState=inactive\n", stderr="",
        ),
    )
    adapter = sr.Systemctl()
    for unit in sr.CONTROL_PLANE_UNITS:
        assert adapter.show(unit)["LoadState"] == "loaded"


def test_systemctl_show_accepts_exact_central_maintenance_catalog(monkeypatch):
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1

    observed = []

    def run(_self, scope, *_args, **_kwargs):
        observed.append((scope, _args[1]))
        return subprocess.CompletedProcess(
            [], 0, stdout="LoadState=loaded\nActiveState=inactive\n", stderr="",
        )

    monkeypatch.setattr(sr.Systemctl, "run", run)
    adapter = sr.Systemctl()
    for scope, unit in MAINTENANCE_TARGETS_V1:
        assert adapter.show(unit, scope)["LoadState"] == "loaded"
    assert observed == list(MAINTENANCE_TARGETS_V1)


def test_systemctl_show_accepts_only_canonical_product_system_units(monkeypatch):
    from executor_birth_service_catalog import SERVICE_SOURCE_V1

    observed = []

    def run(_self, scope, *args, **_kwargs):
        assert args[0] == "show"
        observed.append((scope, args[1]))
        return subprocess.CompletedProcess(
            [], 0, stdout="LoadState=loaded\nActiveState=active\n", stderr="",
        )

    monkeypatch.setattr(sr.Systemctl, "run", run)
    units = tuple(item.unit_name for item in SERVICE_SOURCE_V1 if item.unit_name)
    for unit in units:
        assert sr.Systemctl().show(unit, "system")["LoadState"] == "loaded"
    assert observed == [("system", unit) for unit in units]


def test_systemctl_show_rejects_unit_outside_exact_scope_catalog(monkeypatch):
    monkeypatch.setattr(
        sr.Systemctl, "run",
        lambda *_args, **_kwargs: pytest.fail("systemctl must not be invoked"),
    )
    adapter = sr.Systemctl()
    for scope, unit in (
        ("user", "metnos-backup.service"),
        ("user", "llama-server.service"),
        ("system", "unrelated.service"),
    ):
        with pytest.raises(sr.StackFailure) as caught:
            adapter.show(unit, scope)
        assert caught.value.code == "unknown_unit"


def test_check_rejects_catalog_drift(monkeypatch, tmp_path):
    _wire(monkeypatch, _composite(names=["delete_files"]))
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(), report_path=tmp_path / "report.json",
    )
    with pytest.raises(sr.StackFailure, match="readiness") as caught:
        rec.check()
    assert caught.value.code == "stack_not_ready"
    assert caught.value.details["failed_checks"] == ["catalog_parity"]


def test_optional_absent_sidecar_does_not_break_fresh_install(monkeypatch, tmp_path):
    _wire(monkeypatch, _composite(sidecar_ok=False))
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(playwright_loaded=False),
        report_path=tmp_path / "report.json",
    )
    assert rec.check()["ok"] is True


def test_installed_sidecar_must_be_aligned(monkeypatch, tmp_path):
    _wire(monkeypatch, _composite(sidecar_ok=False))
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(playwright_loaded=True),
        report_path=tmp_path / "report.json",
    )
    with pytest.raises(sr.StackFailure) as caught:
        rec.check()
    assert "sidecar_contract" in caught.value.details["failed_checks"]


def _signed_readiness_catalog(monkeypatch):
    import dataclasses
    import services_registry

    profile = tuple(dataclasses.replace(
        spec, targets=(services_registry.ServiceTarget(
            spec.targets[-1].unit, "system",
        ),),
    ) for spec in services_registry.catalog())
    monkeypatch.setattr(services_registry, "readiness_catalog", lambda: profile)
    return profile


@pytest.mark.parametrize("stopped", [False, True])
def test_signed_system_sidecar_health_is_required_unless_deliberately_stopped(
    monkeypatch, tmp_path, stopped,
):
    import services_registry

    _wire(monkeypatch, _composite(sidecar_ok=False))
    _signed_readiness_catalog(monkeypatch)
    monkeypatch.setattr(
        services_registry, "desired_state",
        lambda key: "stopped" if stopped and key == "playwright" else "running",
    )
    fake = FakeSystemctl()

    def show(unit, scope="user"):
        fake.calls.append(("show", scope, unit))
        return {
            "LoadState": "loaded" if scope == "system" else "not-found",
            "ActiveState": "active" if scope == "system" else "inactive",
        }

    fake.show = show
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    if stopped:
        assert rec.check()["ok"] is True
    else:
        with pytest.raises(sr.StackFailure) as caught:
            rec.check()
        assert "sidecar_contract" in caught.value.details["failed_checks"]
    assert ("show", "system", "metnos-playwright.service") in fake.calls
    assert not any(call[1] == "user" for call in fake.calls)


def test_signed_system_component_failure_cannot_hide_behind_absent_user_unit(
    monkeypatch, tmp_path,
):
    _wire(monkeypatch, _composite())
    _signed_readiness_catalog(monkeypatch)
    fake = FakeSystemctl()

    def show(unit, scope="user"):
        return {
            "LoadState": "loaded" if scope == "system" else "not-found",
            "ActiveState": "failed" if unit == "metnos-telegram-daemon.service"
            else "active",
        }

    fake.show = show
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    with pytest.raises(sr.StackFailure) as caught:
        rec.check()
    assert "managed_components" in caught.value.details["failed_checks"]


def test_readiness_passes_the_same_signed_profile_to_watched_services(
    monkeypatch, tmp_path,
):
    _wire(monkeypatch, _composite())
    profile = _signed_readiness_catalog(monkeypatch)
    observed = []

    def snapshot(key, **kwargs):
        observed.append((key, kwargs.get("spec")))
        return {"installed": True, "load_state": "loaded", "active_state": "active",
                "healthy": True, "process_stopped": False, "observation_error": ""}

    monkeypatch.setattr(sr, "_watched_service_snapshot", snapshot)
    rec = sr.StackReconciler(systemctl=FakeSystemctl(), report_path=tmp_path / "report.json")
    assert rec.check()["ok"] is True
    by_key = {spec.key: spec for spec in profile}
    assert observed == [(key, by_key[key]) for key in sr.WATCHED_SERVICE_KEYS]


def test_watchdog_controls_only_the_installed_profile(monkeypatch):
    _signed_readiness_catalog(monkeypatch)
    validate = sr.StackReconciler._validate_watched_target
    assert validate("durable_workloads", {
        "unit": "metnos-durable-worker.service", "scope": "system",
    }) == ("system", "metnos-durable-worker.service")
    for unit, scope in (
        ("metnos-durable-worker.service", "user"),
        ("unrelated.service", "system"),
    ):
        with pytest.raises(sr.StackFailure) as caught:
            validate("durable_workloads", {"unit": unit, "scope": scope})
        assert caught.value.code == "invalid_service_target"


@pytest.mark.parametrize("operation", ["check", "watchdog"])
def test_readiness_rejects_invalid_signed_profile_before_observation(
        monkeypatch, tmp_path, operation):
    import services_registry
    import service_health_monitor

    monkeypatch.setattr(service_health_monitor, "run", lambda: {"ok": True})
    monkeypatch.setattr(
        services_registry, "readiness_catalog",
        lambda: (_ for _ in ()).throw(ValueError("invalid chain")),
    )
    monkeypatch.setattr(sr, "_json_request", lambda *a, **k: pytest.fail("no probe"))
    fake = FakeSystemctl()
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    monkeypatch.setattr(rec, "restart", lambda **kwargs: pytest.fail("no restart"))
    with pytest.raises(sr.StackFailure) as caught:
        getattr(rec, operation)()
    assert caught.value.code == "service_catalog_unavailable"
    assert fake.calls == []


def test_installed_managed_component_cannot_be_silently_stopped(
        monkeypatch, tmp_path):
    _wire(monkeypatch, _composite())
    fake = FakeSystemctl()
    original = fake.show

    def show(unit, scope="user"):
        state = original(unit, scope)
        if unit == "metnos-telegram-daemon.service":
            state["ActiveState"] = "inactive"
        return state

    fake.show = show
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    with pytest.raises(sr.StackFailure) as caught:
        rec.check()
    assert "managed_components" in caught.value.details["failed_checks"]


def test_wait_ready_retries_transient_startup_failure(monkeypatch, tmp_path):
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(), report_path=tmp_path / "report.json",
    )
    attempts = []

    def check(**_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise sr.StackFailure("endpoint_unavailable", "starting")
        return {"ok": True}

    monkeypatch.setattr(rec, "check", check)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)

    assert rec.wait_ready(timeout_s=1)["ok"] is True
    assert len(attempts) == 2


def test_restart_refuses_non_quiescent_stack(monkeypatch, tmp_path):
    _wire(monkeypatch, _composite(quiescent=False))
    fake = FakeSystemctl()
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    with pytest.raises(sr.StackFailure) as caught:
        rec.restart()
    assert caught.value.code == "stack_busy"
    assert not any(call[0] == "run" for call in fake.calls)


@pytest.mark.parametrize("scope", ["user", "system"])
def test_restart_uses_only_installed_integrated_target(monkeypatch, tmp_path, scope):
    if scope == "system":
        _signed_readiness_catalog(monkeypatch)
    fake = FakeSystemctl(system_http_active=scope == "system")
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(rec, "wait_ready", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(sr, "verify_named_executors", lambda names, sign_first=False, changed_only=False: [])
    monkeypatch.setattr(
        sr, "CircuitBreaker",
        lambda: type("Breaker", (), {
            "success": lambda self: None,
            "failure": lambda self: None,
            "assert_closed": lambda self: None,
        })(),
    )
    out = rec.restart()
    assert out["ok"] is True
    assert ("run", scope, "restart", "metnos.target") in fake.calls
    assert not any(
        call[0] == "run" and call[1] != scope for call in fake.calls
    )
    assert not any("metnos-http.service" in call for call in fake.calls if call[0] == "run")


def test_restart_refuses_when_target_is_not_installed(monkeypatch, tmp_path):
    fake = FakeSystemctl(target_loaded=False)
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(sr, "verify_named_executors", lambda names, sign_first=False, changed_only=False: [])
    with pytest.raises(sr.StackFailure) as caught:
        rec.restart()
    assert caught.value.code == "target_not_installed"


def test_restart_never_starts_user_target_beside_legacy_http(monkeypatch, tmp_path):
    fake = FakeSystemctl(system_http_active=True)
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(sr, "verify_named_executors", lambda *_args, **_kwargs: [])
    with pytest.raises(sr.StackFailure) as caught:
        rec.restart()
    assert caught.value.code == "legacy_baseline_active"
    assert not any(call[:3] == ("run", "user", "restart") for call in fake.calls)


def test_executor_name_cannot_escape_catalog():
    with pytest.raises(sr.StackFailure) as caught:
        sr.verify_named_executors(["../ssh"], sign_first=True)
    assert caught.value.code == "invalid_executor"


def test_named_executor_store_verification_uses_live_catalog(
        monkeypatch, tmp_path):
    import loader
    import manifest_inventory
    import sign
    import executor_birth_intent

    directory = tmp_path / "authoring" / "core" / "read_files"
    _signed_authoring_executor(directory, "read_files")
    contract_id = manifest_inventory.ContractId(
        manifest_inventory.ManifestOrigin.CORE, "read_files/manifest.toml",
    )
    ref = manifest_inventory.ManifestRef(
        contract_id=contract_id,
        origin=contract_id.origin,
        status=manifest_inventory.ManifestStatus.ADMITTED,
        source_root=directory.parent,
        manifest_path=directory / "manifest.toml",
        manifest_relative=contract_id.relative_manifest,
        allowed_code_roots=(directory.parent,),
    )
    from executor_birth_authoring import (
        advance_version, authoring_paths, authoring_tree_id, observe_tree,
    )
    paths = authoring_paths(directory, contract_id.value)
    advance_version(
        paths, contract_id.value, authoring_tree_id(observe_tree(directory)),
    )
    inventory = manifest_inventory.ManifestInventory((ref,), ())
    monkeypatch.setattr(sr, "_repo_root", lambda: tmp_path / "repo")
    monkeypatch.setattr(
        manifest_inventory,
        "resolve_manifest_layout",
        lambda **_kwargs: manifest_inventory.ManifestLayout.STORE_ONLY,
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_manifests",
        lambda **_kwargs: inventory,
    )
    published = []

    def accept_birth(intent):
        candidate = intent.candidate_source_root
        published.append({
            "root": candidate,
            "files": sorted(
                path.relative_to(candidate).as_posix()
                for path in candidate.rglob("*") if path.is_file()
            ),
            "manifest": (candidate / "manifest.toml").read_text(encoding="utf-8"),
        })
        return SimpleNamespace(
            error_code=None,
            publication=SimpleNamespace(operation="publish"),
        )

    monkeypatch.setattr(
        executor_birth_intent, "submit_stack_reconcile_birth", accept_birth,
    )
    monkeypatch.setattr(
        sign,
        "verify_executor",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("store-only must not verify unsigned authoring bytes"),
        ),
    )
    monkeypatch.setattr(
        loader,
        "load_catalog",
        lambda **_kwargs: SimpleNamespace(
            executors={"read_files": SimpleNamespace(digest="sha256:live")},
        ),
    )

    result = sr.verify_named_executors(["read_files"], sign_first=True)

    assert len(published) == 1
    assert published[0]["root"] != directory
    assert published[0]["files"] == [
        "main.py", "manifest.lang_state.json", "manifest.toml",
    ]
    expected = "sha256:" + hashlib.sha256(b"print('ok')\n").hexdigest()
    assert f'digest = "{expected}"' in published[0]["manifest"]
    assert result == [{
        "name": "read_files", "ok": True, "digest": "sha256:live",
    }]


def _store_only_deploy(monkeypatch, tmp_path, *, authored, edited):
    """Deploy one executor under STORE_ONLY and return what reached Birth.

    ``authored`` are the bytes of the authoring tree behind the store
    reference (None: never materialised, as on the host of 10/9/2026);
    ``edited`` the operator's working copy (None: no local directory).
    """
    import loader
    import manifest_inventory
    import sign
    import executor_birth_intent
    from executor_birth_authoring import (
        advance_version, authoring_paths, authoring_tree_id, observe_tree,
    )

    authoring = tmp_path / "authoring" / "core" / "read_files"
    contract_id = manifest_inventory.ContractId(
        manifest_inventory.ManifestOrigin.CORE, "read_files/manifest.toml",
    )
    if authored is not None:
        _signed_authoring_executor(authoring, "read_files")
        (authoring / "main.py").write_bytes(authored)
        advance_version(
            authoring_paths(authoring, contract_id.value), contract_id.value,
            authoring_tree_id(observe_tree(authoring)),
        )
    ref = manifest_inventory.ManifestRef(
        contract_id=contract_id,
        origin=contract_id.origin,
        status=manifest_inventory.ManifestStatus.ADMITTED,
        source_root=authoring.parent,
        manifest_path=authoring / "manifest.toml",
        manifest_relative=contract_id.relative_manifest,
        allowed_code_roots=(authoring.parent,),
    )
    repo = tmp_path / "repo"
    (repo / "executors").mkdir(parents=True)
    if edited is not None:
        working = repo / "executors" / "read_files"
        _signed_authoring_executor(working, "read_files")
        (working / "main.py").write_bytes(edited)
    monkeypatch.setattr(sr, "_repo_root", lambda: repo)
    monkeypatch.setattr(
        manifest_inventory, "resolve_manifest_layout",
        lambda **_kwargs: manifest_inventory.ManifestLayout.STORE_ONLY,
    )
    monkeypatch.setattr(
        manifest_inventory, "inventory_manifests",
        lambda **_kwargs: manifest_inventory.ManifestInventory((ref,), ()),
    )
    reached = []

    def accept_birth(intent):
        candidate = intent.candidate_source_root
        reached.append({
            "code": (candidate / "main.py").read_bytes(),
            "manifest": (candidate / "manifest.toml").read_text(encoding="utf-8"),
        })
        return SimpleNamespace(
            error_code=None, publication=SimpleNamespace(operation="publish"),
        )

    monkeypatch.setattr(
        executor_birth_intent, "submit_stack_reconcile_birth", accept_birth,
    )
    monkeypatch.setattr(
        sign, "verify_executor",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("store-only must not verify unsigned authoring bytes"),
        ),
    )
    monkeypatch.setattr(
        loader, "load_catalog",
        lambda **_kwargs: SimpleNamespace(
            executors={"read_files": SimpleNamespace(digest="sha256:live")},
        ),
    )
    sr.verify_named_executors(["read_files"], sign_first=True)
    return reached


def test_store_only_deploy_without_an_edit_readmits_what_is_authored(
        monkeypatch, tmp_path):
    authored = b"print('ok')\n"
    reached = _store_only_deploy(
        monkeypatch, tmp_path, authored=authored, edited=None)
    assert [item["code"] for item in reached] == [authored]


def test_store_only_deploy_with_nothing_to_admit_fails_closed(
        monkeypatch, tmp_path):
    with pytest.raises(sr.StackFailure) as caught:
        _store_only_deploy(monkeypatch, tmp_path, authored=None, edited=None)
    assert caught.value.code == "birth_unavailable"


def test_named_executor_legacy_verification_keeps_signature_boundary(
        monkeypatch, tmp_path):
    import manifest_inventory
    import sign
    import executor_birth_intent

    directory = tmp_path / "executors" / "read_files"
    _signed_authoring_executor(directory, "read_files")
    monkeypatch.setattr(sr, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        manifest_inventory,
        "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.AUTHORING,
    )
    published = []
    monkeypatch.setattr(
            executor_birth_intent, "submit_stack_reconcile_birth",
        lambda intent: (
            published.append(intent.candidate_source_root)
            or SimpleNamespace(
                error_code=None,
                publication=SimpleNamespace(operation="publish"),
            )
        ),
    )
    monkeypatch.setattr(
        sign,
        "verify_executor",
        lambda path: (True, {"digest": f"sha256:{path.name}"}),
    )

    result = sr.verify_named_executors(["read_files"], sign_first=True)

    assert len(published) == 1
    assert published[0] != directory
    assert result == [{
        "name": "read_files", "ok": True, "digest": "sha256:read_files",
    }]


def test_unreachable_active_http_never_guesses_quiescence(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    monkeypatch.setattr(sr, "_admin_key", lambda *_args: "key")
    monkeypatch.setattr(
        sr, "_json_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sr.StackFailure("endpoint_unavailable", "down")),
    )
    with pytest.raises(sr.StackFailure) as caught:
        rec.require_quiescent()
    assert caught.value.code == "quiescence_unknown"


def test_inactive_http_can_recover_only_with_idle_sidecar(monkeypatch, tmp_path):
    fake = FakeSystemctl()

    def show(unit, scope="user"):
        if unit == "metnos-http.service":
            return {"LoadState": "loaded", "ActiveState": "failed"}
        return FakeSystemctl.show(fake, unit, scope)

    fake.show = show
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    monkeypatch.setattr(sr, "_admin_key", lambda *_args: "key")

    def request(url, **_kwargs):
        if url.endswith("/agent/stack/health"):
            raise sr.StackFailure("endpoint_unavailable", "down")
        return {"ok": True, "broker": {"active_sessions": 0}}

    monkeypatch.setattr(sr, "_json_request", request)
    assert rec.require_quiescent()["source"] == "inactive_http_and_sidecar_broker"


def test_inactive_http_and_sidecar_are_provably_quiescent(monkeypatch, tmp_path):
    fake = FakeSystemctl()

    def show(unit, scope="user"):
        if unit in {"metnos-http.service", "metnos-playwright.service"}:
            return {"LoadState": "loaded", "ActiveState": "failed"}
        return FakeSystemctl.show(fake, unit, scope)

    fake.show = show
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    monkeypatch.setattr(sr, "_admin_key", lambda *_args: "key")
    monkeypatch.setattr(
        sr, "_json_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sr.StackFailure("endpoint_unavailable", "down")),
    )
    assert rec.require_quiescent()["source"] == (
        "inactive_http_and_inactive_sidecar"
    )


def test_circuit_breaker_opens_and_recovers(tmp_path):
    breaker = sr.CircuitBreaker(tmp_path / "circuit.json")
    breaker.failure(now=100)
    breaker.failure(now=101)
    breaker.failure(now=102)
    with pytest.raises(sr.StackFailure) as caught:
        breaker.assert_closed(now=103)
    assert caught.value.code == "circuit_open"
    breaker.success()
    breaker.assert_closed(now=104)


def test_open_circuit_refusal_does_not_extend_it(monkeypatch, tmp_path):
    """A periodic watchdog check must eventually leave the open interval."""
    circuit_path = tmp_path / "circuit.json"
    circuit_path.write_text(json.dumps({
        "schema_version": 1,
        "failures": [100.0, 101.0, 102.0],
        "opened_until": 1000.0,
    }))
    rec = sr.StackReconciler(
        systemctl=FakeSystemctl(), report_path=tmp_path / "report.json",
    )
    breaker_class = sr.CircuitBreaker
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(
        sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(
        sr, "CircuitBreaker", lambda: breaker_class(circuit_path))
    monkeypatch.setattr(sr.time, "time", lambda: 200.0)

    with pytest.raises(sr.StackFailure) as caught:
        rec.restart(automatic=True)

    assert caught.value.code == "circuit_open"
    state = json.loads(circuit_path.read_text())
    assert state["opened_until"] == 1000.0
    assert state["failures"] == [100.0, 101.0, 102.0]


def test_lock_rejects_symlink(tmp_path):
    target = tmp_path / "actual"
    target.write_text("")
    link = tmp_path / "lock"
    link.symlink_to(target)
    lock = sr.ReconcileLock(link)
    with pytest.raises(OSError):
        lock.acquire()


def test_restart_frees_the_catalog_before_handing_over_to_systemd(
    monkeypatch, tmp_path,
) -> None:
    """The server being restarted must be able to read its own catalog.

    Holding the catalog boundary across `systemctl restart` starves the very
    server the reconcile is starting: it cannot load its catalog, is never
    ready, and the readiness gate quarantines the whole stack. The lifecycle
    boundary is the one that must survive, and it does.
    """
    import contract_store

    catalog_held = [False]
    observed: list[tuple[str, bool, bool]] = []

    @contextlib.contextmanager
    def catalog_lock(*, timeout):
        catalog_held[0] = True
        try:
            yield
        finally:
            catalog_held[0] = False

    monkeypatch.setattr(contract_store, "catalog_admission_lock", catalog_lock)

    lock_path = tmp_path / "lock"
    lifecycle = sr.ReconcileLock(lock_path)
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lifecycle)

    class RecordingSystemctl(FakeSystemctl):
        def run(self, scope: str, *args: str, timeout_s: float = 120):
            observed.append(("run", catalog_held[0], lock_path.exists()))
            return super().run(scope, *args, timeout_s=timeout_s)

    fake = RecordingSystemctl()
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(sr, "verify_named_executors", lambda names, sign_first=False, changed_only=False: [])
    monkeypatch.setattr(
        sr, "CircuitBreaker",
        lambda: type("Breaker", (), {
            "success": lambda self: None,
            "failure": lambda self: None,
            "assert_closed": lambda self: None,
        })(),
    )

    def wait_ready(**_kwargs):
        observed.append(("wait_ready", catalog_held[0], lock_path.exists()))
        return {"ok": True}

    monkeypatch.setattr(rec, "wait_ready", wait_ready)

    assert rec.restart()["ok"] is True
    assert observed == [("run", False, True), ("wait_ready", False, True)]
    assert catalog_held[0] is False


def test_reconcile_boundaries_release_catalog_is_idempotent(
    monkeypatch, tmp_path,
) -> None:
    import contract_store

    events: list[str] = []

    @contextlib.contextmanager
    def catalog_lock(*, timeout):
        events.append("catalog_enter")
        try:
            yield
        finally:
            events.append("catalog_exit")

    monkeypatch.setattr(contract_store, "catalog_admission_lock", catalog_lock)
    with sr.catalog_reconcile_lock(path=tmp_path / "lock", wait_s=0.25) as boundaries:
        boundaries.release_catalog()
        boundaries.release_catalog()
        events.append("body")
    assert events == ["catalog_enter", "catalog_exit", "body"]


def test_repairing_one_dependency_frees_the_catalog_too(
    monkeypatch, tmp_path,
) -> None:
    """Repairing a single dependency is the same handover, on a smaller scale.

    It restarts a unit and then waits for readiness — up to two minutes with
    nobody able to read the catalog, which is every ordinary turn refused for
    as long as the repair lasts.
    """
    import contract_store

    catalog_held = [False]
    observed: list[tuple[str, bool, bool]] = []

    @contextlib.contextmanager
    def catalog_lock(*, timeout):
        catalog_held[0] = True
        try:
            yield
        finally:
            catalog_held[0] = False

    monkeypatch.setattr(contract_store, "catalog_admission_lock", catalog_lock)

    lock_path = tmp_path / "lock"
    lifecycle = sr.ReconcileLock(lock_path)
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lifecycle)

    class RecordingSystemctl(FakeSystemctl):
        def run(self, scope: str, *args: str, timeout_s: float = 120):
            observed.append(("run", catalog_held[0], lock_path.exists()))
            return super().run(scope, *args, timeout_s=timeout_s)

    rec = sr.StackReconciler(
        systemctl=RecordingSystemctl(), report_path=tmp_path / "report.json",
    )
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(
        sr, "CircuitBreaker",
        lambda: type("Breaker", (), {
            "success": lambda self: None,
            "failure": lambda self: None,
            "assert_closed": lambda self: None,
        })(),
    )
    monkeypatch.setattr(
        sr, "_watched_service_snapshot",
        lambda key, **_kwargs: {"active_state": "failed", "healthy": False},
    )
    monkeypatch.setattr(sr, "_watched_service_ok", lambda _key, _row: False)
    monkeypatch.setattr(
        sr.StackReconciler, "_validate_watched_target",
        staticmethod(lambda _key, _row: ("system", "searxng.service")),
    )
    monkeypatch.setattr(
        sr.StackReconciler, "_wait_watched_healthy",
        staticmethod(lambda _key, **_kwargs: {"healthy": True}),
    )

    def wait_ready(**_kwargs):
        observed.append(("wait_ready", catalog_held[0], lock_path.exists()))
        return {"ok": True}

    monkeypatch.setattr(rec, "wait_ready", wait_ready)

    assert rec._repair_watched(["searxng"])["ok"] is True
    assert observed == [("run", False, True), ("wait_ready", False, True)]
    assert catalog_held[0] is False


def _declares(manifest: str, code: bytes) -> bool:
    return f'digest = "sha256:{hashlib.sha256(code).hexdigest()}"' in manifest


def test_store_only_deploy_admits_the_edit_where_nothing_was_authored(
        monkeypatch, tmp_path):
    """Review A-11, first probe: the host had no authoring tree at all.

    The documented command failed with ``authoring_version_invalid: missing``
    before reaching Birth, for every first-party executor.
    """
    edit = b"print('edit')\n"
    reached = _store_only_deploy(
        monkeypatch, tmp_path, authored=None, edited=edit)
    assert [item["code"] for item in reached] == [edit]
    assert _declares(reached[0]["manifest"], edit)


def test_store_only_deploy_admits_the_edit_not_the_stale_authoring(
        monkeypatch, tmp_path):
    """Review A-11, second probe: an authoring tree holding older bytes.

    The command delivered the authored bytes and silently dropped the edit -
    a publication that reports success while shipping the previous code.
    """
    edit = b"print('new')\n"
    reached = _store_only_deploy(
        monkeypatch, tmp_path, authored=b"print('old')\n", edited=edit)
    assert [item["code"] for item in reached] == [edit]
    assert _declares(reached[0]["manifest"], edit)




# --- changed-only admission from an installation (I-001 v3) -----------------
# The capture reads real files; Birth, the verified store reads and the reread
# are substitutes. "store_verified" is proven against those substitutes only.

def _contract_bytes(name, spec):
    files = spec["files"]
    manifest = (
        f'name = "{name}"\ndescription = "{spec.get("description", "d")}"\n'
        "[code]\nfiles = [" + ", ".join(f'"{item}"' for item in files) + "]\n"
        f'digest = "sha256:{"0" * 64}"\n'
    ).encode("utf-8")
    return manifest, spec.get("lang", b'{"version":1}\n'), dict(files)


def _contract_name(contract_id):
    return contract_id.value.rsplit("/", 1)[0].split(":", 1)[-1]


def _release_store(monkeypatch, tmp_path, *, working, served):
    import contract_store
    import executor_birth_intent
    import manifest_inventory
    import sign
    import tomllib
    from types import MappingProxyType
    from executor_birth_snapshot import CandidateSnapshot
    from manifest_code_digest import prepare_manifest_digest_v1

    executors = tmp_path / "repo" / "executors"
    executors.mkdir(parents=True)
    for name, spec in working.items():
        manifest, lang, files = _contract_bytes(name, spec)
        for relative, payload in {
            "manifest.toml": manifest, "manifest.lang_state.json": lang,
            "manifest.toml.sig": b"stale-signature", **files,
        }.items():
            path = executors / name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    refs = {}
    state = {}
    for name, spec in served.items():
        contract_id = manifest_inventory.ContractId(
            manifest_inventory.ManifestOrigin.CORE, f"{name}/manifest.toml")
        refs[name] = manifest_inventory.ManifestRef(
            contract_id=contract_id, origin=contract_id.origin,
            status=manifest_inventory.ManifestStatus.ADMITTED,
            source_root=tmp_path / "store",
            manifest_path=tmp_path / "store" / name / "manifest.toml",
            manifest_relative=contract_id.relative_manifest,
            allowed_code_roots=(tmp_path / "store",),
        )
        manifest, lang, files = _contract_bytes(name, spec)
        state[name] = {
            "generation": f"g-{name}-1", "reread": f"g-{name}-1",
            "payloads": (prepare_manifest_digest_v1(manifest, files), lang, files),
        }
    reached: list[str] = []
    control = {"refuse": set(), "reread_previous": False}

    def owner(ref):
        return _contract_name(ref.contract_id)

    def current_contract(ref, *, trusted_publics, **_kwargs):
        assert trusted_publics
        return contract_store.VerifiedManifest(
            contract_id=ref.contract_id, generation_id=state[owner(ref)]["reread"],
            source_manifest_dir=tmp_path / "store", allowed_code_roots=(),
            manifest_bytes=b"", manifest_hash="", parsed={}, signature_bytes=b"",
            signature_hash="", language_state_bytes=b"", language_state={},
            signed_by="test", declared_code_digest="", verified_code_digest="",
        )

    def served_snapshot(ref, generation_identifier, *, trusted_publics, **_kwargs):
        entry = state[owner(ref)]
        assert generation_identifier == entry["generation"]
        private = tmp_path / f"served-{owner(ref)}-{generation_identifier}"
        private.mkdir(exist_ok=True)
        manifest, lang, files = entry["payloads"]
        return CandidateSnapshot(
            private_root=private, manifest_bytes=manifest,
            language_state_bytes=lang, code_files=MappingProxyType(dict(files)),
        )

    def birth(intent):
        name = _contract_name(intent.contract_id)
        reached.append(name)
        if name in control["refuse"]:
            return SimpleNamespace(request_id=f"r-{name}", error_code="birth_refused",
                                   publication=None, report=None)
        candidate = intent.candidate_source_root
        manifest = (candidate / "manifest.toml").read_bytes()
        declared = tomllib.loads(manifest.decode("utf-8"))["code"]["files"]
        entry = state[name]
        previous = entry["generation"]
        entry["generation"] = f"{previous}+"
        entry["payloads"] = (
            manifest, (candidate / "manifest.lang_state.json").read_bytes(),
            {item: (candidate / item).read_bytes() for item in declared},
        )
        if not control["reread_previous"]:
            entry["reread"] = entry["generation"]
        return SimpleNamespace(
            request_id=f"r-{name}", error_code=None,
            report=SimpleNamespace(candidate_id=f"c-{name}"),
            publication=SimpleNamespace(
                previous_generation_id=previous,
                current_generation_id=entry["generation"], operation="publish"),
        )

    monkeypatch.setattr(sr, "_repo_root", lambda: executors.parent)
    monkeypatch.setattr(
        manifest_inventory, "resolve_manifest_layout",
        lambda **_kwargs: manifest_inventory.ManifestLayout.STORE_ONLY)
    monkeypatch.setattr(
        manifest_inventory, "inventory_manifests",
        lambda **_kwargs: manifest_inventory.ManifestInventory(tuple(refs.values()), ()))
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: [("test", object())])
    monkeypatch.setattr(contract_store, "current_contract", current_contract)
    monkeypatch.setattr(
        contract_store, "acquire_current_reattestation_snapshot", served_snapshot)
    monkeypatch.setattr(executor_birth_intent, "submit_stack_reconcile_birth", birth)

    def run(*, plan=False):
        return sr.verify_named_executors(
            [], sign_first=not plan, changed_only=not plan, plan_only=plan)

    return run, reached, control, executors


def test_release_admission_publishes_an_edit_and_rereads_its_generation(
        monkeypatch, tmp_path):
    run, reached, _control, _root = _release_store(
        monkeypatch, tmp_path,
        working={"alpha": {"files": {"main.py": b"new\n"}}},
        served={"alpha": {"files": {"main.py": b"old\n"}}})
    assert run() == [{
        "name": "alpha", "outcome": "store_verified", "request_id": "r-alpha",
        "candidate_id": "c-alpha", "previous_generation_id": "g-alpha-1",
        "current_generation_id": "g-alpha-1+",
    }]
    assert reached == ["alpha"]


@pytest.mark.parametrize("change", ["manifest", "language", "split"])
def test_release_admission_sees_what_the_code_digest_hides(
        monkeypatch, tmp_path, change):
    from manifest_code_digest import code_digest_of_payloads

    served = {"files": {"a.py": b"ab", "b.py": b"c"}}
    working = {
        "manifest": {"files": served["files"], "description": "changed"},
        "language": {"files": served["files"], "lang": b'{"version":2}\n'},
        "split": {"files": {"a.py": b"a", "b.py": b"bc"}},
    }[change]
    if change == "split":
        assert code_digest_of_payloads(["a.py", "b.py"], working["files"]) == \
            code_digest_of_payloads(["a.py", "b.py"], served["files"])
    run, reached, _control, _root = _release_store(
        monkeypatch, tmp_path, working={"alpha": working}, served={"alpha": served})
    assert [row["outcome"] for row in run()] == ["store_verified"]
    assert reached == ["alpha"]


def test_release_admission_leaves_an_unchanged_executor_alone(monkeypatch, tmp_path):
    spec = {"files": {"main.py": b"same\n"}}
    run, reached, _control, _root = _release_store(
        monkeypatch, tmp_path, working={"alpha": spec}, served={"alpha": spec})
    assert run() == [{"name": "alpha", "outcome": "unchanged", "generation_id": "g-alpha-1"}]
    assert reached == []


def test_release_admission_names_an_executor_the_store_never_admitted(
        monkeypatch, tmp_path):
    run, reached, _control, _root = _release_store(
        monkeypatch, tmp_path,
        working={"beta": {"files": {"main.py": b"new\n"}}}, served={})
    assert run() == [{"name": "beta", "outcome": "not_installed"}]
    assert reached == []


def test_release_plan_lists_every_outcome_without_birth(monkeypatch, tmp_path):
    same = {"files": {"main.py": b"same\n"}}
    run, reached, _control, _root = _release_store(
        monkeypatch, tmp_path,
        working={"alpha": {"files": {"main.py": b"new\n"}}, "beta": same, "gamma": same},
        served={"alpha": {"files": {"main.py": b"old\n"}}, "gamma": same})
    assert [(row["name"], row["outcome"]) for row in run(plan=True)] == [
        ("alpha", "changed"), ("beta", "not_installed"), ("gamma", "unchanged")]
    assert reached == []


def test_release_admission_refuses_a_reread_of_the_previous_generation(
        monkeypatch, tmp_path):
    run, _reached, control, _root = _release_store(
        monkeypatch, tmp_path,
        working={"alpha": {"files": {"main.py": b"new\n"}}},
        served={"alpha": {"files": {"main.py": b"old\n"}}})
    control["reread_previous"] = True
    with pytest.raises(sr.StackFailure) as caught:
        run()
    assert caught.value.code == "birth_admission_failed"
    [row] = caught.value.details["outcomes"]
    assert row["outcome"] == "error" and row["error"] == "store_generation_mismatch"
    assert row["current_generation_id"] == "g-alpha-1+"


def test_release_admission_reports_a_partial_failure_and_resumes(monkeypatch, tmp_path):
    run, reached, control, _root = _release_store(
        monkeypatch, tmp_path,
        working={name: {"files": {"main.py": b"new\n"}} for name in ("alpha", "beta", "gamma")},
        served={name: {"files": {"main.py": b"old\n"}} for name in ("alpha", "beta", "gamma")})
    control["refuse"] = {"beta"}
    with pytest.raises(sr.StackFailure) as caught:
        run()
    assert [(row["name"], row["outcome"]) for row in caught.value.details["outcomes"]] == [
        ("alpha", "store_verified"), ("beta", "error"), ("gamma", "not_attempted")]
    control["refuse"] = set()
    assert [(row["name"], row["outcome"]) for row in run()] == [
        ("alpha", "unchanged"), ("beta", "store_verified"), ("gamma", "store_verified")]
    assert reached == ["alpha", "beta", "beta", "gamma"]


def test_release_admission_names_a_cache_in_the_working_copy(monkeypatch, tmp_path):
    """An installation carries no caches; a development tree does, and the
    closed candidate refuses it by name instead of publishing around it."""
    run, reached, _control, root = _release_store(
        monkeypatch, tmp_path,
        working={"alpha": {"files": {"main.py": b"new\n"}}},
        served={"alpha": {"files": {"main.py": b"old\n"}}})
    (root / "alpha" / "__pycache__").mkdir()
    (root / "alpha" / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"cache")
    with pytest.raises(sr.StackFailure) as caught:
        run()
    assert caught.value.code == "candidate_unavailable"
    assert "candidate_file_extra: __pycache__" in str(caught.value)
    assert caught.value.details["outcomes"] == [{
        "name": "alpha", "outcome": "error",
        "error": "candidate_file_extra: __pycache__"}]
    assert reached == []


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_release_admission_refuses_links_in_the_working_copy(monkeypatch, tmp_path, link):
    run, reached, _control, root = _release_store(
        monkeypatch, tmp_path,
        working={"alpha": {"files": {"main.py": b"new\n"}}},
        served={"alpha": {"files": {"main.py": b"old\n"}}})
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"new\n")
    target = root / "alpha" / "main.py"
    target.unlink()
    (target.symlink_to if link == "symbolic" else target.hardlink_to)(outside)
    with pytest.raises(sr.StackFailure) as caught:
        run()
    assert caught.value.code == "candidate_unavailable"
    assert reached == []


def test_release_admission_refuses_a_file_that_changes_during_capture(
        monkeypatch, tmp_path):
    import executor_birth_snapshot

    run, reached, _control, root = _release_store(
        monkeypatch, tmp_path,
        working={"alpha": {"files": {"main.py": b"new\n"}}},
        served={"alpha": {"files": {"main.py": b"old\n"}}})
    original = executor_birth_snapshot._read_regular

    def racing(directory, relative):
        payload = original(directory, relative)
        if Path(directory) == root / "alpha" and relative == "main.py":
            (root / "alpha" / "main.py").write_bytes(payload + b"# raced\n")
        return payload

    monkeypatch.setattr(executor_birth_snapshot, "_read_regular", racing)
    with pytest.raises(sr.StackFailure) as caught:
        run()
    assert caught.value.code == "candidate_unavailable"
    assert reached == []


def test_restart_does_not_restart_when_nothing_was_admitted(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "_state_dir", lambda: tmp_path)
    fake = FakeSystemctl()
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(
        sr, "verify_named_executors",
        lambda names, **_kwargs: [{"name": "alpha", "outcome": "unchanged"}])
    out = rec.restart(sign_first=True, changed_only=True)
    assert out == {"ok": True, "signed": [{"name": "alpha", "outcome": "unchanged"}],
                   "restarted": False}
    assert not any(call[0] == "run" for call in fake.calls)


def test_changed_only_refuses_to_run_without_birth_or_plan(monkeypatch):
    import manifest_inventory

    monkeypatch.setattr(
        manifest_inventory, "resolve_manifest_layout",
        lambda **_kwargs: manifest_inventory.ManifestLayout.STORE_ONLY)
    monkeypatch.setattr(
        manifest_inventory, "inventory_manifests",
        lambda **_kwargs: manifest_inventory.ManifestInventory((), ()))
    with pytest.raises(sr.StackFailure) as caught:
        sr.verify_named_executors([], changed_only=True)
    assert caught.value.code == "changed_only_invalid"


def _activation_rig(monkeypatch, tmp_path, outcomes):
    """A reconciler whose restart can be refused, over scripted admissions."""
    monkeypatch.setattr(sr, "_state_dir", lambda: tmp_path)
    fake = FakeSystemctl()
    control = {"refuse_restart": False}

    def run(scope, *args, timeout_s=120):
        fake.calls.append(("run", scope, *args))
        refused = control["refuse_restart"] and args[:1] == ("restart",)
        return subprocess.CompletedProcess(
            args, 1 if refused else 0, stdout="", stderr="refused" if refused else "")

    fake.run = run
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(rec, "wait_ready", lambda **_kwargs: {"ok": True})
    script = iter(outcomes)

    def verify(names, **_kwargs):
        step = next(script)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(sr, "verify_named_executors", verify)

    def restarts():
        return sum(1 for call in fake.calls if call[0] == "run" and "restart" in call)

    return rec, control, restarts


def test_a_refused_restart_leaves_the_admission_pending_until_activated(
        monkeypatch, tmp_path):
    admitted = {"name": "alpha", "outcome": "store_verified",
                "current_generation_id": "g-alpha-2"}
    rec, control, restarts = _activation_rig(monkeypatch, tmp_path, [
        [admitted], [{"name": "alpha", "outcome": "unchanged"}],
    ])
    control["refuse_restart"] = True
    with pytest.raises(sr.StackFailure) as caught:
        rec.restart(sign_first=True, changed_only=True)
    assert caught.value.code == "target_restart_failed"
    assert caught.value.details["outcomes"] == [admitted]
    assert caught.value.details["activation_pending"] == [admitted]
    control["refuse_restart"] = False
    out = rec.restart(sign_first=True, changed_only=True)
    assert out["restarted"] is True and out["activated"] == [admitted]
    assert restarts() == 2
    assert sr.PendingActivation(tmp_path / "stack_reconcile_pending_activation.json").rows() == []


def test_a_partial_admission_failure_still_owes_a_restart(monkeypatch, tmp_path):
    admitted = {"name": "alpha", "outcome": "store_verified",
                "current_generation_id": "g-alpha-2"}
    failure = sr.StackFailure("birth_admission_failed", "beta", details={"outcomes": [
        admitted, {"name": "beta", "outcome": "error", "error": "refused"}]})
    rec, _control, restarts = _activation_rig(monkeypatch, tmp_path, [
        failure, [{"name": "alpha", "outcome": "unchanged"}],
    ])
    with pytest.raises(sr.StackFailure) as caught:
        rec.restart(sign_first=True, changed_only=True)
    assert [row["name"] for row in caught.value.details["outcomes"]] == ["alpha", "beta"]
    assert admitted in caught.value.details["activation_pending"]
    assert restarts() == 0
    out = rec.restart(sign_first=True, changed_only=True)
    assert out["activated"] == [admitted] and restarts() == 1


def test_an_unreadable_activation_record_restarts_rather_than_skip(monkeypatch, tmp_path):
    rec, _control, restarts = _activation_rig(monkeypatch, tmp_path, [
        [{"name": "alpha", "outcome": "unchanged"}],
    ])
    (tmp_path / "stack_reconcile_pending_activation.json").write_text("{not json")
    out = rec.restart(sign_first=True, changed_only=True)
    assert out["restarted"] is True and restarts() == 1


def test_undecodable_activation_record_restarts_rather_than_crash(monkeypatch, tmp_path):
    rec, _control, restarts = _activation_rig(monkeypatch, tmp_path, [
        [{"name": "alpha", "outcome": "unchanged"}],
    ])
    (tmp_path / "stack_reconcile_pending_activation.json").write_bytes(b"\xff\xfe")
    out = rec.restart(sign_first=True, changed_only=True)
    assert out["restarted"] is True and restarts() == 1


def test_a_publication_whose_reread_failed_is_still_owed_a_restart(monkeypatch, tmp_path):
    uncertain = {"name": "alpha", "outcome": "error", "error": "store_generation_mismatch",
                 "current_generation_id": "g-alpha-2"}
    failure = sr.StackFailure("birth_admission_failed", "alpha",
                              details={"outcomes": [uncertain]})
    rec, _control, restarts = _activation_rig(monkeypatch, tmp_path, [
        failure, [{"name": "alpha", "outcome": "unchanged"}],
    ])
    with pytest.raises(sr.StackFailure) as caught:
        rec.restart(sign_first=True, changed_only=True)
    assert caught.value.details["outcomes"] == [uncertain]
    out = rec.restart(sign_first=True, changed_only=True)
    # Owed and restarted, but never relabelled as verified.
    assert out["activated"] == [uncertain] and restarts() == 1


def test_a_process_killed_mid_batch_leaves_the_restart_owed(monkeypatch, tmp_path):
    """The intent precedes every effect: a death between Birth and any
    record still leaves a restart owed to the next deploy."""
    rec, _control, restarts = _activation_rig(monkeypatch, tmp_path, [
        KeyboardInterrupt(), [{"name": "alpha", "outcome": "unchanged"}],
    ])
    with pytest.raises(KeyboardInterrupt):
        rec.restart(sign_first=True, changed_only=True)
    assert restarts() == 0
    out = rec.restart(sign_first=True, changed_only=True)
    assert out["restarted"] is True and restarts() == 1


def test_a_batch_that_published_nothing_clears_its_intent(monkeypatch, tmp_path):
    rec, _control, restarts = _activation_rig(monkeypatch, tmp_path, [
        [{"name": "alpha", "outcome": "unchanged"}],
    ])
    assert rec.restart(sign_first=True, changed_only=True)["restarted"] is False
    assert restarts() == 0
    assert not (tmp_path / "stack_reconcile_pending_activation.json").exists()


def test_preview_and_admission_options_belong_to_deploy_only(monkeypatch, capsys):
    reached = []

    class Recorder:
        def __init__(self, *_args, **_kwargs):
            pass

        def __getattr__(self, name):
            return lambda **kwargs: reached.append((name, kwargs)) or {"ok": True}

    monkeypatch.setattr(sr, "StackReconciler", Recorder)
    for argv in (["watchdog", "--plan", "--changed-only"], ["check", "--sign"],
                 ["watchdog", "--changed-only"], ["wait-ready", "--plan"]):
        assert sr.main(argv) == 1, argv
        assert "option_invalid" in capsys.readouterr().out
    assert reached == []
