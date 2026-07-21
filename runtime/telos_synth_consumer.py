# SPDX-License-Identifier: AGPL-3.0-only
"""telos_synth_consumer.py — Consumer dei marker `synt_pending/*.json`.

Pipeline accept→synt_request (C.8 fase 2, 24/5/2026). Le proposte
introspettive accettate dalla dashboard `/admin/proposals/telos` lasciano
un marker JSON in `<USER_DATA>/proposal_accepts/synt_pending/<sig>.json`
(via `proposal_actions.on_accept`). Questo modulo:

  1. Legge i marker pendenti.
  2. Applica rate limit `telos.synth_daily_cap` (settings).
  3. Ordina per `expected_alignment` desc (FIFO inside same score).
  4. Per ognuno chiama `synth_request.handle_synth_request(args)` con
     `expected_name` + `intent` dal marker.
  5. Sposta marker in `processed/` (success, candidate o failure dal nome
     `<sig>.<status>.json`).

Determinismo §7.9 nella scelta + rate limit. Synth interno e' LLM
(stage 5 wise) — il consumer NON e' real-time, gira via scheduler v2
ogni N ore (default daily@03:30).

API:
    run_once(*, dry_run=False, max_jobs=None) -> dict
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from logging_setup import get_logger
import config as _C  # §7.11

log = get_logger(__name__)

SYNT_PENDING_DIR = _C.PATH_USER_DATA / "proposal_accepts" / "synt_pending"
SYNT_PROCESSED_DIR = _C.PATH_USER_DATA / "proposal_accepts" / "synt_processed"
SYNT_AUDIT_LOG = _C.PATH_USER_DATA / "telos_synth_consumer.jsonl"


def _today_key() -> str:
    """YYYY-MM-DD per rate limiting daily."""
    return time.strftime("%Y-%m-%d", time.localtime())


def _count_today_processed() -> int:
    """Quanti marker processati oggi (success o failure)."""
    if not SYNT_PROCESSED_DIR.is_dir():
        return 0
    today = _today_key()
    n = 0
    for p in SYNT_PROCESSED_DIR.iterdir():
        if not p.is_file():
            continue
        # Marker nome: <sig>.<status>.json; mtime per today filter.
        try:
            mtime_day = time.strftime("%Y-%m-%d",
                                       time.localtime(p.stat().st_mtime))
            if mtime_day == today:
                n += 1
        except OSError:
            continue
    return n


def _load_pending() -> list[dict]:
    """Legge tutti i marker pending. Aggiunge `_path` per move successivo."""
    if not SYNT_PENDING_DIR.is_dir():
        return []
    out = []
    for p in sorted(SYNT_PENDING_DIR.iterdir()):
        if not p.is_file() or p.suffix != ".json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("synt_pending unreadable %s: %s", p.name, e)
            continue
        data["_path"] = str(p)
        out.append(data)
    return out


def _move_processed(src_path: str, sig: str, status: str,
                    result: Optional[dict] = None) -> Path:
    """Sposta marker in processed/<sig>.<status>.json + side-car result."""
    SYNT_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dst = SYNT_PROCESSED_DIR / f"{sig}.{status}.json"
    src = Path(src_path)
    try:
        if src.exists():
            src.rename(dst)
    except OSError as e:
        log.warning("processed move failed %s -> %s: %s", src, dst, e)
        return dst
    if result is not None:
        try:
            result_path = SYNT_PROCESSED_DIR / f"{sig}.{status}.result.json"
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("processed result write failed %s: %s", sig, e)
    return dst


def _audit(payload: dict) -> None:
    """Append-only audit log per consumer."""
    try:
        SYNT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SYNT_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("audit write failed: %s", e)


def run_once(*, dry_run: bool = False, max_jobs: Optional[int] = None) -> dict:
    """Processa i marker pending fino al cap giornaliero.

    Args:
        dry_run: se True, NON chiama synth (solo conta + lista).
        max_jobs: override del cap (test). Default da runtime_settings.

    Ritorna dict con stats: scanned, eligible, processed, skipped_rate_limit,
                            success, failed, dry_run.
    """
    # Rate limit daily (settings)
    if max_jobs is None:
        # get_int esiste e applica il default da _DEFAULTS (3): `get` non
        # esisteva → ImportError mascherato → config sempre ignorata (bug 20/6).
        from runtime_settings import get_int
        daily_cap = get_int("telos.synth_daily_cap")
    else:
        daily_cap = max_jobs

    pending = _load_pending()
    today_done = _count_today_processed()
    budget = max(0, daily_cap - today_done)

    # Sort per expected_alignment desc (priority queue)
    pending.sort(
        key=lambda d: float(d.get("expected_alignment") or 0.0),
        reverse=True,
    )

    stats = {
        "scanned": len(pending),
        "eligible": len(pending),
        "processed": 0,
        "skipped_rate_limit": 0,
        "success": 0,
        "failed": 0,
        "dry_run": dry_run,
        "today_done": today_done,
        "daily_cap": daily_cap,
        "budget": budget,
    }

    if not pending:
        return stats
    if budget == 0:
        stats["skipped_rate_limit"] = len(pending)
        log.info("synth consumer: daily cap %d already reached today", daily_cap)
        return stats

    to_process = pending[:budget]
    stats["skipped_rate_limit"] = max(0, len(pending) - budget)

    if dry_run:
        stats["processed"] = 0
        log.info("synth consumer dry_run: would process %d markers", len(to_process))
        return stats

    # Lazy import per evitare cost di import se non si processa nulla.
    from synth_request import handle_synth_request

    for marker in to_process:
        sig = marker.get("sig", "")
        expected_name = marker.get("expected_name", "")
        intent = marker.get("intent", "")
        prop_id = marker.get("prop_id", "")
        src_path = marker.get("_path", "")
        if not expected_name or not intent:
            log.warning("synth marker incomplete sig=%s name=%r intent=%r",
                         sig, expected_name, intent)
            _move_processed(src_path, sig, "invalid",
                            {"error": "missing expected_name or intent"})
            stats["processed"] += 1
            stats["failed"] += 1
            continue
        log.info("synth consumer processing sig=%s name=%s", sig, expected_name)
        t0 = time.time()
        try:
            result = handle_synth_request(
                {"expected_name": expected_name, "intent": intent},
                user_query=intent,
            )
        except Exception as e:  # noqa: BLE001 — bound failures sicuri
            log.exception("synth consumer exception for sig=%s: %s", sig, e)
            result = {"ok": False, "error": f"exception: {type(e).__name__}: {e}"}

        elapsed = time.time() - t0
        ok = bool(result.get("ok"))
        installed = bool(result.get("installed"))
        status = "success" if (ok and installed) else "failed"
        if ok and result.get("candidate_created"):
            status = "candidate"
        if ok and not installed and result.get("synthesized") is False:
            # Short-circuit no-synth (already in catalog, redirected, l7).
            status = "noop"

        _move_processed(src_path, sig, status, result)
        _audit({
            "ts": time.time(), "sig": sig, "prop_id": prop_id,
            "expected_name": expected_name,
            "elapsed_s": round(elapsed, 2),
            "status": status, "result_keys": list(result.keys()),
        })
        stats["processed"] += 1
        if status in {"success", "candidate"}:
            stats["success"] += 1
        elif status == "noop":
            stats["success"] += 1
        else:
            stats["failed"] += 1

    log.info("synth consumer done: %s", stats)
    return stats


# Scheduler v2 callback wrapper (deterministic §7.9, zero-arg).
def task_telos_synth_consume(payload: Optional[dict] = None) -> dict:
    """Callback per scheduler v2. Chiama run_once() con default settings."""
    return run_once()
