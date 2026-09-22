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

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
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


def _retry(label: str, thunk, attempts: int = 3) -> bool:
    """Run a download thunk up to `attempts` times. The download helpers verify
    sha256 and delete a corrupt transfer (e.g. one mangled by a TLS-intercepting
    middlebox), returning False — so a flaky link usually assembles cleanly on a
    later attempt. Never keeps bad bytes (§2.8)."""
    for i in range(1, attempts + 1):
        if thunk():
            return True
        ui.warn(f"{label}: attempt {i}/{attempts} failed integrity check, retrying")
    return False


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


def _write_http_dropin(conf_name: str, env: dict[str, str]) -> bool:
    """Scrive un drop-in `metnos-http.service.d/<conf_name>` con righe Environment
    e fa daemon-reload. NON riavvia metnos-http: un `python -m install.sidecar`
    post-install può capitare DURANTE un turno attivo (§8.6 no daemon restart
    during a turn) → se il servizio è attivo, avvisa di riavviare a mano. Unico
    helper per i drop-in VLM/Photon (prima duplicato + duplicava il bug)."""
    if not shutil.which("systemctl"):
        ui.warn(f"systemctl absent — cannot write {conf_name}")
        return False
    d = Path.home() / ".config" / "systemd" / "user" / "metnos-http.service.d"
    d.mkdir(parents=True, exist_ok=True)
    body = "[Service]\n" + "".join(f"Environment={k}={v}\n" for k, v in env.items())
    (d / conf_name).write_text(body)
    _systemctl_user("daemon-reload")
    if _systemctl_user("is-active", "metnos-http.service").stdout.strip() == "active":
        ui.info("metnos-http is running — restart to apply the new env: "
                "`systemctl --user restart metnos-http` (not done automatically "
                "to avoid interrupting an in-flight turn).")
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


# ─── VLM (image captions) ────────────────────────────────────────────
# Qwen3-VL-2B serves captions for find_images_indices on :8081. Unlike the
# other sidecars it has NO systemd unit by design: it is lazy-launched on demand
# by runtime/virt.ensure_vlm_up → scripts/vlm_server.sh and auto-stops after
# 10min idle (rare, one-off indexing). So the "installer" only fetches the two
# GGUFs (model + mmproj) and points the launcher at them via a metnos-http
# drop-in; the base install's llama-server binary is reused.

_VLM_REPO = "Qwen/Qwen3-VL-2B-Instruct-GGUF"   # official Qwen GGUFs
_VLM_REVISION = "52d6c8ffea26cc873ac5ad116f8631268d7eb503"
_VLM_MODEL = "Qwen3VL-2B-Instruct-Q4_K_M.gguf"
_VLM_MMPROJ = "mmproj-Qwen3VL-2B-Instruct-F16.gguf"


def _vlm_models_dir() -> Path:
    base = os.environ.get("METNOS_MODELS_DIR") or (str(_repo_dir()) + "/models")
    return Path(base) / "vlm"


def _write_vlm_dropin(model: Path, mmproj: Path, llama: Path | None) -> bool:
    """Point vlm_server.sh (spawned by metnos-http) at the install layout via a
    metnos-http drop-in, so ensure_vlm_up finds the GGUFs + binary without editing
    the unit template."""
    env = {"METNOS_VLM_MODEL": str(model), "METNOS_VLM_MMPROJ": str(mmproj)}
    if llama:
        env["METNOS_VLM_LLAMA_BIN"] = str(llama)
    return _write_http_dropin("vlm.conf", env)


def install_vlm(*, yes: bool = False) -> dict:
    """Fetch the VLM model + mmproj and wire the lazy launcher (no service)."""
    from . import llm_manager
    dest = _vlm_models_dir()
    dest.mkdir(parents=True, exist_ok=True)
    model, mmproj = dest / _VLM_MODEL, dest / _VLM_MMPROJ
    for f in (_VLM_MODEL, _VLM_MMPROJ):
        ui.step(f"Downloading {f}")
        if not _retry(
                f,
                lambda f=f: llm_manager.download_model(
                    _VLM_REPO, f, dest / f, revision=_VLM_REVISION)):
            ui.warn(f"{f}: download failed (network or upstream)")
            return {"vlm": "download_failed"}
    ui.ok(f"VLM model + mmproj present in {dest}")

    # Reuse the base install's llama-server (the VLM runs a local llama-server
    # on :8081). Absent → wired to an external LLM endpoint with no local
    # binary: be honest, the models are useless without it.
    llama = _resolve_vlm_llama(llm_manager)
    if not llama:
        ui.warn("no managed llama-server found — VLM captions need a LOCAL "
                "llama-server. Models are downloaded; install/run the local LLM "
                "tier (base provisioning) and re-run.")

    _write_vlm_dropin(model, mmproj, llama)
    ui.info("VLM is lazy: the first image-index run starts it on :8081 "
            "(auto-stops after 10min idle).")
    return {"vlm": "models_ready" if llama else "models_ready_no_llama"}


