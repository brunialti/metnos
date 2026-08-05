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
    assert {"http", "playwright", "llm", "searxng", "photon", "i18n"} <= {
        service.key for service in services
    }
    assert "issues" not in {service.key for service in services}
    assert registry.get("i18n").required is True
    assert "fast.micro" in registry.get("llm").description
    assert "fast.fidelity" in registry.get("i18n").description
    assert registry.get("llm").endpoint_env == "METNOS_LLM_URL"
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


def test_template_renders_catalog_and_missing_services():
    rows = []
    for spec in registry.catalog():
        row = {
            **registry.snapshot_one(spec, probe_endpoint=False),
            "load_state": "not-found", "installed": False,
            "actionable": False, "status": "missing",
        }
        rows.append(row)
    from i18n import language_context

    with language_context("it"):
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

    with language_context("en"):
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
