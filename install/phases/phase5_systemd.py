# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 5 — Systemd services (user level).

Writes user-level systemd units from the templates in
``install/units/*``, runs ``systemctl --user daemon-reload``, enables and
starts the integrated ``metnos.target``, probes its composite readiness,
installs the mandatory ``metnos-i18n-translator.timer``, and conditionally enables
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

from .. import i18n, llm_manager, state, ui


STACK_UNIT_TEMPLATES = (
    ("metnos.target.tmpl", "metnos.target"),
    ("metnos-stack-ready.service.tmpl", "metnos-stack-ready.service"),
    ("metnos-stack-quarantine.service.tmpl", "metnos-stack-quarantine.service"),
    ("metnos-stack-watchdog.service.tmpl", "metnos-stack-watchdog.service"),
    ("metnos-stack-watchdog.timer.tmpl", "metnos-stack-watchdog.timer"),
    ("metnos-durable-worker.service.tmpl", "metnos-durable-worker.service"),
    ("metnos-i18n-translator.service.tmpl", "metnos-i18n-translator.service"),
    ("metnos-i18n-translator.timer.tmpl", "metnos-i18n-translator.timer"),
)
STACK_OWNED_OPTIONAL_UNITS = (
    "metnos-side-display.service",
    "metnos-playwright.service",
    "metnos-telegram-daemon.service",
    "metnos-llm.service",
    "metnos-searxng.service",
    "metnos-photon.service",
)

# The readiness probe owns its functional deadline. systemd gets a bounded
# shutdown margin, and the installer that waits for the systemd transaction
# gets one additional margin. Keeping the relationship here prevents a shorter
# client timeout from invalidating a still-healthy readiness job.
STACK_READY_PROBE_TIMEOUT_S = 120
STACK_READY_SERVICE_TIMEOUT_S = STACK_READY_PROBE_TIMEOUT_S + 30
STACK_ACTIVATION_TIMEOUT_S = STACK_READY_SERVICE_TIMEOUT_S + 30


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
    return _repo_dir() / ".venv"


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


def _substitute(
    template: str,
    port: int,
    lang: str,
    http_host: str = "127.0.0.1",
) -> str:
    """Replace @VAR@ placeholders in unit template content."""
    repl = {
        "@VENV@":       str(_venv_dir()),
        "@DATA_DIR@":   os.environ.get("METNOS_USER_DATA", str(Path.home() / ".local" / "share" / "metnos")),
        "@CONFIG_DIR@": os.environ.get("METNOS_USER_CONFIG", str(Path.home() / ".config" / "metnos")),
        "@STATE_DIR@":  os.environ.get("METNOS_USER_STATE", str(Path.home() / ".local" / "state" / "metnos")),
        "@REPO_DIR@":   str(_repo_dir()),
        "@PORT@":       str(port),
        "@HTTP_HOST@":  http_host,
        "@LANG@":       lang,
        "@COMPLETION_ENV@": _completion_env_line(),
        "@STACK_READY_PROBE_TIMEOUT@": str(STACK_READY_PROBE_TIMEOUT_S),
        "@STACK_READY_SERVICE_TIMEOUT@": str(STACK_READY_SERVICE_TIMEOUT_S),
    }
    for k, v in repl.items():
        template = template.replace(k, v)
    return template


def _install_unit(
    template_path: Path,
    dest_name: str,
    port: int,
    lang: str,
    http_host: str = "127.0.0.1",
) -> bool:
    """Render one template into the user systemd dir."""
    if not template_path.exists():
        ui.warn(f"missing template: {template_path}")
        return False
    rendered = _substitute(template_path.read_text(), port, lang, http_host)
    dest = _systemd_user_dir() / dest_name
    dest.write_text(rendered)
    ui.ok(f"wrote {dest}")
    return True


def _install_optional_unit(template_path: Path, dest_name: str,
                           port: int, lang: str,
                           http_host: str = "127.0.0.1") -> bool:
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
    return _install_unit(template_path, dest_name, port, lang, http_host)


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


def _systemctl_user(*args: str, check: bool = False,
                    timeout_s: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=timeout_s, check=check,
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
    http_host = (
        (phase4.notes.get("http_host") if phase4 else None) or "127.0.0.1"
    )
    telegram_enabled = bool(phase4 and phase4.notes.get("telegram"))
    # Metnos runtime language (METNOS_LANG) — the locale the user chose during
    # install. The installer UI itself is English; this configures Metnos.
    lang = (phase4.notes.get("locale") if phase4 else None) or "it"

    notes["http_port"] = port
    notes["http_host"] = http_host
    notes["lang"] = lang
    notes["telegram_unit_installed"] = telegram_enabled

    # Locate templates
    tmpl_dir = Path(__file__).resolve().parent.parent / "units"
    if not tmpl_dir.exists():
        ui.fail(f"templates dir missing: {tmpl_dir}")

    # 1. Install metnos-http.service
    ui.step(f"Installing metnos-http.service ({http_host}:{port})")
    _install_unit(
        tmpl_dir / "metnos-http.service.tmpl",
        "metnos-http.service",
        port,
        lang,
        http_host,
    )

    # 1a. Integrated owner + readiness/quarantine/watchdog units.  The target
    # remains inactive on a legacy mixed host, but the read-only/targeted
    # watchdog timer can run independently from default.target: it never
    # starts a second HTTP listener and preserves the rollback baseline.
    for template_name, unit_name in STACK_UNIT_TEMPLATES:
        _install_unit(
            tmpl_dir / template_name, unit_name, port, lang, http_host,
        )
    notes["stack_units_installed"] = True

    # 1b. Persistent virtual graphical surface for the Playwright Side browser.
    # Xvfb is an explicit host prerequisite; do not silently fall back to
    # headless when it is unavailable.
    side_display_src = _repo_dir() / "systemd" / "metnos-side-display.service"
    if shutil.which("Xvfb"):
        notes["side_display_unit_installed"] = _install_optional_unit(
            side_display_src, "metnos-side-display.service", port, lang,
            http_host,
        )
    else:
        ui.warn("Xvfb not found — Side browser display unit not installed. "
                "Install package xvfb and rerun phase 5.")
        notes["side_display_unit_installed"] = False

    # 2. Optionally install telegram daemon (only if importable)
    telegram_module_ok = False
    if telegram_enabled:
        if _runtime_module_importable("runtime.channels.daemon"):
            ui.step("Installing metnos-telegram-daemon.service")
            _install_optional_unit(
                tmpl_dir / "metnos-telegram-daemon.service.tmpl",
                "metnos-telegram-daemon.service", port, lang, http_host,
            )
            telegram_module_ok = True
        else:
            ui.warn("runtime.channels.daemon not importable — skipping Telegram unit. "
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
    http_runtime_importable = _runtime_module_importable(
        "runtime.metnos_http_server")
    i18n_runtime_importable = _runtime_module_importable(
        "runtime.admin.i18n_cli")
    runtime_importable = http_runtime_importable and i18n_runtime_importable
    missing_core_modules = [
        name for name, available in (
            ("runtime.metnos_http_server", http_runtime_importable),
            ("runtime.admin.i18n_cli", i18n_runtime_importable),
        )
        if not available
    ]
    if missing_core_modules:
        ui.warn(i18n.t(
            "p5_required_modules_missing",
            modules=", ".join(missing_core_modules),
        ))
    legacy_http_active = _legacy_system_http_active()
    if legacy_http_active:
        if runtime_importable:
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
        if runtime_importable:
            ui.step("Enabling bounded watchdog for the legacy baseline")
            wanted = _systemctl_user(
                "add-wants", "default.target", "metnos-stack-watchdog.timer")
            started = (
                _systemctl_user("start", "metnos-stack-watchdog.timer")
                if wanted.returncode == 0 else wanted
            )
            notes["watchdog_enabled"] = (
                wanted.returncode == 0 and started.returncode == 0)
            if notes["watchdog_enabled"]:
                ui.ok("metnos-stack-watchdog.timer enabled")
            else:
                ui.warn(
                    "watchdog timer could not be enabled: "
                    f"{(started.stderr or wanted.stderr).strip()}"
                )
        else:
            notes["watchdog_enabled"] = False
    elif not runtime_importable:
        notes["http_enabled"] = False
        notes["http_healthy"] = False
        notes["target_enabled"] = False
        notes["migration_required"] = False
        notes["watchdog_enabled"] = False
    else:
        # Remove an upgrade-era direct default.target symlink without stopping
        # the service.  metnos.target now owns the start/stop relationship.
        _systemctl_user("disable", "metnos-http.service")
        ui.step("Enabling and starting metnos.target")
        r = _systemctl_user(
            "enable", "--now", "metnos.target",
            timeout_s=STACK_ACTIVATION_TIMEOUT_S,
        )
        if r.returncode != 0:
            ui.warn(f"systemctl enable target failed: {r.stderr.strip()}")
            notes["http_enabled"] = False
            notes["target_enabled"] = False
        else:
            ui.ok("metnos.target enabled + composite-ready")
            notes["http_enabled"] = True
            notes["target_enabled"] = True
        notes["migration_required"] = False
        notes["watchdog_enabled"] = bool(notes.get("target_enabled"))

        # 5. Health probe (only if start succeeded)
        if notes["http_enabled"]:
            ui.step("Probing HTTP health endpoint (up to 20s)")
            if _wait_for_http(port):
                ui.ok(f"http://127.0.0.1:{port}/agent/health responds 200")
                notes["http_healthy"] = True
            else:
                ui.warn("health endpoint did not respond within 20s — check `systemctl --user status metnos-http`")
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

    # 7. The i18n timer is a required dependency of the integrated target.
    #    A legacy system-HTTP installation cannot start that target safely, so
    #    keep the same required timer alive directly until guarded migration.
    if notes.get("target_enabled"):
        notes["i18n_translator_enabled"] = True
    elif runtime_importable and legacy_http_active:
        r = _systemctl_user(
            "enable", "--now", "metnos-i18n-translator.timer")
        if r.returncode != 0:
            ui.warn(f"i18n translator timer failed to enable: {r.stderr.strip()}")
            notes["i18n_translator_enabled"] = False
        else:
            ui.ok("i18n translator timer enabled")
            notes["i18n_translator_enabled"] = True
    else:
        notes["i18n_translator_enabled"] = False

    # 8. Linger advisory
    ui.console().print()
    ui.console().print("  [bold]Tip:[/bold] to keep Metnos running across reboots even when "
                       "you don't log in, run [cyan]sudo loginctl enable-linger $USER[/cyan].")

    return notes