def _resolve_vlm_llama(llm_manager) -> Path | None:
    """Resolve or acquire the local binary required by the lazy VLM.

    A base install may intentionally use an already-running external LLM
    endpoint. In that case there is no managed llama.cpp tree yet, but the VLM
    still needs a local executable for its own model on port 8081.
    """
    override = os.environ.get("METNOS_VLM_LLAMA_BIN", "").strip()
    if override and Path(override).is_file():
        return Path(override)
    try:
        managed = llm_manager._find_llama_bin(
            llm_manager._llama_dir(), "llama-server")
        if managed:
            return managed
        system_bin = shutil.which("llama-server")
        if system_bin:
            return Path(system_bin)
        plan = llm_manager.recommend(llm_manager.detect_hardware())
        return llm_manager.acquire_llama(plan.backend, llm_manager._llama_dir())
    except Exception as exc:
        ui.warn(f"llama-server acquisition failed: {type(exc).__name__}: {exc}")
        return None


# ─── Photon (offline geocoder) ───────────────────────────────────────
# Self-hosted offline geocoder serving /api on :2322. The runtime reaches it via
# $METNOS_PHOTON_URL (places, get_location) and falls back to Nominatim when it
# is absent. Replicates the production recipe: komoot photon-1.1.0.jar + an
# official per-country dump hosted by GraphHopper (jsonl.zst) imported into a
# local index. User-level unit, no sudo. Heavy (multi-GB dump + ~14 GB transient
# jsonl + a multi-GB index + a 15-30 min Java import) — one country at a time.

_PHOTON_VERSION = "1.1.0"
_PHOTON_JAR_URL = ("https://github.com/komoot/photon/releases/download/"
                   f"{_PHOTON_VERSION}/photon-{_PHOTON_VERSION}.jar")
_PHOTON_JAR_SHA256 = "592e304500bf77f46d4307c43748a0d86c20c24df1dd4771c5ad64803906d989"
_KOMOOT_BASE = "https://download1.graphhopper.com/public"
_PHOTON_INDEX_SCHEMA = 1
_PHOTON_INDEX_COMPLETE = ".metnos-index-complete.json"
_PHOTON_INDEX_BUILDING = ".metnos-index-building.json"
_PHOTON_JSONL_SCHEMA = 1

# country code → GraphHopper-hosted komoot dump (replicates the production
# photon-switch-country catalog; a closed catalog like the runtime vocab).
_PHOTON_COUNTRY_PATH = {
    "it": "europe/italy/photon-dump-italy",
    "uk": "europe/british-islands/photon-dump-british-islands",
    "fr": "europe/france-monacco/photon-dump-france-monacco",
    "de": "europe/germany/photon-dump-germany",
    "es": "europe/spain/photon-dump-spain",
    "ch": "europe/switzerland/photon-dump-switzerland",
    "at": "europe/austria/photon-dump-austria",
    "nl": "europe/netherlands/photon-dump-netherlands",
    "be": "europe/belgium/photon-dump-belgium",
    "pt": "europe/portugal/photon-dump-portugal",
    "gr": "europe/greece/photon-dump-greece",
    "ie": "europe/ireland/photon-dump-ireland",
    "us": "north-america/us/photon-dump-us",
    "ca": "north-america/canada/photon-dump-canada",
    "mx": "north-america/mexico/photon-dump-mexico",
    "br": "south-america/brazil/photon-dump-brazil",
    "au": "australia-oceania/australia/photon-dump-australia",
    "nz": "australia-oceania/new-zealand/photon-dump-new-zealand",
    "jp": "asia/japan/photon-dump-japan",
    "in": "asia/india/photon-dump-india",
    "planet": "photon-dump-planet",
}


def _photon_dump_url(country: str) -> str:
    path = _PHOTON_COUNTRY_PATH.get(country)
    if not path:  # fallback: try as a European country code (as prod does)
        path = f"europe/{country}/photon-dump-{country}"
    return f"{_KOMOOT_BASE}/{path}-1.0-latest.jsonl.zst"


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write installer state without ever exposing a half-written marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def _photon_index_has_commit(index_dir: Path) -> bool:
    """Return whether the OpenSearch/Lucene tree contains a committed segment.

    Directory existence is not evidence of a completed import: Photon creates
    ``photon_data`` before consuming the dump, so a killed importer leaves a
    plausible-looking but incomplete tree behind. A Lucene ``segments_*`` file
    is a useful structural check; the completion marker below remains the
    authoritative proof that the Java importer exited successfully.
    """
    if not index_dir.is_dir():
        return False
    return any(path.is_file() for path in index_dir.rglob("segments_*"))


