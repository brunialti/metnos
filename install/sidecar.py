# SPDX-License-Identifier: AGPL-3.0-only
"""install/sidecar.py — optional self-hosted sidecars (real install, post-base).

The base install ships the mandatory pieces (embedder + LLM tier). A few
capabilities lean on **optional, self-hosted** companion services that are too
heavy to force on every install: web search (SearXNG), offline geocoding
(Photon), image captions (VLM). This module installs them **for real** — clone
/ deps / model, a user-level systemd unit (no sudo), enable + start, honest
outcome — one at a time.

Standalone use (also how phase 2 adds one during install):

    python -m install.sidecar searxng        # interactive
    python -m install.sidecar searxng --yes   # non-interactive
    python -m install.sidecar --list          # what's available

Design mirrors ``install/playwright_sidecar.py``: same ``ui`` fallback, same
"render a units/*.tmpl into ~/.config/systemd/user, daemon-reload, enable --now,
health-probe" shape, same §2.8 honesty (we never claim "running" unless the
service actually answered its health endpoint).

Each sidecar is a user-level service: it runs as the invoking user, needs no
root, and (with ``loginctl enable-linger``) survives logout — matching the rest
of the Metnos install. This is deliberately lighter than the reference
production boxes (which run SearXNG/Photon as system services with dedicated
users); a single-user self-hosted instance does not need that ceremony.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from . import ui
except ImportError:  # standalone senza package context
    class _UI:  # minimal fallback
        @staticmethod
        def step(m): print(f"  → {m}")
        @staticmethod
        def ok(m): print(f"  ✓ {m}")
        @staticmethod
        def warn(m): print(f"  ! {m}")
        @staticmethod
        def info(m): print(f"    {m}")
        @staticmethod
        def fail(m, exit_code=1): print(f"  ✗ {m}"); sys.exit(exit_code)
        @staticmethod
        def console():
            class _C:
                def print(self, *a, **k): print(*a)
            return _C()
        @staticmethod
        def confirm(q, default=True): return default
    ui = _UI()  # type: ignore


# ─── shared path / process helpers ───────────────────────────────────

def _repo_dir() -> Path:
    return Path(os.environ.get("METNOS_INSTALL_ROOT") or
                Path(__file__).resolve().parent.parent)


def _user_data() -> Path:
    return Path(os.environ.get("METNOS_USER_DATA",
                               Path.home() / ".local" / "share" / "metnos"))


def _user_config() -> Path:
    return Path(os.environ.get("METNOS_USER_CONFIG",
                               Path.home() / ".config" / "metnos"))


def _venv_python() -> str:
    """Python that runs Metnos itself (the base-install venv)."""
    return os.environ.get("METNOS_VENV_PYTHON") or sys.executable


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
         timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, timeout=timeout)


def _systemctl_user(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True, timeout=30)


def _render_and_install_unit(tmpl_name: str, unit_name: str,
                             repl: dict[str, str]) -> bool:
    """Render ``install/units/<tmpl_name>`` into the user systemd dir,
    daemon-reload, enable + start it. Returns whether enable succeeded."""
    if not shutil.which("systemctl"):
        ui.warn("systemctl absent — skipping the service unit")
        return False
    tmpl = _repo_dir() / "install" / "units" / tmpl_name
    if not tmpl.exists():
        ui.warn(f"missing unit template: {tmpl}")
        return False
    body = tmpl.read_text()
    for k, v in repl.items():
        body = body.replace(k, v)
    dest_dir = Path.home() / ".config" / "systemd" / "user"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / unit_name).write_text(body)
    _systemctl_user("daemon-reload")
    r = _systemctl_user("enable", "--now", unit_name)
    if r.returncode != 0:
        ui.warn(f"systemctl enable {unit_name} failed: {r.stderr.strip()[-200:]}")
        return False
    return True


def _wait_http(url: str, *, timeout_s: int = 30) -> bool:
    """Poll an HTTP endpoint until it answers 200, or timeout."""
    import httpx
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except httpx.RequestError as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.0)
    if last:
        ui.info(f"last probe error: {last}")
    return False


# ─── SearXNG ─────────────────────────────────────────────────────────
# Self-hosted metasearch aggregator. The runtime queries it at
# ``$METNOS_SEARXNG_URL`` (default http://localhost:8888) on the
# ``/search?format=json`` path (executors/find_urls). Without it, find_urls
# degrades honestly. We install a user-level, redis-less instance: a single
# user does not need the production rate-limiter (which is the only thing that
# wants redis).

_SEARXNG_REPO = "https://github.com/searxng/searxng.git"


def _searxng_settings(port: int, secret_key: str) -> str:
    """Minimal settings overlay (``use_default_settings`` inherits the rest).

    Two non-defaults matter: ``json`` in ``search.formats`` (the Metnos query
    path is ``/search?format=json``; upstream defaults to html-only) and
    ``limiter: false`` (drops the redis dependency for a single user)."""
    return (
        "# Metnos SearXNG sidecar — single-user, self-hosted. Generated by\n"
        "# `python -m install.sidecar searxng`. Inherits upstream defaults.\n"
        "use_default_settings: true\n"
        "server:\n"
        f"  port: {port}\n"
        '  bind_address: "127.0.0.1"\n'
        f'  secret_key: "{secret_key}"\n'
        "  limiter: false           # no redis/valkey needed for one user\n"
        "  public_instance: false\n"
        "  image_proxy: true\n"
        '  method: "GET"\n'
        "search:\n"
        "  formats:                 # json REQUIRED — Metnos queries /search?format=json\n"
        "    - html\n"
        "    - json\n"
    )


def _searxng_secret(settings_path: Path) -> str:
    """Reuse an existing secret_key on re-run (regenerating would invalidate
    live sessions); otherwise mint a fresh one."""
    if settings_path.exists():
        for line in settings_path.read_text().splitlines():
            s = line.strip()
            if s.startswith("secret_key:"):
                val = s.split(":", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return secrets.token_hex(32)


def install_searxng(*, yes: bool = False, port: int | None = None) -> dict:
    """Real SearXNG install: clone + dedicated venv + settings + user unit."""
    if port is None:
        port = int(os.environ.get("METNOS_SEARXNG_PORT", "8888"))

    root = _user_data() / "sidecars" / "searxng"
    src = root / "searxng"            # the git clone (searx/ package lives here)
    venv = root / "venv"              # dedicated venv (isolated from Metnos deps)
    cache = root / "cache"            # private TMPDIR for the sqlite caches
    cfg_dir = _user_config() / "searxng"
    settings = cfg_dir / "settings.yml"
    root.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    if not shutil.which("git"):
        ui.warn("git not found — cannot clone SearXNG")
        return {"searxng": "no_git"}

    # 1. clone (idempotent: shallow clone once, refresh on re-run)
    if (src / "searx" / "webapp.py").exists():
        ui.ok(f"SearXNG source present at {src}")
    else:
        ui.step("Cloning SearXNG (shallow)")
        r = _run(["git", "clone", "--depth", "1", _SEARXNG_REPO, str(src)])
        if r.returncode != 0 or not (src / "searx" / "webapp.py").exists():
            ui.warn(f"clone failed: {r.stderr.strip()[-300:]}")
            return {"searxng": "clone_failed"}
        ui.ok(f"cloned into {src}")

    # 2. dedicated venv + deps (SearXNG pins versions that can clash with the
    #    Metnos runtime venv — keep it separate, like the production box).
    if not (venv / "bin" / "python").exists():
        ui.step("Creating dedicated venv")
        r = _run([_venv_python(), "-m", "venv", str(venv)])
        if r.returncode != 0:
            ui.warn(f"venv creation failed: {r.stderr.strip()[-300:]}")
            return {"searxng": "venv_failed"}
    vpy = str(venv / "bin" / "python")
    ui.step("Installing SearXNG dependencies (pip)")
    _run([vpy, "-m", "pip", "install", "--upgrade",
          "pip", "setuptools", "wheel", "pyyaml"], timeout=600)
    req = src / "requirements.txt"
    r = _run([vpy, "-m", "pip", "install", "-r", str(req)], timeout=1800)
    if r.returncode != 0:
        ui.warn(f"pip install failed: {r.stderr.strip()[-400:]}")
        return {"searxng": "pip_failed"}
    # sanity: the searx package must import with the clone as cwd
    chk = _run([vpy, "-c", "import searx, searx.webapp"], cwd=src)
    if chk.returncode != 0:
        ui.warn(f"searx import check failed: {chk.stderr.strip()[-300:]}")
        return {"searxng": "import_failed"}
    ui.ok("SearXNG dependencies installed")

    # 3. settings.yml (preserve an existing secret_key across re-runs)
    settings.write_text(_searxng_settings(port, _searxng_secret(settings)))
    ui.ok(f"wrote {settings}")

    # 4. user unit
    enabled = _render_and_install_unit(
        "metnos-searxng.service.tmpl", "metnos-searxng.service",
        {"@SEARXNG_VENV@": str(venv),
         "@SEARXNG_SRC@": str(src),
         "@SEARXNG_SETTINGS@": str(settings),
         "@SEARXNG_CACHE@": str(cache)},
    )
    if not enabled:
        return {"searxng": "installed_no_unit"}

    # 5. honest health probe
    ui.step(f"Probing http://127.0.0.1:{port}/healthz (up to 30s)")
    healthy = _wait_http(f"http://127.0.0.1:{port}/healthz", timeout_s=30)
    if healthy:
        ui.ok(f"SearXNG running on :{port}")
    else:
        ui.warn("SearXNG did not answer /healthz yet — check "
                "`systemctl --user status metnos-searxng`")

    # The runtime default endpoint is http://localhost:8888. A non-default port
    # needs METNOS_SEARXNG_URL on the metnos-http unit, so say so honestly.
    if port != 8888:
        ui.info(f"non-default port: set Environment=METNOS_SEARXNG_URL="
                f"http://localhost:{port} on metnos-http.service")
    return {"searxng": "running" if healthy else "started_unhealthy",
            "searxng_port": port}


# ─── not-yet-implemented sidecars (honest placeholders, §2.8) ────────
# Photon and VLM are next in line (one at a time). Until their real installer
# lands they MUST NOT pretend to install — they report honestly.

def _not_implemented(name: str) -> dict:
    ui.warn(f"{name}: real installer not shipped yet (coming in a follow-up). "
            f"Nothing was installed.")
    return {name: "not_implemented"}


def install_photon(*, yes: bool = False) -> dict:
    return _not_implemented("photon")


def install_vlm(*, yes: bool = False) -> dict:
    return _not_implemented("vlm")


# ─── registry (single source of truth for the optional list) ─────────

SIDECARS: dict[str, dict] = {
    "searxng": {
        "label": "SearXNG search aggregator",
        "size": "~200 MB",
        "desc": "Self-hosted web search (find_urls).",
        "install": install_searxng,
        "ready": True,
    },
    "photon": {
        "label": "Photon offline geocoder",
        "size": "~3 GB",
        "desc": "Offline place lookup (per-country dataset).",
        "install": install_photon,
        "ready": False,
    },
    "vlm": {
        "label": "VLM Qwen3-VL-2B",
        "size": "~3 GB",
        "desc": "Image enrichment — captions for find_images_indices.",
        "install": install_vlm,
        "ready": False,
    },
}


def install(name: str, *, yes: bool = False) -> dict:
    """Dispatch to the named sidecar's real installer."""
    entry = SIDECARS.get(name)
    if not entry:
        ui.warn(f"unknown sidecar '{name}' (known: {', '.join(SIDECARS)})")
        return {name: "unknown"}
    return entry["install"](yes=yes)


# ─── CLI ─────────────────────────────────────────────────────────────

def _print_list() -> None:
    ui.console().print("Optional Metnos sidecars:")
    for name, e in SIDECARS.items():
        tag = "" if e["ready"] else "  (coming soon)"
        ui.console().print(f"  {name:<9} {e['size']:<8} {e['label']}{tag}")
    ui.console().print("\nInstall one:  python -m install.sidecar <name>")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    yes = "--yes" in argv or "-y" in argv
    argv = [a for a in argv if a not in ("--yes", "-y")]
    if not argv or argv[0] in ("--list", "-l", "list"):
        _print_list()
        return 0
    name = argv[0]
    notes = install(name, yes=yes)
    status = notes.get(name, "")
    print(notes)
    return 0 if status in ("running", "installed", "started_unhealthy") else 1


if __name__ == "__main__":
    sys.exit(main())
