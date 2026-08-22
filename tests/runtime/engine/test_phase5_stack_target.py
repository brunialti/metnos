"""Installer fresh/upgrade behavior for the integrated user target."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]

from install.phases import phase5_systemd as phase5


def _wire(monkeypatch, tmp_path, *, legacy: bool, telegram: bool = False):
    calls: list[tuple[str, ...]] = []
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setenv("METNOS_USER_CONFIG", str(tmp_path / "config"))
    monkeypatch.setattr(phase5, "_systemd_user_dir", lambda: units)
    monkeypatch.setattr(phase5, "_repo_dir", lambda: ROOT)
    monkeypatch.setattr(phase5, "_runtime_module_importable", lambda _module: True)
    monkeypatch.setattr(phase5, "_legacy_system_http_active", lambda: legacy)
    monkeypatch.setattr(phase5, "_wait_for_http", lambda _port: True)
    monkeypatch.setattr(
        phase5.state, "load",
        lambda _phase: SimpleNamespace(notes={
            "http_port": 8770, "locale": "it", "telegram": telegram,
        }),
    )
    monkeypatch.setattr(phase5.llm_manager, "find_completion_bin", lambda: None)
    monkeypatch.setattr(
        phase5.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name in {"systemctl", "Xvfb"} else None,
    )

    def systemctl(*args, **_kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(phase5, "_systemctl_user", systemctl)
    for name in ("banner", "step", "ok", "warn"):
        monkeypatch.setattr(phase5.ui, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        phase5.ui, "console",
        lambda: SimpleNamespace(print=lambda *_args, **_kwargs: None),
    )
    return calls


def test_fresh_install_enables_only_integrated_target(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path, legacy=False)
    notes = phase5.run(SimpleNamespace())
    units = tmp_path / "units"
    assert notes["target_enabled"] is True
    assert notes["watchdog_enabled"] is True
    assert notes["migration_required"] is False
    assert (units / "metnos.target").is_file()
    assert (units / "metnos-stack-ready.service").is_file()
    assert (units / "metnos-stack-watchdog.timer").is_file()
    assert (units / "metnos-i18n-translator.service").is_file()
    assert (units / "metnos-i18n-translator.timer").is_file()
    lre_config = tmp_path / "config" / "lre.env"
    assert lre_config.is_file()
    assert "METNOS_DURABLE_WORKLOADS_ENABLED=0" in lre_config.read_text()
    assert notes["lre_config_created"] is True
    assert notes["lre_enabled_by_default"] is False
    assert notes["i18n_translator_enabled"] is True
    assert (
        units / "metnos-side-display.service.d" / "10-metnos-target.conf"
    ).is_file()
    assert ("enable", "--now", "metnos.target") in calls
    assert ("enable", "--now", "metnos-http.service") not in calls


def test_upgrade_with_active_system_http_installs_but_does_not_cut_over(
        monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path, legacy=True)
    notes = phase5.run(SimpleNamespace())
    assert notes["target_enabled"] is False
    assert notes["migration_required"] is True
    assert notes["http_healthy"] is True
    assert notes["watchdog_enabled"] is True
    assert (tmp_path / "units" / "metnos.target").is_file()
    assert ("enable", "--now", "metnos.target") not in calls
    assert (
        "add-wants", "default.target", "metnos-stack-watchdog.timer"
    ) in calls
    assert ("start", "metnos-stack-watchdog.timer") in calls
    assert ("enable", "--now", "metnos-i18n-translator.timer") in calls
    assert notes["i18n_translator_enabled"] is True
    assert not any("stop" in call or "disable" in call for call in calls)


def test_upgrade_preserves_the_existing_lre_feature_choice(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path, legacy=True)
    path = tmp_path / "config" / "lre.env"
    path.parent.mkdir(parents=True)
    path.write_text("METNOS_DURABLE_WORKLOADS_ENABLED=1\n")

    notes = phase5.run(SimpleNamespace())

    assert notes["lre_config_created"] is False
    assert path.read_text() == "METNOS_DURABLE_WORKLOADS_ENABLED=1\n"
    assert ("enable", "--now", "metnos.target") not in calls


def test_upgrade_records_migration_even_when_target_venv_is_not_ready(
        monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path, legacy=True)
    monkeypatch.setattr(phase5, "_runtime_module_importable", lambda _module: False)

    notes = phase5.run(SimpleNamespace())

    assert notes["migration_required"] is True
    assert notes["target_enabled"] is False
    assert notes["http_enabled"] is True
    assert notes["http_healthy"] is True
    assert notes["watchdog_enabled"] is False
    assert notes["i18n_translator_enabled"] is False
    assert ("enable", "--now", "metnos.target") not in calls
    assert not any("metnos-stack-watchdog.timer" in call for call in calls)


def test_upgrade_preserves_existing_optional_unit_bodies(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path, legacy=True, telegram=True)
    units = tmp_path / "units"
    existing = {
        "metnos-side-display.service": "local display tuning\n",
        "metnos-telegram-daemon.service": "local telegram tuning\n",
    }
    for name, body in existing.items():
        (units / name).write_text(body)

    notes = phase5.run(SimpleNamespace())

    assert notes["migration_required"] is True
    for name, body in existing.items():
        assert (units / name).read_text() == body
        assert (
            units / f"{name}.d" / "10-metnos-target.conf"
        ).is_file()
    assert ("enable", "--now", "metnos-telegram-daemon.service") in calls


def test_legacy_system_http_probe_fails_closed(monkeypatch):
    monkeypatch.setattr(
        phase5, "_systemctl_system",
        lambda *_args: subprocess.CompletedProcess(
            [], 1, stdout="", stderr="manager unavailable",
        ),
    )
    assert phase5._legacy_system_http_active() is True

    monkeypatch.setattr(
        phase5, "_systemctl_system",
        lambda *_args: subprocess.CompletedProcess(
            [], 0, stdout="LoadState=loaded\n", stderr="",
        ),
    )
    assert phase5._legacy_system_http_active() is True


def test_legacy_system_http_probe_allows_only_proven_inactive_or_missing(
        monkeypatch):
    states = iter((
        "LoadState=loaded\nActiveState=inactive\n",
        "LoadState=not-found\nActiveState=inactive\n",
        "LoadState=loaded\nActiveState=deactivating\n",
    ))
    monkeypatch.setattr(
        phase5, "_systemctl_system",
        lambda *_args: subprocess.CompletedProcess(
            [], 0, stdout=next(states), stderr="",
        ),
    )
    assert phase5._legacy_system_http_active() is False
    assert phase5._legacy_system_http_active() is False
    assert phase5._legacy_system_http_active() is True


def test_runtime_import_probe_uses_resolved_default_venv(monkeypatch, tmp_path):
    python = tmp_path / "bin" / "python"
    python.parent.mkdir()
    python.write_text("")
    seen = {}

    monkeypatch.setattr(phase5, "_venv_dir", lambda: tmp_path)
    monkeypatch.delenv("METNOS_INSTALL_ROOT", raising=False)
    monkeypatch.setattr(phase5, "_repo_dir", lambda: ROOT)

    def run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(phase5.subprocess, "run", run)

    assert phase5._runtime_module_importable("runtime.metnos_http_server")
    assert seen["command"][0] == str(python)
    assert seen["env"]["PYTHONPATH"].split(":", 1)[0] == str(ROOT)
