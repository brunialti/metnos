# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 5 — Systemd services (user level).

Writes user-level systemd units from the templates in
``install/units/*``, runs ``systemctl --user daemon-reload``, enables and
starts the integrated ``metnos.target``, probes its composite readiness,
conditionally enables
``metnos-telegram-daemon.service`` if phase 4 collected a Telegram
token, and enables the ``metnos-i18n-translator.timer`` (lazy
translation of newly-added i18n keys).

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

from .. import llm_manager, state, ui


STACK_UNIT_TEMPLATES = (
    ("metnos.target.tmpl", "metnos.target"),
    ("metnos-stack-ready.service.tmpl", "metnos-stack-ready.service"),
    ("metnos-stack-quarantine.service.tmpl", "metnos-stack-quarantine.service"),
    ("metnos-stack-watchdog.service.tmpl", "metnos-stack-watchdog.service"),
    ("metnos-stack-watchdog.timer.tmpl", "metnos-stack-watchdog.timer"),
)
STACK_OWNED_OPTIONAL_UNITS = (
    "metnos-side-display.service",
    "metnos-playwright.service",
    "metnos-telegram-daemon.service",
    "metnos-llm.service",
    "metnos-searxng.service",
    "metnos-photon.service",
    "cloudflared-metnos-chat.service",
    "metnos-issues-sidecar.service",
    "metnos-i18n-translator.service",
    "metnos-i18n-translator.timer",
)


def _systemd_user_dir() -> Path:
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_dir() -> Path:
    return Path(os.environ.get("METNOS_INSTALL_ROOT", Path.cwd()))


def _venv_dir() -> Path:
    configured = os.environ.get("METNOS_VENV")
    if configured:
        return Path(configured)
    return Path.home() / ".local" / "share" / "metnos" / ".venv"


def _completion_env_line() -> str:
    """Env line for the byte-deterministic describe path.

    The managed install extracts ``llama-completion`` from the same
    llama.cpp release archive as ``llama-server`` (version-aligned by
    construction). If found, expose it to the runtime via
    ``METNOS_LLAMACPP_COMPLETION_BIN``; otherwise (wired to an existing
    endpoint, or an old release without the binary) leave an honest
    comment — the runtime falls back to HTTP generation and reports
    ``meta.deterministic=false`` (§2.8).
    """
    comp = llm_manager.find_completion_bin()
    if comp:
        return f"Environment=METNOS_LLAMACPP_COMPLETION_BIN={comp}"
    return ("# no managed llama-completion found — describe_entries falls "
            "back to HTTP generation (meta.deterministic=false)")


def _substitute(template: str, port: int, lang: str) -> str:
    """Replace @VAR@ placeholders in unit template content."""
    repl = {
        "@VENV@":       str(_venv_dir()),
        "@DATA_DIR@":   os.environ.get("METNOS_USER_DATA", str(Path.home() / ".local" / "share" / "metnos")),
        "@CONFIG_DIR@": os.environ.get("METNOS_USER_CONFIG", str(Path.home() / ".config" / "metnos")),
        "@STATE_DIR@":  os.environ.get("METNOS_USER_STATE", str(Path.home() / ".local" / "state" / "metnos")),
        "@REPO_DIR@":   str(_repo_dir()),
        "@PORT@":       str(port),
        "@LANG@":       lang,
        "@COMPLETION_ENV@": _completion_env_line(),
    }
    for k, v in repl.items():
        template = template.replace(k, v)
    return template


def _install_unit(template_path: Path, dest_name: str, port: int, lang: str) -> bool:
    """Render one template into the user systemd dir."""
    if not template_path.exists():
        ui.warn(f"missing template: {template_path}")
        return False
    rendered = _substitute(template_path.read_text(), port, lang)
    dest = _systemd_user_dir() / dest_name
    dest.write_text(rendered)
    ui.ok(f"wrote {dest}")
    return True


def _install_optional_unit(template_path: Path, dest_name: str,
                           port: int, lang: str) -> bool:
    """Install a missing optional unit without replacing local tuning.

    Upgrade hosts may already have intentionally customized companion units.
    Their integration contract is expressed by the narrow target drop-in
    below; replacing the service body first would defeat that preservation.
    Core HTTP/target units continue to be rendered from the current source.
    """
    dest = _systemd_user_dir() / dest_name
    if dest.exists():
        ui.ok(f"preserved existing {dest}")
        return True
    return _install_unit(template_path, dest_name, port, lang)


def _install_stack_ownership_dropin(unit_name: str) -> bool:
    """Attach an existing optional unit without replacing its local body."""
    unit = _systemd_user_dir() / unit_name
    if not unit.exists():
        return False
    dropin_dir = _systemd_user_dir() / f"{unit_name}.d"
    dropin_dir.mkdir(parents=True, exist_ok=True)
    dropin = dropin_dir / "10-metnos-target.conf"
    dropin.write_text(
        "[Unit]\n"
        "PartOf=metnos.target\n"
        "Before=metnos-stack-ready.service\n"
    )
    ui.ok(f"wrote {dropin}")
    return True


def _systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=30, check=check,
    )


def _systemctl_system(*args: str) -> subprocess.CompletedProcess:
    """Read-only system-manager adapter used to detect the legacy HTTP unit."""
    return subprocess.run(
        ["systemctl", *args], capture_output=True, text=True,
        timeout=30, check=False,
    )


def _legacy_system_http_active() -> bool:
    """Fail closed unless the system manager proves no legacy listener."""
    result = _systemctl_system(
        "show", "metnos-http.service",
        "--property=LoadState,ActiveState",
    )
    if result.returncode != 0:
        return True
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines()
        if "=" in line
    )
    load_state = values.get("LoadState", "")
    active_state = values.get("ActiveState", "")
    if not load_state or not active_state:
        return True
    if load_state == "not-found":
        return False
    if load_state != "loaded":
        return True
    if active_state in {"inactive", "failed"}:
        return False
    return True


def _runtime_module_importable(module: str) -> bool:
    """Use the venv's python to test if a module imports cleanly.

    Avoids the failure mode where the systemd unit starts a python
    process that ImportError's immediately, leaving systemctl in
    activating → failed loop.
    """
    venv_py = _venv_dir() / "bin" / "python"
    repo = _repo_dir()
    if not venv_py.exists() or not repo.is_dir():
        return False
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo) + ":" + env.get("PYTHONPATH", "")
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

    # Look up port / language / Telegram choice from phase 4
    phase4 = state.load(4)
    port = (phase4.notes.get("http_port") if phase4 else None) or 8770
    telegram_enabled = bool(phase4 and phase4.notes.get("telegram"))
    # Metnos runtime language (METNOS_LANG) — the locale the user chose during
    # install. The installer UI itself is English; this configures Metnos.
    lang = (phase4.notes.get("locale") if phase4 else None) or "it"

    notes["http_port"] = port
    notes["lang"] = lang
    notes["telegram_unit_installed"] = telegram_enabled

    # Locate templates
    tmpl_dir = Path(__file__).resolve().parent.parent / "units"
    if not tmpl_dir.exists():
        ui.fail(f"templates dir missing: {tmpl_dir}")

    # 1. Install metnos-http.service
    ui.step(f"Installing metnos-http.service (port {port})")
    _install_unit(tmpl_dir / "metnos-http.service.tmpl", "metnos-http.service", port, lang)

    # 1a. Integrated owner + readiness/quarantine/watchdog units.  They are
    # rendered even on a legacy mixed host, but never activated there: the
    # migration pilot must prove two E2E cycles and rollback first.
    for template_name, unit_name in STACK_UNIT_TEMPLATES:
        _install_unit(tmpl_dir / template_name, unit_name, port, lang)
    notes["stack_units_installed"] = True

    # 1b. Persistent virtual graphical surface for the Playwright Side browser.
    # Xvfb is an explicit host prerequisite; do not silently fall back to
    # headless when it is unavailable.
    side_display_src = _repo_dir() / "systemd" / "metnos-side-display.service"
    if shutil.which("Xvfb"):
        notes["side_display_unit_installed"] = _install_optional_unit(
            side_display_src, "metnos-side-display.service", port, lang,
        )
    else:
        ui.warn("Xvfb not found — Side browser display unit not installed. "
                "Install package xvfb and rerun phase 5.")
        notes["side_display_unit_installed"] = False

    # 2. Optionally install telegram daemon (only if importable)
    telegram_module_ok = False
    if telegram_enabled:
        if _runtime_module_importable("runtime.telegram_daemon"):
            ui.step("Installing metnos-telegram-daemon.service")
            _install_optional_unit(
                tmpl_dir / "metnos-telegram-daemon.service.tmpl",
                "metnos-telegram-daemon.service", port, lang,
            )
            telegram_module_ok = True
        else:
            ui.warn("runtime.telegram_daemon not importable — skipping Telegram unit. "
                    "Once the module ships, re-run `python -m install --force-phase 5`.")
    notes["telegram_unit_installed"] = telegram_module_ok

    # Existing upgraded optional units may contain intentional local tuning.
    # Bind them to the new owner through a drop-in instead of overwriting the
    # unit body. Fresh templates already declare the same relationship.
    for owned_unit in STACK_OWNED_OPTIONAL_UNITS:
        _install_stack_ownership_dropin(owned_unit)

    # 3. daemon-reload
    ui.step("Reloading systemd user unit catalog")
    _systemctl_user("daemon-reload")
    ui.ok("daemon-reload OK")

    # 4. Enable + start the integrated target — only if the runtime is
    # importable and there is no active legacy system HTTP on the same port.
    runtime_importable = _runtime_module_importable("runtime.metnos_http_server")
    legacy_http_active = _legacy_system_http_active()
    if legacy_http_active:
        if not runtime_importable:
            ui.warn(
                "runtime.metnos_http_server is not importable in the target "
                "venv. The legacy baseline remains active; repair the venv "
                "before running the migration pilot."
            )
        else:
            ui.warn(
                "active system-level metnos-http.service detected — integrated "
                "user target installed but not started. Run the migration pilot; "
                "the installer will not create a second listener or disable the "
                "rollback baseline."
            )
        notes["http_enabled"] = True
        notes["target_enabled"] = False
        notes["migration_required"] = True
        notes["http_healthy"] = _wait_for_http(port)
    elif not runtime_importable:
        ui.warn("runtime.metnos_http_server not importable in the venv. "
                "Unit file is in place, but enable is skipped to avoid a "
                "failing systemd loop. Re-run phase 5 once runtime/ ships.")
        notes["http_enabled"] = False
        notes["http_healthy"] = False
        notes["target_enabled"] = False
        notes["migration_required"] = False
    else:
        # Remove an upgrade-era direct default.target symlink without stopping
        # the service.  metnos.target now owns the start/stop relationship.
        _systemctl_user("disable", "metnos-http.service")
        ui.step("Enabling and starting metnos.target")
        r = _systemctl_user("enable", "--now", "metnos.target")
        if r.returncode != 0:
            ui.warn(f"systemctl enable target failed: {r.stderr.strip()}")
            notes["http_enabled"] = False
            notes["target_enabled"] = False
        else:
            ui.ok("metnos.target enabled + composite-ready")
            notes["http_enabled"] = True
            notes["target_enabled"] = True
        notes["migration_required"] = False

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

    # 7. i18n translator timer (lazy fill of the i18n DB). Oneshot service +
    #    5-min timer. Harmless on a complete seed (translate-pending exits in
    #    <1s when nothing is pending); it earns its keep when the runtime adds
    #    new MSG_*/ERR_* keys that need translating into the other locale.
    if _runtime_module_importable("runtime.admin.i18n_cli"):
        ui.step("Installing metnos-i18n-translator (service + 5-min timer)")
        _install_optional_unit(
            tmpl_dir / "metnos-i18n-translator.service.tmpl",
            "metnos-i18n-translator.service", port, lang,
        )
        _install_optional_unit(
            tmpl_dir / "metnos-i18n-translator.timer.tmpl",
            "metnos-i18n-translator.timer", port, lang,
        )
        _install_stack_ownership_dropin("metnos-i18n-translator.service")
        _install_stack_ownership_dropin("metnos-i18n-translator.timer")
        _systemctl_user("daemon-reload")
        r = _systemctl_user("enable", "--now", "metnos-i18n-translator.timer")
        if r.returncode != 0:
            ui.warn(f"i18n translator timer failed to enable: {r.stderr.strip()}")
            notes["i18n_translator_enabled"] = False
        else:
            ui.ok("i18n translator timer enabled")
            notes["i18n_translator_enabled"] = True
    else:
        ui.warn("runtime.admin.i18n_cli not importable — skipping i18n translator timer.")
        notes["i18n_translator_enabled"] = False

    # 8. Linger advisory
    ui.console().print()
    ui.console().print("  [bold]Tip:[/bold] to keep Metnos running across reboots even when "
                       "you don't log in, run [cyan]sudo loginctl enable-linger $USER[/cyan].")

    return notes
