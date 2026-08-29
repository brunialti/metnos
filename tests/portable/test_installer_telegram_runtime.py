"""Portable regression for upgrading historical Telegram service units."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
PHASE5_PATH = ROOT / "install" / "phases" / "phase5_systemd.py"


def _load_phase5(monkeypatch):
    for name in (
        "install.i18n", "install.llm_manager", "install.state", "install.ui",
    ):
        module = ModuleType(name)
        if name == "install.ui":
            module.ok = lambda *_args, **_kwargs: None
        monkeypatch.setitem(sys.modules, name, module)
    name = "install.phases._portable_phase5_telegram_runtime"
    spec = importlib.util.spec_from_file_location(name, PHASE5_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_unit_body_but_binds_managed_runtime(
        monkeypatch, tmp_path) -> None:
    phase5 = _load_phase5(monkeypatch)
    units = tmp_path / "units"
    units.mkdir()
    unit = units / "metnos-telegram-daemon.service"
    historical = (
        "[Service]\n"
        "WorkingDirectory=/opt/metnos/runtime\n"
        "ExecStart=/opt/suprastructure/.venv/bin/python -m channels.daemon\n"
        "TimeoutStopSec=180\n"
    )
    unit.write_text(historical)
    repo = tmp_path / "metnos"
    venv = repo / ".venv"
    monkeypatch.setattr(phase5, "_systemd_user_dir", lambda: units)
    monkeypatch.setattr(phase5, "_repo_dir", lambda: repo)
    monkeypatch.setattr(phase5, "_venv_dir", lambda: venv)

    assert phase5._install_telegram_runtime_dropin() is True
    assert unit.read_text() == historical
    assert (
        units
        / "metnos-telegram-daemon.service.d"
        / "20-metnos-runtime.conf"
    ).read_text() == (
        "[Service]\n"
        "ExecStart=\n"
        f"ExecStart={venv / 'bin' / 'python'} -m runtime.channels.daemon\n"
        f"WorkingDirectory={repo}\n"
        f"Environment=PYTHONPATH={repo}\n"
        f"Environment=METNOS_INSTALL_ROOT={repo}\n"
    )


def test_phase5_wires_runtime_binding_only_after_unit_installation() -> None:
    tree = ast.parse(PHASE5_PATH.read_text())
    run = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    calls = [
        node.func.id for node in ast.walk(run)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("_install_telegram_runtime_dropin") == 1
    assert calls.index("_install_optional_unit") < calls.index(
        "_install_telegram_runtime_dropin"
    )
