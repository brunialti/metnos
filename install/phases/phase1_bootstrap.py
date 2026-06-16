# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 1 — Bootstrap.

The venv and bootstrap deps (rich, httpx) are already in place by the
time this phase runs (install/bootstrap.sh did that). What this phase
does is the rest of phase 1 per ADR 0145:

- run all pre-flight checks
- install the full Python dependency set into the venv
- create the standard runtime directories

It is short and very safe. Most of the volume of phase 1 is the
``pip install`` invocation, which can run for a couple of minutes the
first time and is essentially instant on re-runs (cache).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .. import i18n, preflight, ui


# Runtime deps — kept tight to minimise install time. The skill bundles
# add their own deps lazily on first use.
_RUNTIME_DEPS = [
    "aiohttp>=3.9",          # HTTP server (metnos-http)
    "httpx>=0.27",           # HTTP client (web crawl, frontier API)
    "Jinja2>=3.1",           # docs templates + dialog forms
    "MarkupSafe>=2.1",       # used by Jinja2
    "pydantic>=2.6",         # manifest validation, config
    "tomli>=2.0; python_version < '3.11'",
    "rich>=13.7",            # already installed by bootstrap, pinned here
    "cryptography>=42",      # Fernet for credentials store (ADR 0131)
    "onnxruntime>=1.17",     # BGE-M3 embedder (ADR 0117)
    "numpy>=1.26",
    "Pillow>=10.2",          # image read for create_images_indices
    "anthropic>=0.34",       # optional, but kept core
    "openai>=1.40",          # optional, frontier fallback
    "prompt_toolkit>=3.0",   # dialog form
]


def _venv_pip() -> str:
    venv = os.environ.get("METNOS_VENV", str(Path.home() / ".local" / "share" / "metnos" / ".venv"))
    return str(Path(venv) / "bin" / "pip")


def _runtime_dirs() -> list[Path]:
    """Standard directories created during bootstrap (empty)."""
    home = Path(os.environ.get("METNOS_USER_DATA", Path.home() / ".local" / "share" / "metnos"))
    cfg = Path(os.environ.get("METNOS_USER_CONFIG", Path.home() / ".config" / "metnos"))
    state = Path(os.environ.get("METNOS_USER_STATE", Path.home() / ".local" / "state" / "metnos"))
    return [
        home,
        home / "credentials",
        home / "turns",
        home / "skills",
        home / "executors",
        home / "logs",
        home / "index",
        home / "models",   # populated in phase 2
        cfg,
        state,
        state / "install",
    ]


def _install_deps() -> tuple[int, int]:
    """Install the runtime deps. Returns (installed, already_present)."""
    pip = _venv_pip()
    if not Path(pip).exists():
        ui.fail(i18n.t("p1_pip_not_found", pip=pip))

    installed = 0
    skipped = 0
    with ui.progress() as p:
        task = p.add_task(i18n.t("p1_progress_deps"), total=len(_RUNTIME_DEPS))
        for dep in _RUNTIME_DEPS:
            # `pip install` is the simplest path; pip's resolver handles
            # already-satisfied as a quick no-op. We capture output so
            # the progress bar isn't drowned out.
            try:
                r = subprocess.run(
                    [pip, "install", "--quiet", "--upgrade-strategy", "only-if-needed", dep],
                    capture_output=True, text=True, timeout=180,
                )
                if r.returncode != 0:
                    reason = r.stderr.strip().splitlines()[-1] if r.stderr else 'unknown'
                    ui.warn(i18n.t("p1_dep_install_failed", dep=dep, reason=reason))
                else:
                    if "already satisfied" in (r.stdout + r.stderr).lower():
                        skipped += 1
                    else:
                        installed += 1
            except subprocess.TimeoutExpired:
                ui.warn(i18n.t("p1_dep_timeout", dep=dep))
            p.update(task, advance=1)
    return installed, skipped


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}

    ui.banner("Phase 1 — Bootstrap", "Pre-flight checks + Python dependencies + runtime directories")

    # 1. Pre-flight
    ui.step("Running pre-flight checks")
    ok = preflight.run_all(min_disk_gb=8)
    if not ok and not getattr(args, "force", False):
        ui.fail("Pre-flight failed. Re-run with --force to ignore (not recommended) or fix the issues above.")
    notes["preflight_ok"] = ok

    # 2. Create runtime directories
    ui.step("Creating runtime directory layout")
    for d in _runtime_dirs():
        d.mkdir(parents=True, exist_ok=True)
        # 0o700 on credentials/state, 0o755 on the rest
        if d.name in {"credentials", "install"} or d.parent.name in {"credentials", "install"}:
            try:
                d.chmod(0o700)
            except PermissionError:
                pass
    ui.ok(f"{len(_runtime_dirs())} directories ready")
    notes["dirs_created"] = len(_runtime_dirs())

    # 3. Install full dependency set
    ui.step(f"Installing {len(_RUNTIME_DEPS)} Python dependencies (this can take a few minutes)")
    installed, skipped = _install_deps()
    ui.ok(f"{installed} installed, {skipped} already present")
    notes["pip_installed"] = installed
    notes["pip_already_present"] = skipped

    # 4. Sanity import
    ui.step("Verifying core imports")
    try:
        import importlib
        for mod in ("aiohttp", "httpx", "jinja2", "pydantic", "onnxruntime", "PIL", "cryptography.fernet"):
            importlib.import_module(mod)
        ui.ok("Core modules importable")
        notes["import_ok"] = True
    except ImportError as e:
        ui.warn(f"import failure: {e}")
        notes["import_ok"] = False

    return notes
