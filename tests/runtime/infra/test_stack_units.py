"""Static ownership and failure-containment contract for metnos.target."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
UNITS = ROOT / "install" / "units"
RUNTIME = ROOT / "runtime"

from install.phases import phase5_systemd as phase5
import services_registry as registry
import stack_reconcile as reconcile


def _read(name: str) -> str:
    return (UNITS / name).read_text()


def test_target_cannot_be_ready_before_composite_gate():
    unit = _read("metnos.target.tmpl")
    assert (
        "Requires=metnos-http.service metnos-i18n-translator.timer "
        "metnos-stack-ready.service"
    ) in unit
    assert "BindsTo=metnos-stack-ready.service" in unit
    assert "After=metnos-stack-ready.service" in unit


def test_ready_gate_orders_every_owned_runtime_component():
    unit = phase5._substitute(
        _read("metnos-stack-ready.service.tmpl"), 8770, "it")
    assert "Type=oneshot" in unit
    assert "stack_reconcile wait-ready" in unit
    assert f"--timeout {phase5.STACK_READY_PROBE_TIMEOUT_S}" in unit
    assert f"TimeoutStartSec={phase5.STACK_READY_SERVICE_TIMEOUT_S}" in unit
    assert phase5.STACK_ACTIVATION_TIMEOUT_S > phase5.STACK_READY_SERVICE_TIMEOUT_S
    assert "OnFailure=metnos-stack-quarantine.service" in unit
    for dependency in (
        "metnos-http.service", "metnos-side-display.service",
        "metnos-playwright.service", "metnos-telegram-daemon.service",
        "metnos-llm.service", "metnos-searxng.service",
        "metnos-photon.service", "metnos-durable-worker.service",
        "metnos-i18n-translator.timer",
    ):
        assert dependency in unit


def test_failure_quarantine_is_closed_and_stops_partial_stack():
    unit = _read("metnos-stack-quarantine.service.tmpl")
    assert "/usr/bin/systemctl --user stop" in unit
    assert "*" not in unit
    for dependency in (
        "metnos-http.service", "metnos-side-display.service",
        "metnos-playwright.service", "metnos-telegram-daemon.service",
        "metnos-llm.service", "metnos-searxng.service",
        "metnos-photon.service", "metnos-durable-worker.service",
        "metnos-i18n-translator.service",
        "metnos-i18n-translator.timer",
    ):
        assert dependency in unit


def test_service_templates_are_owned_by_target_not_default_individually():
    for name in (
        "metnos-http.service.tmpl",
        "metnos-playwright.service.tmpl",
        "metnos-telegram-daemon.service.tmpl",
        "metnos-searxng.service.tmpl",
        "metnos-photon.service.tmpl",
    ):
        unit = _read(name)
        assert "PartOf=metnos.target" in unit
        assert "WantedBy=default.target" not in unit


def test_http_service_delegates_service_scoped_birth_cgroups():
    unit = _read("metnos-http.service.tmpl")
    assert "Delegate=yes" in unit
    assert "DelegateSubgroup=metnos-birth-host" in unit
    assert "MemoryAccounting=yes" in unit
    assert "TasksAccounting=yes" in unit


def test_durable_worker_is_a_bounded_supervised_target_component():
    unit = _read("metnos-durable-worker.service.tmpl")
    assert "-m durable_workloads.service" in unit
    assert "PartOf=metnos.target" in unit
    assert "Before=metnos-stack-ready.service" in unit
    assert "Restart=on-failure" in unit
    assert "StartLimitBurst=3" in unit
    assert "TimeoutStopSec=45" in unit
    assert "KillMode=control-group" in unit
    assert "EnvironmentFile=" not in unit
    assert "Environment=METNOS_DURABLE_WORKLOADS_ENABLED=" not in unit
    assert "Environment=METNOS_EXECUTOR_PARALLEL=1" in unit
    assert "Environment=METNOS_DURABLE_WORKERS=" not in unit
    assert "strict shared parser" in unit


def test_telegram_unit_invokes_the_shipped_daemon_module():
    unit = _read("metnos-telegram-daemon.service.tmpl")
    assert "-m runtime.channels.daemon" in unit
    assert "runtime.telegram_daemon" not in unit


def test_watchdog_is_bounded_by_reconcile_circuit():
    service = _read("metnos-stack-watchdog.service.tmpl")
    timer = _read("metnos-stack-watchdog.timer.tmpl")
    assert "stack_reconcile watchdog" in service
    assert "OnActiveSec=3min" in timer
    assert "OnBootSec=" not in timer
    assert "OnUnitActiveSec=2min" in timer
    assert "PartOf=metnos.target" in timer


def test_i18n_timer_rearms_when_started_after_boot():
    timer = _read("metnos-i18n-translator.timer.tmpl")
    assert "OnActiveSec=30s" in timer
    assert "OnBootSec=" not in timer
    assert "OnUnitActiveSec=5min" in timer
    assert "PartOf=metnos.target" in timer


def test_catalog_target_reconcile_and_phase5_unit_sets_remain_in_parity():
    registry_units = set(registry.integrated_user_units())
    assert set(phase5.STACK_OWNED_OPTIONAL_UNITS) == (
        registry_units - {
            "metnos-http.service",
            "metnos-durable-worker.service",
            "metnos-i18n-translator.service",
            "metnos-i18n-translator.timer",
        }
    )
    assert set(reconcile.STACK_UNITS) == (
        registry_units | {"metnos-stack-watchdog.timer"}
    )
    # A supported system baseline is observed by the endpoint/service health
    # checks; its unused user alias is not a second required component.
    system_baseline_aliases = {
        target.unit for service in registry.catalog()
        if any(target.scope == "system" for target in service.targets)
        for target in service.targets if target.scope == "user"
    }
    assert set(reconcile.RUNTIME_COMPONENT_UNITS) == (
        registry_units - system_baseline_aliases
        - {"metnos-i18n-translator.service"}
    )
    rendered_control_units = {
        unit for _template, unit in phase5.STACK_UNIT_TEMPLATES
    }
    assert rendered_control_units == {
        reconcile.TARGET_UNIT,
        *reconcile.CONTROL_PLANE_UNITS,
        "metnos-stack-watchdog.timer",
        "metnos-i18n-translator.service",
        "metnos-i18n-translator.timer",
        "metnos-durable-worker.service",
    }

    target = _read("metnos.target.tmpl")
    catalog_target_units = {
        item.unit
        for service in registry.catalog() if service.integrated
        for item in service.targets if item.scope == "user"
    }
    assert all(unit in target for unit in catalog_target_units)


def test_rendered_units_pass_systemd_analyze(monkeypatch, tmp_path):
    analyzer = shutil.which("systemd-analyze")
    if not analyzer:
        pytest.skip("systemd-analyze unavailable")
    from install import llm_manager
    from install.phases import phase5_systemd as phase5

    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(ROOT))
    test_venv = tmp_path / "venv"
    (test_venv / "bin").mkdir(parents=True)
    (test_venv / "bin" / "python").symlink_to(Path(sys.executable))
    monkeypatch.setenv("METNOS_VENV", str(test_venv))
    monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("METNOS_USER_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("METNOS_USER_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(llm_manager, "find_completion_bin", lambda: None)

    names = {
        "metnos.target.tmpl": "metnos.target",
        "metnos-http.service.tmpl": "metnos-http.service",
        "metnos-playwright.service.tmpl": "metnos-playwright.service",
        "metnos-telegram-daemon.service.tmpl": "metnos-telegram-daemon.service",
        "metnos-stack-ready.service.tmpl": "metnos-stack-ready.service",
        "metnos-stack-quarantine.service.tmpl": "metnos-stack-quarantine.service",
        "metnos-stack-watchdog.service.tmpl": "metnos-stack-watchdog.service",
        "metnos-stack-watchdog.timer.tmpl": "metnos-stack-watchdog.timer",
        "metnos-i18n-translator.service.tmpl": "metnos-i18n-translator.service",
        "metnos-i18n-translator.timer.tmpl": "metnos-i18n-translator.timer",
        "metnos-durable-worker.service.tmpl": "metnos-durable-worker.service",
    }
    rendered = []
    for template, unit in names.items():
        body = phase5._substitute(_read(template), 8770, "it")
        body = body.replace("@BROWSERS_DIR@", str(tmp_path / "browsers"))
        path = tmp_path / unit
        path.write_text(body)
        rendered.append(str(path))
    display = tmp_path / "metnos-side-display.service"
    display.write_text((ROOT / "systemd" / "metnos-side-display.service").read_text())
    rendered.append(str(display))

    standard_paths = subprocess.run(
        [analyzer, "unit-paths", "--user"], capture_output=True,
        text=True, check=True,
    ).stdout.splitlines()
    result = subprocess.run(
        [analyzer, "verify", "--user", "--man=no", *rendered],
        capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "SYSTEMD_UNIT_PATH": ":".join((str(tmp_path), *standard_paths)),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
