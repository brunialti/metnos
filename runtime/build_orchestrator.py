"""build_orchestrator — gestione lifecycle build asincrona indici immagine
(ADR 0093, 6/5/2026).

API:
  - start_async_build(base_path, idx, *, actor, channel, chat_id) -> dict
  - get_build_status(base_path, idx) -> dict | None
  - stop_build(base_path, idx) -> dict
  - list_active_builds() -> list[dict]
  - cleanup_orphan_tmp_dirs(*, max_age_s) -> dict

Implementazione: usa systemctl --user start --transient --unit=<name>
per spawnare il build_runner come unit di systemd. systemd reaps zombie,
gestisce restart e logga a journalctl --user. Se l'unit esiste già con
quel nome, ritorna `already_running: true`.

Niente Popen: tutto via systemd. Niente threading: il daemon HTTP usa
async tasks per healthcheck e dispatch (vedi http_async_tasks.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from logging_setup import get_logger
import config as _C  # §7.11

log = get_logger(__name__)

_INDEX_BASE: Path | None = None  # test override via setattr


def _index_image_root() -> Path:
    """Test isolation via env vars (8/5/2026): vedi runtime/config.py."""
    if _INDEX_BASE is not None:
        return Path(_INDEX_BASE)
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    return _C.PATH_USER_DATA / "index" / "image"


def _is_dry_run() -> bool:
    return os.environ.get("METNOS_DRY_RUN", "0") == "1"


_PROGRESS_DIR = _C.PATH_USER_STATE / "build_progress"
_VALID_IDX = ("scene", "persons", "gps")

# Path stabile al venv (the design guide istruzioni operative)
_VENV_PYTHON = "/opt/suprastructure/.venv/bin/python"


def _digest_of(base_path: Path) -> str:
    return hashlib.sha256(str(base_path.resolve()).encode("utf-8")).hexdigest()[:16]


def _unit_name(base_path: Path, idx: str) -> str:
    return f"metnos-build-{_digest_of(base_path)}-{idx}"


def _index_dir(base_path: Path, idx: str) -> Path:
    return _index_image_root() / _digest_of(base_path) / idx


def _progress_path(base_path: Path, idx: str) -> Path:
    return _PROGRESS_DIR / f"{_digest_of(base_path)}_{idx}.json"


# --- systemctl --user wrappers -----------------------------------------------

def _systemctl_user(*args: str, timeout: float = 5.0) -> tuple[int, str, str]:
    """Wrapper di systemctl --user. Ritorna (returncode, stdout, stderr)."""
    cmd = ["systemctl", "--user", *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, "", f"{type(e).__name__}: {e}"


def _is_unit_active(unit_name: str) -> bool:
    """True se l'unit è active|activating."""
    rc, out, _err = _systemctl_user("is-active", unit_name, timeout=3.0)
    state = (out or "").strip()
    return state in ("active", "activating", "reloading")


def _is_unit_failed(unit_name: str) -> bool:
    rc, out, _err = _systemctl_user("is-failed", unit_name, timeout=3.0)
    return (out or "").strip() == "failed"


# --- API pubblica ------------------------------------------------------------

