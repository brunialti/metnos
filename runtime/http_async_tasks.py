"""http_async_tasks — task asincroni del daemon HTTP per build indici
(ADR 0093, 6/5/2026).

Tre task on_startup:
  1. progress_healthcheck_task — ogni 30s, kill build stale (>5min senza
     update);
  2. notification_dispatcher_task — ogni 10s, legge marker complete e
     invia notifica via send_messages all'actor;
  3. tmpcache_sweeper_task — ogni 24h, sweep orphan .tmp_* dirs e archive
     completion markers.

Tutti i task: try/except + log; se crashano, vengono restartati al boot
del daemon. Niente persistenza ad-hoc: tutto via filesystem (idempotente).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import build_orchestrator
from logging_setup import get_logger
import config as _C  # §7.11

log = get_logger(__name__)


_COMPLETE_DIR = Path("/tmp/metnos_build_complete")
_COMPLETE_ARCHIVE = _C.PATH_USER_STATE / "build_completed_archive"
_PROGRESS_DIR = _C.PATH_USER_STATE / "build_progress"

# Configurazione task (override via env per test)
_HEALTHCHECK_INTERVAL_S = float(os.environ.get("METNOS_BUILD_HEALTHCHECK_INTERVAL_S", "30"))
_DISPATCHER_INTERVAL_S = float(os.environ.get("METNOS_BUILD_DISPATCHER_INTERVAL_S", "10"))
_SWEEPER_INTERVAL_S = float(os.environ.get("METNOS_BUILD_SWEEPER_INTERVAL_S", str(24 * 3600)))
# Dialog get_inputs scaduti: cadenza 60s (bug pre-esistente: `dialog_pending`
# sweep "funzione scritta, mai chiamata in produzione" → i file restavano).
_DIALOG_SWEEP_INTERVAL_S = float(os.environ.get("METNOS_DIALOG_SWEEP_INTERVAL_S", "60"))
_STALE_THRESHOLD_S = float(os.environ.get("METNOS_BUILD_STALE_S", "300"))
_TMP_MAX_AGE_S = float(os.environ.get("METNOS_BUILD_TMP_MAX_AGE_S", str(7 * 86400)))
_ARCHIVE_MAX_AGE_S = float(os.environ.get("METNOS_BUILD_ARCHIVE_MAX_AGE_S", str(30 * 86400)))


# --- Task 1: healthcheck ----------------------------------------------------

async def progress_healthcheck_task(app) -> None:
    """Loop infinito: stale build detection + abort marking."""
    log.info("build_healthcheck started (interval=%.0fs, stale=%.0fs)",
              _HEALTHCHECK_INTERVAL_S, _STALE_THRESHOLD_S)
    while True:
        try:
            await asyncio.sleep(_HEALTHCHECK_INTERVAL_S)
            run_healthcheck_once()
        except asyncio.CancelledError:
            log.info("build_healthcheck cancelled")
            return
        except Exception:
            log.exception("build_healthcheck tick error")


def run_healthcheck_once() -> dict:
    """Una passata di healthcheck. Esposta per test."""
    if not _PROGRESS_DIR.exists():
        return {"checked": 0, "stale_killed": 0, "aborted": 0}
    checked = 0
    stale_killed = 0
    aborted = 0
    now = time.time()
    for fp in _PROGRESS_DIR.glob("*.json"):
        checked += 1
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        state = data.get("state")
        if state in ("done", "error", "interrupted", "aborted"):
            continue
        last_update = float(data.get("last_update") or 0)
        age = now - last_update if last_update else 0
        base_p = data.get("base_path")
        idx = data.get("idx")
        if not base_p or not idx:
            continue

        unit_active = False
        try:
            unit_active = build_orchestrator._is_unit_active(
                build_orchestrator._unit_name(Path(base_p), idx)
            )
        except Exception:
            pass

        # Caso 1: stale e unit ancora attivo → kill
        if age > _STALE_THRESHOLD_S and unit_active:
            log.warning("healthcheck: build stale base=%s idx=%s age=%.0fs → stop",
                         base_p, idx, age)
            try:
                build_orchestrator.stop_build(Path(base_p), idx)
                stale_killed += 1
                _mark_progress_state(fp, "aborted",
                                      reason=f"stale {age:.0f}s")
            except Exception:
                log.exception("healthcheck: stop_build failed")
            continue
        # Caso 2: state=running ma unit non attivo → mark aborted
        if state == "running" and not unit_active:
            log.warning("healthcheck: orphan progress base=%s idx=%s "
                         "(state=running, unit dead) → aborted", base_p, idx)
            _mark_progress_state(fp, "aborted",
                                  reason="unit not active")
            aborted += 1
    return {"checked": checked, "stale_killed": stale_killed, "aborted": aborted}


def _mark_progress_state(fp: Path, state: str, *, reason: str = "") -> None:
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    data["state"] = state
    if reason:
        data["state_reason"] = reason
    data["last_update"] = time.time()
    tmp = fp.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(fp)
    except OSError as e:
        log.warning("_mark_progress_state: write fallito %s: %s", fp, e)


# --- Task 2: notification dispatcher ----------------------------------------

async def notification_dispatcher_task(app) -> None:
    """Legge marker complete e invia notifica via send_messages."""
    log.info("build_dispatcher started (interval=%.0fs)", _DISPATCHER_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_DISPATCHER_INTERVAL_S)
            run_dispatcher_once()
        except asyncio.CancelledError:
            log.info("build_dispatcher cancelled")
            return
        except Exception:
            log.exception("build_dispatcher tick error")


def run_dispatcher_once() -> dict:
    """Una passata di dispatch. Esposta per test."""
    if not _COMPLETE_DIR.exists():
        return {"dispatched": 0, "failures": 0}
    dispatched = 0
    failures = 0
    for fp in sorted(_COMPLETE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            failures += 1
            try:
                fp.unlink()
            except OSError:
                pass
            continue
        ok = _dispatch_notification(data)
        # Sposta nell'archivio (anche se la notifica fallisce; audit trail)
        _archive_marker(fp, data, dispatched_ok=ok)
        if ok:
            dispatched += 1
        else:
            failures += 1
    return {"dispatched": dispatched, "failures": failures}


def _dispatch_notification(data: dict) -> bool:
    """Compose msg e invoca send_messages. True se invio completo."""
    actor = data.get("actor") or "host"
    channel = data.get("channel") or ""
    base_path = data.get("base_path") or "?"
    idx = data.get("idx") or "?"
    n_entries = int(data.get("n_entries") or 0)
    duration_s = float(data.get("duration_s") or 0)
    errors = int(data.get("errors_count") or 0)
    ok = bool(data.get("ok", True))

    minutes = int(duration_s // 60)
    seconds = int(duration_s % 60)
    from messages import get as _msg  # §11 i18n
    if ok:
        body = _msg("MSG_BUILD_INDEX_OK", base_path=base_path, idx=idx,
                    n_entries=n_entries, minutes=minutes, seconds=seconds,
                    errors=errors)
    else:
        body = _msg("MSG_BUILD_INDEX_FAIL", base_path=base_path, idx=idx,
                    errors=errors)

    try:
        # Invoca send_messages come executor-puro: import diretto, no PLANNER.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                                 / "executors" / "send_messages"))
        import send_messages as _sm  # type: ignore
        msg = {
            "to_user": actor,
            "subject": _msg("MSG_BUILD_INDEX_SUBJECT"),
            "body": body,
        }
        if channel:
            msg["via_channel"] = channel
        out = _sm.invoke({"messages": [msg]})
        if not isinstance(out, dict) or not out.get("ok"):
            log.warning("dispatch: send_messages non-ok: %s",
                        json.dumps(out, ensure_ascii=False)[:300]
                        if isinstance(out, dict) else str(out)[:300])
            return False
        sent = int(out.get("ok_count") or 0)
        return sent > 0
    except Exception:
        log.exception("dispatch: send_messages crash")
        return False


def _archive_marker(fp: Path, data: dict, *, dispatched_ok: bool) -> None:
    _COMPLETE_ARCHIVE.mkdir(parents=True, exist_ok=True)
    digest = fp.stem  # già nella forma <digest>_<idx>
    ts = int(time.time())
    archive_name = f"{digest}_{ts}.json"
    if not dispatched_ok:
        archive_name = f"{digest}_{ts}_undelivered.json"
    dst = _COMPLETE_ARCHIVE / archive_name
    try:
        shutil.move(str(fp), str(dst))
    except OSError:
        try:
            fp.unlink()
        except OSError:
            pass


# --- Task 3: tmp cache sweeper ----------------------------------------------

async def tmpcache_sweeper_task(app) -> None:
    """Sweep tmp orphan dirs + completion archive scaduti."""
    log.info("build_sweeper started (interval=%.0fs)", _SWEEPER_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_SWEEPER_INTERVAL_S)
            run_sweeper_once()
        except asyncio.CancelledError:
            log.info("build_sweeper cancelled")
            return
        except Exception:
            log.exception("build_sweeper tick error")


def run_sweeper_once() -> dict:
    """Una passata di sweep."""
    tmp_res = build_orchestrator.cleanup_orphan_tmp_dirs(
        max_age_s=_TMP_MAX_AGE_S,
    )
    archive_swept = 0
    if _COMPLETE_ARCHIVE.exists():
        now = time.time()
        for fp in _COMPLETE_ARCHIVE.glob("*.json"):
            try:
                age = now - fp.stat().st_mtime
            except OSError:
                continue
            if age > _ARCHIVE_MAX_AGE_S:
                try:
                    fp.unlink()
                    archive_swept += 1
                except OSError:
                    pass
    log.info("sweeper: tmp_swept=%d archive_swept=%d",
              len(tmp_res.get("swept", [])), archive_swept)
    return {
        "tmp": tmp_res,
        "archive_swept": archive_swept,
    }


# --- Task 4: dialog get_inputs sweeper --------------------------------------

async def dialog_sweeper_task(app) -> None:
    """Rimuove i dialoghi `get_inputs` scaduti (TTL) ogni 60s. Senza questo i
    file restavano su disco (bug pre-esistente: sweep mai schedulato). I
    descrittori degli ABBANDONATI attivi sono loggati: aggancio futuro alla
    notifica utente (§2.8 «scaduto senza feedback»)."""
    log.info("dialog_sweeper started (interval=%.0fs)", _DIALOG_SWEEP_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_DIALOG_SWEEP_INTERVAL_S)
            import dialog_pending
            abandoned = dialog_pending.sweep_expired()
            if abandoned:
                log.info("dialog_sweeper: %d dialoghi scaduti rimossi (abbandonati: %s)",
                         len(abandoned), [a.get("title") for a in abandoned][:5])
        except asyncio.CancelledError:
            log.info("dialog_sweeper cancelled")
            return
        except Exception:
            log.exception("dialog_sweeper tick error")


# --- registrazione lifecycle ------------------------------------------------

def register_async_tasks(app) -> None:
    """Aggancia i task on_startup. Cancellati on_shutdown."""
    async def _start_tasks(app):
        app["build_healthcheck_task"] = asyncio.create_task(
            progress_healthcheck_task(app)
        )
        app["build_dispatcher_task"] = asyncio.create_task(
            notification_dispatcher_task(app)
        )
        app["build_sweeper_task"] = asyncio.create_task(
            tmpcache_sweeper_task(app)
        )
        app["dialog_sweeper_task"] = asyncio.create_task(
            dialog_sweeper_task(app)
        )

    async def _stop_tasks(app):
        for key in ("build_healthcheck_task", "build_dispatcher_task",
                     "build_sweeper_task", "dialog_sweeper_task"):
            t = app.get(key)
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    app.on_startup.append(_start_tasks)
    app.on_shutdown.append(_stop_tasks)
