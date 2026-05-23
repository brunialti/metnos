# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 5 — Systemd services (user level).

Writes user-level systemd units from the templates in
``install/units/*.service.tmpl``, runs ``systemctl --user
daemon-reload``, enables and starts ``metnos-http.service``, probes
its health endpoint, and conditionally enables
``metnos-telegram-daemon.service`` if phase 4 collected a Telegram
token.

User-level units (vs system-level) means **no sudo is required**.
The service runs as the invoking user, dies when the session ends
unless ``loginctl enable-linger`` is set (we print the suggestion
but do not run it ourselves — it's a single sudo command the user
should run consciously).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import state, ui


def _systemd_user_dir() -> Path:
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_dir() -> Path:
    return Path(os.environ.get("METNOS_REPO_DIR", Path.cwd()))


def _substitute(template: str, port: int) -> str:
    """Replace @VAR@ placeholders in unit template content."""
    repl = {
        "@VENV@":       os.environ.get("METNOS_VENV", str(Path.home() / ".local" / "share" / "metnos" / ".venv")),
        "@HOME_DIR@":   os.environ.get("METNOS_HOME", str(Path.home() / ".local" / "share" / "metnos")),
        "@CONFIG_DIR@": os.environ.get("METNOS_CONFIG", str(Path.home() / ".config" / "metnos")),
        "@STATE_DIR@":  os.environ.get("METNOS_STATE", str(Path.home() / ".local" / "state" / "metnos")),
        "@REPO_DIR@":   str(_repo_dir()),
        "@PORT@":       str(port),
    }
    for k, v in repl.items():
        template = template.replace(k, v)
    return template


def _install_unit(template_path: Path, dest_name: str, port: int) -> bool:
    """Render one template into the user systemd dir."""
    if not template_path.exists():
        ui.warn(f"missing template: {template_path}")
        return False
    rendered = _substitute(template_path.read_text(), port)
    dest = _systemd_user_dir() / dest_name
    dest.write_text(rendered)
    ui.ok(f"wrote {dest}")
    return True


def _systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=30, check=check,
    )


def _runtime_module_importable(module: str) -> bool:
    """Use the venv's python to test if a module imports cleanly.

    Avoids the failure mode where the systemd unit starts a python
    process that ImportError's immediately, leaving systemctl in
    activating → failed loop.
    """
    venv_py = Path(os.environ.get("METNOS_VENV", "")) / "bin" / "python"
    repo = os.environ.get("METNOS_REPO_DIR", "")
    if not venv_py.exists() or not repo:
        return False
    env = os.environ.copy()
    env["PYTHONPATH"] = repo + ":" + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [str(venv_py), "-c", f"import {module}"],
        env=env, capture_output=True, text=True, timeout=15,
    )
    return r.returncode == 0


def _wait_for_http(port: int, *, timeout_s: int = 20) -> bool:
    """Poll http://127.0.0.1:<port>/agent/health until 200 or timeout."""
    import httpx  # already in venv
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/agent/health"
    last_err = ""
    with ui.progress() as p:
        task = p.add_task(f"Probing {url}", total=timeout_s)
        while time.time() < deadline:
            try:
                r = httpx.get(url, timeout=2.0)
                if r.status_code == 200:
                    return True
            except httpx.RequestError as e:
                last_err = f"{type(e).__name__}: {e}"
            elapsed = timeout_s - (deadline - time.time())
            p.update(task, completed=elapsed)
            time.sleep(0.5)
    if last_err:
        ui.warn(f"last error: {last_err}")
    return False


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner("Phase 5 — Systemd services",
              "Install user units · enable · health-probe")

    if not shutil.which("systemctl"):
        ui.fail("systemctl not found — this installer requires systemd (Linux user session).")

    # Look up port / Telegram choice from phase 4
    phase4 = state.load(4)
    port = (phase4.notes.get("http_port") if phase4 else None) or 8770
    telegram_enabled = bool(phase4 and phase4.notes.get("telegram"))

    notes["http_port"] = port
    notes["telegram_unit_installed"] = telegram_enabled

    # Locate templates
    tmpl_dir = Path(__file__).resolve().parent.parent / "units"
    if not tmpl_dir.exists():
        ui.fail(f"templates dir missing: {tmpl_dir}")

    # 1. Install metnos-http.service
    ui.step(f"Installing metnos-http.service (port {port})")
    _install_unit(tmpl_dir / "metnos-http.service.tmpl", "metnos-http.service", port)

    # 2. Optionally install telegram daemon (only if importable)
    telegram_module_ok = False
    if telegram_enabled:
        if _runtime_module_importable("runtime.telegram_daemon"):
            ui.step("Installing metnos-telegram-daemon.service")
            _install_unit(tmpl_dir / "metnos-telegram-daemon.service.tmpl",
                          "metnos-telegram-daemon.service", port)
            telegram_module_ok = True
        else:
            ui.warn("runtime.telegram_daemon not importable — skipping Telegram unit. "
                    "Once the module ships, re-run `python -m install --force-phase 5`.")
    notes["telegram_unit_installed"] = telegram_module_ok

    # 3. daemon-reload
    ui.step("Reloading systemd user unit catalog")
    _systemctl_user("daemon-reload")
    ui.ok("daemon-reload OK")

    # 4. Enable + start metnos-http — only if module is importable
    if not _runtime_module_importable("runtime.metnos_http_server"):
        ui.warn("runtime.metnos_http_server not importable in the venv. "
                "Unit file is in place, but enable is skipped to avoid a "
                "failing systemd loop. Re-run phase 5 once runtime/ ships.")
        notes["http_enabled"] = False
        notes["http_healthy"] = False
    else:
        ui.step("Enabling and starting metnos-http.service")
        r = _systemctl_user("enable", "--now", "metnos-http.service")
        if r.returncode != 0:
            ui.warn(f"systemctl enable failed: {r.stderr.strip()}")
            notes["http_enabled"] = False
        else:
            ui.ok("metnos-http enabled + started")
            notes["http_enabled"] = True

        # 5. Health probe (only if start succeeded)
        if notes["http_enabled"]:
            ui.step(f"Probing HTTP health endpoint (up to 20s)")
            if _wait_for_http(port):
                ui.ok(f"http://127.0.0.1:{port}/agent/health responds 200")
                notes["http_healthy"] = True
            else:
                ui.warn(f"health endpoint did not respond within 20s — check `systemctl --user status metnos-http`")
                notes["http_healthy"] = False

    # 6. Telegram (optional, only if unit was installed)
    if telegram_module_ok:
        ui.step("Starting metnos-telegram-daemon.service")
        r = _systemctl_user("enable", "--now", "metnos-telegram-daemon.service")
        if r.returncode != 0:
            ui.warn(f"telegram daemon failed to start: {r.stderr.strip()}")
            notes["telegram_started"] = False
        else:
            ui.ok("telegram daemon running")
            notes["telegram_started"] = True

    # 7. Linger advisory
    ui.console().print()
    ui.console().print("  [bold]Tip:[/bold] to keep Metnos running across reboots even when "
                       "you don't log in, run [cyan]sudo loginctl enable-linger $USER[/cyan].")

    return notes