def start_async_build(base_path: Path | str, idx: str, *,
                        actor: str = "host",
                        channel: str = "",
                        chat_id: str = "",
                        batch_size: int = 500) -> dict:
    """Lancia transient unit per build asincrona.

    Ritorna:
      - {ok: True, build_started: True, unit_name, eta_estimate_s, n_total}
      - {ok: True, already_running: True, progress: dict, unit_name}
      - {ok: False, error: str}
    """
    if idx not in _VALID_IDX:
        return {"ok": False, "error": f"idx must be one of {_VALID_IDX}"}
    base_path = Path(os.path.expanduser(str(base_path))).resolve()
    if not base_path.exists() or not base_path.is_dir():
        return {"ok": False, "error": f"base_path not found: {base_path}"}

    unit = _unit_name(base_path, idx)

    # Dry-run: NIENTE spawn systemd. Ritorna shape compatibile con caller
    # (ok=True, build_started=True) marcato dry_run.
    if _is_dry_run():
        return {
            "ok": True,
            "dry_run": True,
            "build_started": False,
            "would_spawn_unit": unit,
            "unit_name": unit,
            "idx": idx,
            "base_path": str(base_path),
        }

    # Se già attiva, ritorna stato corrente
    if _is_unit_active(unit):
        prog = _read_progress(base_path, idx) or {}
        return {
            "ok": True,
            "already_running": True,
            "unit_name": unit,
            "progress": prog,
        }

    # Reset eventuale stato failed precedente (idempotente, non blocca)
    if _is_unit_failed(unit):
        _systemctl_user("reset-failed", unit, timeout=3.0)

    # PYTHONPATH come property cosi' build_runner trova i moduli runtime
    # + executor.
    _rt_dir = Path(__file__).resolve().parent
    _install_root = _rt_dir.parent
    pythonpath = (
        f"{_rt_dir}:"
        f"{_install_root / 'executors' / 'create_images_indices'}:"
        "/usr/lib/python3/dist-packages:/opt/suprastructure/src"
    )
    # systemctl start non supporta --property: serve systemd-run.
    # Usiamo systemd-run --user per properties + --unit (transient).
    cmd_run = [
        "systemd-run", "--user",
        f"--unit={unit}",
        "--collect",  # garbage-collect quando done
        f"--setenv=PYTHONPATH={pythonpath}",
        "--setenv=PYTHONUNBUFFERED=1",
        f"--setenv=HOME={os.environ.get('HOME', '/home/user')}",
        "--property=KillMode=mixed",
        "--property=TimeoutStopSec=30",
        "--",
        _VENV_PYTHON,
        "-m", "build_runner",
        "--base-path", str(base_path),
        "--idx", idx,
        "--actor", actor or "host",
        "--channel", channel or "",
        "--chat-id", chat_id or "",
        "--batch-size", str(int(batch_size)),
        "--resume", "true",
    ]

    try:
        proc = subprocess.run(
            cmd_run, capture_output=True, text=True, timeout=10.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.exception("systemd-run --user fallito")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        # Caso "Unit already exists": race condition, considera già running
        if "already exists" in stderr.lower() or _is_unit_active(unit):
            prog = _read_progress(base_path, idx) or {}
            return {
                "ok": True,
                "already_running": True,
                "unit_name": unit,
                "progress": prog,
                "notice": "unit already exists",
            }
        log.error("systemd-run failed rc=%s stderr=%s", proc.returncode, stderr)
        return {"ok": False,
                 "error": f"systemd-run failed (rc={proc.returncode}): {stderr[:200]}"}

    log.info("build async started: unit=%s base=%s idx=%s", unit, base_path, idx)
    return {
        "ok": True,
        "build_started": True,
        "unit_name": unit,
        "base_path": str(base_path),
        "idx": idx,
        "actor": actor,
        "channel": channel,
    }


def get_build_status(base_path: Path | str, idx: str) -> dict | None:
    """Snapshot dello stato corrente. None se mai partito."""
    if idx not in _VALID_IDX:
        return None
    base_path = Path(os.path.expanduser(str(base_path))).resolve()
    prog = _read_progress(base_path, idx)
    unit = _unit_name(base_path, idx)
    unit_active = _is_unit_active(unit)
    if prog is None and not unit_active:
        return None
    if prog is None:
        prog = {"state": "unknown"}
    out = dict(prog)
    out["unit_name"] = unit
    out["unit_active"] = unit_active
    last_update = float(prog.get("last_update") or 0.0)
    out["last_update_age_s"] = max(0.0, time.time() - last_update) if last_update else None
    return out


def stop_build(base_path: Path | str, idx: str) -> dict:
    """systemctl --user stop + cleanup tmp orphan dirs."""
    if idx not in _VALID_IDX:
        return {"ok": False, "error": f"idx must be one of {_VALID_IDX}"}
    base_path = Path(os.path.expanduser(str(base_path))).resolve()
    unit = _unit_name(base_path, idx)
    rc, _out, err = _systemctl_user("stop", unit, timeout=10.0)
    log.info("stop_build unit=%s rc=%s", unit, rc)
    cleanup = cleanup_orphan_tmp_dirs(base_path=base_path, idx=idx, max_age_s=0)
    return {"ok": True, "unit_name": unit, "stop_rc": rc,
             "stop_err": err.strip()[:200] if err else "",
             "cleanup": cleanup}


def list_active_builds() -> list[dict]:
    """Lista builds note (progress.json esistenti). Newest first."""
    out: list[dict] = []
    if not _PROGRESS_DIR.exists():
        return out
    for fp in sorted(_PROGRESS_DIR.glob("*.json"),
                      key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        base_p = data.get("base_path")
        idx = data.get("idx")
        if not base_p or not idx:
            continue
        unit = _unit_name(Path(base_p), idx)
        active = _is_unit_active(unit)
        last_update = float(data.get("last_update") or 0.0)
        age_s = max(0.0, time.time() - last_update) if last_update else None
        item = dict(data)
        item["unit_name"] = unit
        item["unit_active"] = active
        item["last_update_age_s"] = age_s
        item["digest"] = _digest_of(Path(base_p))
        out.append(item)
    return out


def cleanup_orphan_tmp_dirs(*, base_path: Path | None = None,
                             idx: str | None = None,
                             max_age_s: float = 7 * 86400) -> dict:
    """Sweep `<idx_dir>.tmp_<rand>` orphans (no unit attivo + older than).

    Se base_path+idx forniti, sweep solo quel pair. Altrimenti sweep tutti.
    """
    swept: list[str] = []
    skipped: list[str] = []
    base_root = _index_image_root()
    if not base_root.exists():
        return {"swept": [], "skipped": []}

    targets: list[Path] = []
    if base_path is not None and idx is not None:
        idx_dir = _index_dir(base_path, idx)
        for sib in idx_dir.parent.glob(f"{idx_dir.name}.tmp_*"):
            targets.append(sib)
    else:
        for digest_dir in base_root.iterdir():
            if not digest_dir.is_dir():
                continue
            for sib in digest_dir.glob("*.tmp_*"):
                targets.append(sib)

    now = time.time()
    for t in targets:
        if not t.is_dir():
            continue
        age = now - t.stat().st_mtime
        if age < max_age_s:
            skipped.append(str(t))
            continue
        # Non rimuovere se un unit attivo punta a questo tmp_dir (controllo
        # debole: cerchiamo un progress.json running che ci punti)
        if _tmp_dir_is_in_use(t):
            skipped.append(str(t))
            continue
        try:
            shutil.rmtree(t)
            swept.append(str(t))
            log.info("cleanup_orphan_tmp_dirs: rimosso %s (age=%.0fs)", t, age)
        except OSError as e:
            log.warning("cleanup_orphan_tmp_dirs: rmtree fallito %s: %s", t, e)
            skipped.append(str(t))
    return {"swept": swept, "skipped": skipped}


def _tmp_dir_is_in_use(tmp_dir: Path) -> bool:
    """True se un progress.json running punta a questo tmp_dir."""
    if not _PROGRESS_DIR.exists():
        return False
    for fp in _PROGRESS_DIR.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("state") == "running" and data.get("tmp_dir") == str(tmp_dir):
            return True
    return False


# --- helpers privati ---------------------------------------------------------

def _read_progress(base_path: Path, idx: str) -> dict | None:
    p = _progress_path(base_path, idx)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