def _photon_index_complete(country_dir: Path, country: str, dump_url: str) -> bool:
    """Validate the durable receipt written only after a successful import."""
    marker = country_dir / _PHOTON_INDEX_COMPLETE
    try:
        receipt = json.loads(marker.read_text())
    except (OSError, ValueError, TypeError):
        return False
    expected = {
        "schema": _PHOTON_INDEX_SCHEMA,
        "country": country,
        "photon_version": _PHOTON_VERSION,
        "dump_url": dump_url,
    }
    return (
        all(receipt.get(key) == value for key, value in expected.items())
        and isinstance(receipt.get("source_bytes"), int)
        and receipt["source_bytes"] > 0
        and _photon_index_has_commit(country_dir / "photon_data")
    )


def _photon_import_receipt(country: str, dump_url: str, jsonl: Path) -> dict:
    """Build the durable receipt before the transient JSONL is reclaimed."""
    return {
        "schema": _PHOTON_INDEX_SCHEMA,
        "country": country,
        "photon_version": _PHOTON_VERSION,
        "dump_url": dump_url,
        "source_bytes": jsonl.stat().st_size,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _photon_jsonl_marker(dumps: Path, country: str) -> Path:
    return dumps / f".photon-dump-{country}.jsonl-complete.json"


def _photon_jsonl_receipt(dump_url: str, dump: Path, jsonl: Path) -> dict:
    """Describe one completely expanded dump for safe resume."""
    return {
        "schema": _PHOTON_JSONL_SCHEMA,
        "dump_url": dump_url,
        "dump_bytes": dump.stat().st_size,
        "jsonl_bytes": jsonl.stat().st_size,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _photon_jsonl_complete(marker: Path, dump_url: str,
                           dump: Path, jsonl: Path) -> bool:
    """Prove that decompression, not just output-file creation, completed."""
    try:
        receipt = json.loads(marker.read_text())
    except (OSError, ValueError, TypeError):
        return False
    return (
        receipt.get("schema") == _PHOTON_JSONL_SCHEMA
        and receipt.get("dump_url") == dump_url
        and dump.is_file()
        and jsonl.is_file()
        and receipt.get("dump_bytes") == dump.stat().st_size > 0
        and receipt.get("jsonl_bytes") == jsonl.stat().st_size > 0
    )


def _photon_dump_valid(dump: Path) -> bool:
    """Validate the whole zstd frame before importing its contents."""
    if not dump.is_file() or dump.stat().st_size <= 0:
        return False
    try:
        result = _run(["unzstd", "-t", str(dump)], timeout=900)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def install_photon(*, yes: bool = False, country: str | None = None,
                   port: int | None = None) -> dict:
    """Real Photon install: komoot jar + per-country dump + import + user unit."""
    from . import downloads  # shared robust_fetch (parallel chunks + sha gate)

    country = (country or os.environ.get("METNOS_PHOTON_COUNTRY", "it")).lower()
    port = port or int(os.environ.get("METNOS_PHOTON_PORT", "2322"))
    if not shutil.which("java"):
        ui.warn("java not found — Photon needs a JRE (e.g. openjdk-21). "
                "Install Java and re-run.")
        return {"photon": "no_java"}

    root = _user_data() / "sidecars" / "photon"
    dumps, data = root / "dumps", root / "data"
    jar = root / f"photon-{_PHOTON_VERSION}.jar"
    country_dir = data / country
    index_dir = country_dir / "photon_data"
    for d in (root, dumps, data):
        d.mkdir(parents=True, exist_ok=True)

    # 1. photon.jar (pinned sha256 — immutable GitHub release)
    if jar.exists() and downloads._sha256_file(jar) == _PHOTON_JAR_SHA256:
        ui.ok(f"photon.jar present ({_PHOTON_VERSION})")
    else:
        ui.step(f"Downloading photon-{_PHOTON_VERSION}.jar")
        if not _retry(jar.name, lambda: downloads.robust_fetch(
                _PHOTON_JAR_URL, jar, sha256=_PHOTON_JAR_SHA256, label=jar.name)):
            ui.warn("photon.jar download failed")
            return {"photon": "jar_failed"}
        ui.ok("photon.jar downloaded (sha verified)")

    # 2. per-country index (download dump → decompress → java import). A
    #    success receipt, not directory existence, is the idempotency gate:
    #    Photon creates photon_data/ before the import is complete.
    url = _photon_dump_url(country)
    complete_marker = country_dir / _PHOTON_INDEX_COMPLETE
    building_marker = country_dir / _PHOTON_INDEX_BUILDING
    dump = dumps / f"photon-dump-{country}.jsonl.zst"
    jsonl = dumps / f"photon-dump-{country}.jsonl"
    jsonl_marker = _photon_jsonl_marker(dumps, country)
    if _photon_index_complete(country_dir, country, url):
        # A crash can happen after the completion receipt is committed but
        # before transient cleanup. The receipt makes this cleanup safe.
        building_marker.unlink(missing_ok=True)
        jsonl.unlink(missing_ok=True)
        jsonl_marker.unlink(missing_ok=True)
        ui.ok(f"Photon index present for '{country}'")
    else:
        if index_dir.exists():
            ui.warn("Photon index has no valid completion receipt; "
                    "discarding the incomplete/unverified managed index")
            shutil.rmtree(index_dir)
        complete_marker.unlink(missing_ok=True)
        if not _photon_jsonl_complete(jsonl_marker, url, dump, jsonl):
            jsonl.unlink(missing_ok=True)
            jsonl_marker.unlink(missing_ok=True)
            if not _photon_dump_valid(dump):
                if dump.exists():
                    ui.warn("existing Photon archive is incomplete or invalid; "
                            "downloading it again")
                    dump.unlink()
                ui.step(f"Downloading komoot dump '{country}' (large)")
                ui.info(url)
                # The mobile "latest" dump has no published sha. robust_fetch
                # proves transfer length; unzstd -t below proves the full frame.
                if not _retry(
                        dump.name,
                        lambda: downloads.robust_fetch(
                            url, dump, label=dump.name)):
                    ui.warn("dump download failed")
                    return {"photon": "dump_failed"}
                if not _photon_dump_valid(dump):
                    ui.warn("downloaded Photon archive failed zstd validation")
                    dump.unlink(missing_ok=True)
                    return {"photon": "dump_invalid"}
            else:
                ui.ok(f"validated retained Photon archive for '{country}'")

            ui.step("Decompressing dump (zstd; ~14 GB for a large country)")
            jsonl_part = jsonl.with_name(jsonl.name + ".part")
            jsonl_part.unlink(missing_ok=True)
            try:
                r = _run(["unzstd", "-f", str(dump),
                          "-o", str(jsonl_part)], timeout=3600)
            except (OSError, subprocess.SubprocessError) as exc:
                jsonl_part.unlink(missing_ok=True)
                ui.warn(f"unzstd failed: {type(exc).__name__}: {exc}")
                return {"photon": "decompress_failed"}
            if (r.returncode != 0 or not jsonl_part.is_file()
                    or jsonl_part.stat().st_size <= 0):
                jsonl_part.unlink(missing_ok=True)
                ui.warn(f"unzstd failed: {(r.stderr or '').strip()[-200:]}")
                return {"photon": "decompress_failed"}
            jsonl_part.replace(jsonl)
            _write_json_atomic(
                jsonl_marker, _photon_jsonl_receipt(url, dump, jsonl))
        ui.step("Importing into the Photon index (java, 15-30 min)")
        country_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(building_marker, {
            "schema": _PHOTON_INDEX_SCHEMA,
            "country": country,
            "photon_version": _PHOTON_VERSION,
            "dump_url": url,
            "source_bytes": jsonl.stat().st_size,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            r = _run(["java", "-Xmx4G", "-jar", str(jar), "import",
                      "-import-file", str(jsonl), "-data-dir", str(country_dir),
                      "-j", "4"], timeout=7200)
        except (OSError, subprocess.SubprocessError) as exc:
            ui.warn(f"import failed: {type(exc).__name__}: {exc}")
            return {"photon": "import_failed"}
        if r.returncode != 0 or not _photon_index_has_commit(index_dir):
            detail = (r.stderr or r.stdout or "no diagnostic output").strip()[-300:]
            ui.warn(f"import failed (exit {r.returncode}): {detail}")
            return {"photon": "import_failed"}
        _write_json_atomic(
            complete_marker, _photon_import_receipt(country, url, jsonl))
        building_marker.unlink(missing_ok=True)
        jsonl.unlink(missing_ok=True)  # reclaim the ~14 GB transient
        jsonl_marker.unlink(missing_ok=True)
        ui.ok(f"index built for '{country}'")

    # 3. symlink data/current → <country> (the dir CONTAINING photon_data/;
    #    photon's -data-dir is that parent, as the production instance runs it)
    current = data / "current"
    if current.is_symlink() or current.exists():
        current.unlink()
    current.symlink_to(country)

    # 4. user unit
    enabled = _render_and_install_unit(
        "metnos-photon.service.tmpl", "metnos-photon.service",
        {"@JAVA@": shutil.which("java") or "java",
         "@PHOTON_XMX@": os.environ.get("METNOS_PHOTON_XMX", "4G"),
         "@PHOTON_JAR@": str(jar),
         "@PHOTON_DATA@": str(current),
         "@PHOTON_ROOT@": str(root),
         "@PHOTON_PORT@": str(port)},
    )
    if not enabled:
        return {"photon": "installed_no_unit"}

    # 5. point the runtime at the local instance (drop-in; default is a prod IP)
    _photon_dropin(port)

    # 6. health probe
    ui.step(f"Probing http://127.0.0.1:{port}/status (up to 40s)")
    healthy = _wait_http(f"http://127.0.0.1:{port}/status", timeout_s=40)
    ui.ok(f"Photon running on :{port} ('{country}')") if healthy else ui.warn(
        "Photon did not answer /status yet — check "
        "`systemctl --user status metnos-photon`")
    return {"photon": "running" if healthy else "started_unhealthy",
            "photon_country": country, "photon_port": port}


def _photon_dropin(port: int) -> None:
    """Point the runtime at the local Photon (the default endpoint is a prod IP)."""
    _write_http_dropin("photon.conf",
                       {"METNOS_PHOTON_URL": f"http://localhost:{port}"})


# ─── registry (single source of truth for the optional list) ─────────

def install_playwright(*, yes: bool = False) -> dict:
    """Install the browser sidecar through the common sidecar contract."""
    from . import playwright_sidecar
    return playwright_sidecar.install(yes=yes)

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
        "size": "~3 GB index (default country: it)",
        "desc": "Offline place lookup (get_location, places) — per-country komoot dump.",
        "install": install_photon,
        "ready": True,
    },
    "vlm": {
        "label": "VLM Qwen3-VL-2B",
        "size": "~1.9 GB",
        "desc": "Image enrichment — captions for find_images_indices (lazy :8081, no service).",
        "install": install_vlm,
        "ready": True,
    },
    "playwright": {
        "label": "Playwright browser sidecar",
        "size": "~700 MB",
        "desc": "Local JS rendering and graphical Side browser for sites.",
        "install": install_playwright,
        "ready": True,
    },
}


def install(name: str, *, yes: bool = False, **opts) -> dict:
    """Dispatch to the named sidecar's real installer. Extra `opts` (port,
    country, …) are passed through ONLY to installers that declare them — so the
    CLI can set Photon's country without install_vlm choking on it."""
    import inspect
    entry = SIDECARS.get(name)
    if not entry:
        ui.warn(f"unknown sidecar '{name}' (known: {', '.join(SIDECARS)})")
        return {name: "unknown"}
    fn = entry["install"]
    accepted = set(inspect.signature(fn).parameters)
    kw = {k: v for k, v in opts.items() if k in accepted and v is not None}
    return fn(yes=yes, **kw)


# ─── CLI ─────────────────────────────────────────────────────────────

def _print_list() -> None:
    ui.console().print("Optional Metnos sidecars:")
    for name, e in SIDECARS.items():
        tag = "" if e["ready"] else "  (coming soon)"
        ui.console().print(f"  {name:<9} {e['size']:<8} {e['label']}{tag}")
    ui.console().print("\nInstall one:  python -m install.sidecar <name>")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    yes = "--yes" in raw or "-y" in raw
    opts: dict = {}
    rest: list[str] = []
    it = iter(a for a in raw if a not in ("--yes", "-y"))
    for a in it:
        if a == "--port":
            opts["port"] = int(next(it, "0") or 0) or None
        elif a == "--country":
            opts["country"] = next(it, None)
        elif a.startswith("--port="):
            opts["port"] = int(a.split("=", 1)[1] or 0) or None
        elif a.startswith("--country="):
            opts["country"] = a.split("=", 1)[1] or None
        else:
            rest.append(a)
    if not rest or rest[0] in ("--list", "-l", "list"):
        _print_list()
        return 0
    name = rest[0]
    notes = install(name, yes=yes, **opts)
    status = notes.get(name, "")
    print(notes)
    return 0 if status in ("running", "started_unhealthy",
                           "models_ready", "models_ready_no_llama") else 1


if __name__ == "__main__":
    sys.exit(main())
