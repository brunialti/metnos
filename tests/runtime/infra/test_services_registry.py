"""Contratto del gestore centralizzato dei servizi Metnos."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

import services_registry as registry
from http_render import render_template


def _show(*, unit: str, load: str = "loaded", active: str = "active"):
    stdout = "\n".join((
        f"Id={unit}",
        f"LoadState={load}",
        f"ActiveState={active}",
        "SubState=running" if active == "active" else "SubState=dead",
        "MainPID=42",
        "ActiveEnterTimestamp=Wed 2026-07-15 10:00:00 CEST",
        "UnitFileState=enabled",
    ))
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def test_catalog_keys_and_targets_are_closed_and_unique():
    services = registry.catalog()
    assert len({service.key for service in services}) == len(services)
    assert {
        "http", "playwright", "llm", "searxng", "photon", "i18n",
        "durable_workloads",
    } <= {
        service.key for service in services
    }
    assert "issues" not in {service.key for service in services}
    assert registry.get("i18n").required is True
    assert "fast.micro" in registry.get("llm").description
    # Il traduttore usa il workload `translation.i18n`, che il registro
    # risolve su `wise`: la descrizione citava un livello fast che non ha
    # alcun carico (ADR 0207, emendamento al censimento).
    assert "wise" in registry.get("i18n").description
    assert registry.get("llm").endpoint_env == "METNOS_LLM_URL"
    assert registry.get("durable_workloads").health_policy == "application"
    for service in services:
        assert service.targets
        assert all(target.scope in {"system", "user"}
                   for target in service.targets)
        assert all(target.unit.endswith((".service", ".timer"))
                   for target in service.targets)


def test_endpoints_have_one_canonical_default(monkeypatch):
    monkeypatch.delenv("METNOS_SEARXNG_URL", raising=False)
    assert registry.endpoint("searxng") == "http://127.0.0.1:8888"
    monkeypatch.setenv("METNOS_SEARXNG_URL", "http://search.internal:9999/")
    assert registry.endpoint("searxng") == "http://search.internal:9999"
    assert registry.endpoint("not_in_catalog") == ""


def test_polkit_rule_is_derived_and_strict():
    rule = registry.render_polkit_rule("metnos-test")
    assert 'subject.user !== "metnos-test"' in rule
    for unit in registry.system_units():
        assert f'"{unit}"' in rule
    assert '"start", "stop", "restart"' in rule
    assert "*" not in rule
    assert "manage-unit-files" not in rule


def test_resolve_target_uses_loaded_alternative(monkeypatch):
    def fake_run(cmd, **kwargs):
        unit = next(arg for arg in cmd if arg.endswith(".service"))
        if "--user" in cmd:
            return _show(unit=unit, load="not-found", active="inactive")
        return _show(unit=unit)

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    row = registry.resolve_target(registry.get("http"))
    assert row["scope"] == "system"
    assert row["active_state"] == "active"


def test_resolve_target_prefers_active_system_baseline_over_loaded_user_unit(
        monkeypatch):
    def fake_run(cmd, **kwargs):
        unit = next(arg for arg in cmd if arg.endswith(".service"))
        if "--user" in cmd:
            return _show(unit=unit, load="loaded", active="inactive")
        return _show(unit=unit, load="loaded", active="active")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    row = registry.resolve_target(registry.get("http"))
    assert row["scope"] == "system"
    assert row["active_state"] == "active"


def test_llm_resolves_supported_system_installation(monkeypatch):
    """Upgrade hosts may still own llama-server as a system unit."""
    def fake_run(cmd, **kwargs):
        unit = next(arg for arg in cmd if arg.endswith(".service"))
        if unit == "metnos-llm.service":
            return _show(unit=unit, load="not-found", active="inactive")
        if unit == "llama-server.service" and "--user" not in cmd:
            return _show(unit=unit, load="loaded", active="active")
        raise AssertionError(f"unexpected target: {cmd}")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    row = registry.resolve_target(registry.get("llm"))
    assert row["unit"] == "llama-server.service"
    assert row["scope"] == "system"
    assert row["active_state"] == "active"


def test_durable_worker_disabled_by_policy_is_running_and_healthy(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec, **_kwargs: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "main_pid": "42",
            "active_since": "", "unit_state": "enabled",
        },
    )
    import durable_workloads.service as durable_service

    monkeypatch.setattr(durable_service, "health_snapshot", lambda: {
        "state": "degraded", "enabled": False,
        "reason_code": "feature_disabled",
    })
    row = registry.snapshot_one(registry.get("durable_workloads"))
    assert (row["status"], row["healthy"], row["health_detail"]) == (
        "running", True, "feature_disabled",
    )
    assert row["feature_converged"] is True
    assert row["health_message_key"] == (
        "UI_SERVICES_LRE_HEALTH_FEATURE_DISABLED"
    )


def test_durable_worker_detects_config_process_mismatch(monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec, **_kwargs: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "main_pid": "42",
            "active_since": "", "unit_state": "enabled",
        },
    )
    import durable_workloads.service as durable_service

    monkeypatch.setattr(durable_service, "health_snapshot", lambda: {
        "state": "ready", "enabled": True, "reason_code": "none",
    })

    row = registry.snapshot_one(registry.get("durable_workloads"))

    assert row["status"] == "degraded"
    assert row["healthy"] is False
    assert row["feature_converged"] is False
    assert row["health_detail"] == "feature_state_mismatch"


def _installed_lre(monkeypatch):
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
        },
    )


def test_lre_feature_enable_is_persisted_and_restarts_exact_unit(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    _installed_lre(monkeypatch)
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert registry.configure_lre_feature(True) == (True, "")
    assert registry.read_feature_configuration(environ={}).enabled is True
    assert seen == [[
        "systemctl", "--user", "--no-block", "restart",
        "metnos-durable-worker.service",
    ]]


def test_lre_feature_environment_override_cannot_be_changed_from_ui(
        monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_DURABLE_WORKLOADS_ENABLED", "0")
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    _installed_lre(monkeypatch)
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("systemctl must not run")
        ),
    )

    ok, detail = registry.configure_lre_feature(True)

    assert ok is False
    assert "environment" in detail
    assert registry.read_feature_configuration(environ={}).enabled is False


def test_lre_feature_enable_rolls_back_if_restart_is_rejected(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    _installed_lre(monkeypatch)
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="restart rejected",
        ),
    )

    assert registry.configure_lre_feature(True) == (
        False, "restart rejected",
    )
    assert registry.read_feature_configuration(environ={}).enabled is False


def test_lre_feature_disable_stays_closed_if_restart_is_rejected(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(True)
    _installed_lre(monkeypatch)
    seen = []

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        code = 1 if "restart" in cmd else 0
        return subprocess.CompletedProcess(
            cmd, code, stdout="", stderr="restart rejected" if code else "",
        )

    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert registry.configure_lre_feature(False) == (
        False, "restart rejected",
    )
    assert registry.read_feature_configuration(environ={}).enabled is False
    assert [command[-2] for command in seen] == ["restart", "stop"]


def test_lre_feature_control_rejects_a_target_outside_the_catalog(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec: {
            "unit": "unrelated.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
        },
    )
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("systemctl must not run")
        ),
    )

    ok, detail = registry.configure_lre_feature(True)

    assert ok is False
    assert detail == "LRE service unit is not installed"
    assert registry.read_feature_configuration(environ={}).enabled is False


def test_user_manager_gets_explicit_bus_environment(monkeypatch):
    monkeypatch.setenv("METNOS_SERVICE_USER", "test-user")
    monkeypatch.setattr(
        registry.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1234),
    )
    cmd, env = registry._systemctl(
        registry.ServiceTarget("metnos-test.service", "user"), "show",
    )
    assert cmd[:2] == ["systemctl", "--user"]
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1234"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1234/bus"


def test_control_rejects_unknown_unit_without_systemctl(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("systemctl must not run")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    assert registry.control("../../ssh", "restart") == (
        False, "invalid service action",
    )
    assert called is False


def test_required_i18n_timer_cannot_be_stopped(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("systemctl must not run")

    monkeypatch.setattr(
        registry, "resolve_target", lambda _: {
            "unit": "metnos-i18n-translator.timer", "scope": "user",
            "load_state": "loaded", "active_state": "active",
        },
    )
    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert registry.control("i18n", "stop") == (
        False, "service action is unavailable from this control plane",
    )
    assert called is False
    assert registry.desired_state("i18n") == "running"


def test_integrated_component_control_stays_on_catalog_target(monkeypatch):
    monkeypatch.setattr(
        registry, "resolve_target", lambda _: {
            "unit": "metnos-playwright.service", "scope": "user",
            "load_state": "loaded",
        },
    )
    recorded = []
    monkeypatch.setattr(
        registry, "_record_desired_state",
        lambda key, state: recorded.append((key, state)),
    )
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    assert registry.control("playwright", "restart") == (True, "")
    assert seen["cmd"][-3:] == [
        "--no-block", "restart", "metnos-playwright.service",
    ]
    assert recorded == [("playwright", "running")]


def test_control_is_non_blocking_and_uses_resolved_target(monkeypatch):
    spec = registry.ServiceSpec(
        "standalone", "Standalone", "Standalone test service", "Test",
        (registry.ServiceTarget("standalone.service", "user"),),
    )
    monkeypatch.setattr(
        registry, "resolve_target", lambda _: {
            "unit": "standalone.service", "scope": "user",
            "load_state": "loaded",
        },
    )
    monkeypatch.setitem(registry._BY_KEY, "standalone", spec)
    monkeypatch.setattr(registry, "_record_desired_state", lambda *_: None)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    ok, detail = registry.control("standalone", "restart")
    assert ok is True and detail == ""
    assert seen["cmd"][-3:] == [
        "--no-block", "restart", "standalone.service",
    ]


def test_template_renders_catalog_and_missing_services(monkeypatch):
    rows = []
    for spec in registry.catalog():
        row = {
            **registry.snapshot_one(spec, probe_endpoint=False),
            "load_state": "not-found", "installed": False,
            "actionable": False, "status": "missing",
        }
        rows.append(row)
    import i18n

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "it")
    html_it = render_template(
        "services.html", services=registry.localized(rows, "it"), notice="",
    )
    assert "SearXNG" in html_it
    assert "Server geografico" in html_it
    assert "Stato operativo dei servizi" in html_it
    assert "Non installato" in html_it
    assert "missing:UI_SERVICES" not in html_it
    assert html_it.index("<h2>Nucleo</h2>") < html_it.index(
        "<h2>Navigazione web</h2>",
    )

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
    html_en = render_template(
        "services.html", services=registry.localized(rows, "en"), notice="",
    )
    assert "Geo server" in html_en
    assert "Service operational status" in html_en
    assert "Not installed" in html_en
    assert "missing:UI_SERVICES" not in html_en


def test_template_distinguishes_start_from_restart():
    common = {
        "group": "Test", "description": "Service test", "scope": "user",
        "sub_state": "dead", "healthy": None, "health_detail": "",
        "installed": True, "unit": "test.service", "main_pid": "0",
        "managed_by": "", "desired_state": "running",
        "in_desired_state": False, "active_since": "",
        "actionable": True, "allowed_actions": ["start", "stop", "restart"],
    }
    rows = [
        {
            **common, "key": "stopped", "label": "Stopped service",
            "status": "stopped", "active_state": "inactive",
        },
        {
            **common, "key": "running", "label": "Running service",
            "status": "running", "active_state": "active",
        },
    ]
    html = render_template(
        "services.html", services=rows, notice="", notifications=[],
    )
    stopped = html.split('id="service-stopped"', 1)[1].split("</article>", 1)[0]
    running = html.split('id="service-running"', 1)[1].split("</article>", 1)[0]
    assert "/admin/services/stopped/start" in stopped
    assert "/admin/services/stopped/stop" not in stopped
    assert "/admin/services/stopped/restart" not in stopped
    assert "/admin/services/running/start" not in running
    assert "/admin/services/running/stop" in running
    assert "/admin/services/running/restart" in running


def test_template_exposes_the_closed_lre_feature_control(monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec, **_kwargs: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "main_pid": "42",
            "active_since": "", "unit_state": "enabled",
        },
    )
    import durable_workloads.service as durable_service

    monkeypatch.setattr(durable_service, "health_snapshot", lambda: {
        "state": "degraded", "enabled": False,
        "reason_code": "feature_disabled",
    })
    row = registry.snapshot_one(registry.get("durable_workloads"))
    import i18n

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "it")
    italian = render_template(
        "services.html", services=registry.localized([row], "it"),
        notice="", notifications=[],
    )
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
    english = render_template(
        "services.html", services=registry.localized([row], "en"),
        notice="", notifications=[],
    )

    assert "/admin/services/durable_workloads/feature/enable" in italian
    assert "Attiva LRE" in italian
    assert "feature_disabled" not in italian
    assert "Enable LRE" in english
    assert "missing:UI_SERVICES" not in italian + english


def test_snapshot_failure_is_isolated(monkeypatch):
    broken = registry.catalog()[0]

    def fake_snapshot(spec, *, probe_endpoint=True, deadline_at=None):
        if spec is broken:
            raise RuntimeError("broken probe")
        return {"key": spec.key, "installed": True}

    monkeypatch.setattr(registry, "snapshot_one", fake_snapshot)
    rows = registry.snapshots()
    assert len(rows) == len(registry.catalog())
    assert rows[0]["status"] == "failed"
    assert rows[1]["key"] == registry.catalog()[1].key


def test_snapshot_pool_is_reused(monkeypatch):
    monkeypatch.setattr(registry, "snapshot_one",
                        lambda spec, *, probe_endpoint=True, deadline_at=None:
                        {"key": spec.key, "installed": True})
    first = registry._snapshot_pool()
    registry.snapshots(probe_endpoints=False)
    second = registry._snapshot_pool()
    assert first is second
