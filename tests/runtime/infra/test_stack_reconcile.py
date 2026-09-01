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


def test_restart_uses_only_integrated_target(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(rec, "wait_ready", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(sr, "verify_named_executors", lambda names, sign_first=False: [])
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
    assert ("run", "user", "restart", "metnos.target") in fake.calls
    assert not any("metnos-http.service" in call for call in fake.calls if call[0] == "run")


def test_restart_refuses_when_target_is_not_installed(monkeypatch, tmp_path):
    fake = FakeSystemctl(target_loaded=False)
    rec = sr.StackReconciler(systemctl=fake, report_path=tmp_path / "report.json")
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(sr, "ReconcileLock", lambda: lock_type(tmp_path / "lock"))
    monkeypatch.setattr(rec, "require_quiescent", lambda: {"ok": True})
    monkeypatch.setattr(sr, "verify_named_executors", lambda names, sign_first=False: [])
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
