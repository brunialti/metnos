# SPDX-License-Identifier: MIT
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


def _requirements_path() -> Path:
    root = Path(os.environ.get(
        "METNOS_INSTALL_ROOT", Path(__file__).resolve().parents[2]))
    return root / "requirements-linux-x86_64.lock"


def _venv_pip() -> str:
    root = Path(os.environ.get(
        "METNOS_INSTALL_ROOT", Path(__file__).resolve().parents[2]))
    venv = os.environ.get("METNOS_VENV", str(root / ".venv"))
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


def _install_deps() -> int:
    """Use the release's exact dependency closure; failure stops this phase."""
    from ..executor_birth_python_environment_posix import _lock_requirements_v1

    requirements = _requirements_path()
    names = _lock_requirements_v1(requirements.read_bytes())
    pip = _venv_pip()
    if not Path(pip).exists():
        ui.fail(i18n.t("p1_pip_not_found", pip=pip))

    try:
        result = subprocess.run(
            [pip, "install", "--require-hashes", "--no-deps", "--only-binary=:all:",
             "-r", str(requirements)],
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        ui.fail(i18n.t("p1_dep_timeout", dep=requirements.name))
    if result.returncode != 0:
        reason = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "pip"
        ui.fail(i18n.t("p1_dep_install_failed", dep=requirements.name, reason=reason))
    return len(names)


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}

    ui.banner("Phase 1 — Bootstrap", "Pre-flight checks + Python dependencies + runtime directories")

    # 1. Pre-flight
    ui.step("Running pre-flight checks")
    supported = preflight.check_python()
    if not supported.ok:
        ui.fail(supported.detail)
    ok = preflight.run_all(min_disk_gb=8)
    if not ok and not getattr(args, "force", False):
        ui.fail("Pre-flight failed. Re-run with --force to ignore (not recommended) or fix the issues above.")
    notes["preflight_ok"] = ok

    # 2. Create runtime directories
    ui.step("Creating runtime directory layout")
    for d in _runtime_dirs():
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Every XDG root belongs to one Metnos account.  Public artifacts are
        # exported explicitly; runtime data, indexes and models are not shared
        # by making the storage tree world-readable.
        try:
            d.chmod(0o700)
        except OSError:
            pass
    ui.ok(f"{len(_runtime_dirs())} directories ready")
    notes["dirs_created"] = len(_runtime_dirs())

    # 3. Use the same hashed closure as the signed runtime.
    ui.step(i18n.t("p1_progress_deps"))
    if getattr(args, "managed", False):
        # The administrative parent already verified the immutable environment.
        from ..executor_birth_python_environment_posix import _lock_requirements_v1
        notes["dependencies_verified"] = len(_lock_requirements_v1(_requirements_path().read_bytes()))
    else:
        notes["dependencies_verified"] = _install_deps()
    ui.ok(i18n.t("p1_deps_verified", count=notes["dependencies_verified"]))

    # 4. Sanity import
    ui.step("Verifying core imports")
    try:
        import aiohttp  # noqa: F401
        import cryptography.fernet  # noqa: F401
        import httpx  # noqa: F401
        import jinja2  # noqa: F401
        import onnxruntime  # noqa: F401
        import PIL  # noqa: F401
        import tomlkit  # noqa: F401
        ui.ok("Core modules importable")
        notes["import_ok"] = True
    except ImportError as e:
        ui.fail(i18n.t("p1_import_failure", err=e))

    return notes
