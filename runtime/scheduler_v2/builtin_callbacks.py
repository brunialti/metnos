"""Builtin callbacks + default schedule for scheduler v2.

Two responsibilities:

1. `install_default_callbacks(scheduler)`: register on the daemon's
   `CallbackRegistry` every callback referenced by builtin or user jobs.
   Imports the existing v1 task functions (`task_apply_ager`, ...) and the
   user-query callback wrapper from `recurring_tasks` — does NOT
   re-implement them. Wraps zero-arg functions to the v2 callback signature
   `cb(payload: dict) -> Any`.

2. `install_default_jobs(scheduler)`: idempotent INSERT-OR-IGNORE for the
   7 builtin entries (apply_executor_ager / apply_ager / synt_suggest /
   introvertiva_propose / introvertiva_apply / proposals_cleanup /
   lifecycle_summary). Returns the number of rows actually inserted; on a
   second run with all rows already present, returns 0 and does not touch
   `last_run_at` / `total_runs` etc.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .models import ScheduleEntry
from .schedule_parser import next_fire_at as compute_next_fire


_BUILTIN_JOBS: list[dict[str, Any]] = [
    {
        "name": "apply_executor_ager",
        "trigger": "daily@03:30",
        "callback_key": "apply_executor_ager",
        "description": (
            "Decay degli executor inattivi (simmetrico ad apply_ager dei "
            "mnest): active → deprecated dopo 30g di inattivita'; "
            "deprecated → archived dopo altri 14g."
        ),
    },
    {
        "name": "apply_ager",
        "trigger": "daily@04:00",
        "callback_key": "apply_ager",
        "description": "Decay + demote + proto purge sul mnestoma.",
    },
    {
        "name": "synt_suggest",
        "trigger": "daily@04:30",
        "callback_key": "synt_suggest",
        "description": "Cascata reattiva su proto-mnest ricorrenti.",
    },
    {
        "name": "introvertiva_propose",
        "trigger": "daily@05:00",
        "callback_key": "introvertiva_propose",
        "description": (
            "Cascata introvertiva: produce proposte DEDUPE/GENERALIZE/"
            "SPECIALIZE sul corpus accumulato (no auto-apply, audit JSONL)."
        ),
    },
    {
        "name": "introvertiva_apply",
        "trigger": "daily@05:30",
        "callback_key": "introvertiva_apply",
        "description": (
            "Auto-apply specialize ad altissima confidenza (dom>=0.9, "
            "uses>=30, finestra <=14g)."
        ),
    },
    {
        "name": "proposals_cleanup",
        "trigger": "daily@06:00",
        "callback_key": "proposals_cleanup",
        "description": (
            "Manutenzione lifecycle backlog (ADR 0096): archive aged "
            "synt_proposals, dedupe candidates, auto-decay legacy_orphan."
        ),
    },
    {
        "name": "lifecycle_summary",
        "trigger": "daily@06:30",
        "callback_key": "lifecycle_summary",
        "description": (
            "Aggregatore vista unificata (ADR 0097): legge ultimi audit "
            "di executor_ager / introvertiva / proposals_cleanup."
        ),
    },
    {
        "name": "images_index_refresh",
        "trigger": "daily@03:00",
        "callback_key": "images_index_refresh",
        "description": (
            "Refresh incrementale indice immagini unificato (ADR 0117): "
            "walk + stat ~11s su 30k foto, processa solo nuove/modificate "
            "via signature (mtime,size). Modelli locali §10.3."
        ),
    },
    {
        "name": "proposals_eta_aggregate",
        "trigger": "daily@04:30",
        "callback_key": "proposals_eta_aggregate",
        "description": (
            "Aggregator delle latenze per path_shape (ADR 0122): scansiona "
            "i turn JSONL ultimi 7 giorni, calcola p50/p95 wall-clock per "
            "ogni path_shape_hash e li scrive in proposals_eta.sqlite. "
            "Usato dal proposal_evaluator per il signal eta_speedup."
        ),
    },
    {
        "name": "i18n_translate_pending",
        "trigger": "daily@02:00",
        "callback_key": "i18n_translate_pending",
        "description": (
            "Traduce fino a 20 righe pending del DB i18n via LLM tier "
            "wise (override env METNOS_I18N_QUALITY). Idempotente sul "
            "source_hash, audit JSONL append-only. Throttle GPU notturna."
        ),
    },
    {
        "name": "promoter",
        "trigger": "daily@04:45",
        "callback_key": "promoter",
        "description": (
            "Promoter daemon: valuta synth proposals via proposal_evaluator "
            "(ADR 0122), promuove gli accept in `~/.local/share/metnos/"
            "executors/<name>/` con grace 72h (override env), archivia gli "
            "reject, marca i gray come review_needed."
        ),
    },
    {
        "name": "promoter_digest",
        "trigger": "daily@07:00",
        "callback_key": "promoter_digest",
        "description": (
            "Digest Telegram delle proposte in `promoted_grace` non ancora "
            "notificate. Inline keyboard ok/rollback (ADR 0090). Cap N=10 "
            "per fire. Disabilitato via METNOS_PROMOTER_NOTIFY_ADMIN=false."
        ),
    },
]


def task_images_index_refresh() -> dict:
    """Refresh incrementale dell'indice unificato immagini (ADR 0117).

    Trigger automatico daily@03:00. Invoca `create_images_indices` con
    `force=False`: walk + stat ~11s su 30k foto, le invariate sono
    saltate via (mtime,size); le nuove/modificate passano la pipeline
    EXIF + ArcFace + VLM + BGE.
    """
    import sys as _sys
    from pathlib import Path as _P
    base = _P.home() / ".local/share/metnos/Immagini"
    if not base.exists():
        return {"ok": True, "skipped": True, "reason": f"absent: {base}"}
    _sys.path.insert(0, "/opt/myclaw/executors/create_images_indices")
    _sys.path.insert(0, "/opt/myclaw/runtime")
    import create_images_indices as _m
    return _m.invoke({
        "base_path": str(base), "force": False, "recursive": True,
    })


def task_proposals_eta_aggregate() -> dict:
    """Aggregator delle latenze per path_shape (ADR 0122).

    Trigger automatico daily@04:30. Scansiona i turn JSONL ultimi 7 giorni,
    calcola path_shape_hash + total_ms per ogni turno, scrive p50/p95 in
    `proposals_eta.sqlite`. Idempotente (rewrite full per shape).
    """
    import sys as _sys
    import time as _time
    _sys.path.insert(0, "/opt/myclaw/runtime")
    from proposals_eta_index import aggregate_from_jsonls
    since = _time.time() - 7 * 86400
    rep = aggregate_from_jsonls(since_ts=since)
    return {"ok": True, **rep}


def _wrap_zero_arg(fn: Callable[[], Any]) -> Callable[[dict | None], Any]:
    """Adapt a v1 zero-arg `task_*()` to the v2 `cb(payload)` signature."""

    def _adapter(payload: dict | None = None) -> Any:
        return fn()

    return _adapter


def install_default_callbacks(scheduler) -> None:
    """Register all builtin + user callbacks on `scheduler.callbacks`.

    Idempotent across re-installation: uses `replace=True` so re-running
    on a daemon that already has them registered does not raise.
    """
    # Task implementations live in `_v1_tasks.pyc` (bytecode frozen from
    # legacy `runtime/scheduler.py` PR7-deleted). Loaded lazily via
    # SourcelessFileLoader so we don't need to ship Python source for them.
    # TODO ADR 0112 follow-up: estrarre le 7 task functions in
    # runtime/jobs/<name>.py come sorgenti propri (ora vivono in bytecode).
    from importlib.machinery import SourcelessFileLoader
    from pathlib import Path as _P
    _v1 = SourcelessFileLoader(
        "metnos_scheduler_v1_tasks",
        str(_P(__file__).with_name("_v1_tasks.pyc")),
    ).load_module()
    task_apply_ager = _v1.task_apply_ager
    task_apply_executor_ager = _v1.task_apply_executor_ager
    task_synt_suggest = _v1.task_synt_suggest
    task_introvertiva_propose = _v1.task_introvertiva_propose
    task_introvertiva_apply = _v1.task_introvertiva_apply
    task_proposals_cleanup = _v1.task_proposals_cleanup
    task_lifecycle_summary = _v1.task_lifecycle_summary

    cb = scheduler.callbacks
    cb.register(
        "apply_executor_ager",
        _wrap_zero_arg(task_apply_executor_ager),
        "Demote/dim executor inutilizzati",
        replace=True,
    )
    cb.register(
        "apply_ager",
        _wrap_zero_arg(task_apply_ager),
        "Demote/dim mnest deboli",
        replace=True,
    )
    cb.register(
        "synt_suggest",
        _wrap_zero_arg(task_synt_suggest),
        "Suggerisci proposte synth",
        replace=True,
    )
    cb.register(
        "introvertiva_propose",
        _wrap_zero_arg(task_introvertiva_propose),
        "Genera candidati introvertiva (no apply)",
        replace=True,
    )
    cb.register(
        "introvertiva_apply",
        _wrap_zero_arg(task_introvertiva_apply),
        "Auto-apply specialize ad alta confidenza",
        replace=True,
    )
    cb.register(
        "proposals_cleanup",
        _wrap_zero_arg(task_proposals_cleanup),
        "Manutenzione lifecycle backlog (ADR 0096)",
        replace=True,
    )
    cb.register(
        "lifecycle_summary",
        _wrap_zero_arg(task_lifecycle_summary),
        "Aggregatore audit ultimi 24h (ADR 0097)",
        replace=True,
    )
    cb.register(
        "images_index_refresh",
        _wrap_zero_arg(task_images_index_refresh),
        "Refresh incrementale indice immagini unificato (ADR 0117)",
        replace=True,
    )
    cb.register(
        "proposals_eta_aggregate",
        _wrap_zero_arg(task_proposals_eta_aggregate),
        "Aggregator latenze per path_shape (ADR 0122)",
        replace=True,
    )

    # i18n_translate_pending: traduce 20 righe pending/notte (cap throttling
    # GPU). Firma nativa v2 (`cb(payload)`), niente wrapper zero-arg.
    from jobs.i18n_translate_pending import task_i18n_translate_pending
    cb.register(
        "i18n_translate_pending",
        task_i18n_translate_pending,
        "Traduce 20 righe pending del DB i18n (daily@02:00, tier wise default)",
        replace=True,
    )

    # promoter / promoter_digest: scheduler v2 daily@04:45 + daily@07:00.
    # Firma nativa v2 (cb(payload)). Vedi `runtime/jobs/promoter.py`.
    from jobs.promoter import task_promoter
    from jobs.promoter_digest import task_promoter_digest
    cb.register(
        "promoter",
        task_promoter,
        "Promoter daemon: valuta+promuove synth proposals (daily@04:45)",
        replace=True,
    )
    cb.register(
        "promoter_digest",
        task_promoter_digest,
        "Digest Telegram delle proposte promoted_grace (daily@07:00)",
        replace=True,
    )

    # User-task callback: payload is the full recurring_tasks record dict
    # (query, channel, actor, chat_id, name, label).
    from recurring_tasks import (  # type: ignore
        _run_user_query_callback,
        _wrap_with_times_tracking,
    )

    user_cb = _wrap_with_times_tracking(_run_user_query_callback)
    cb.register(
        "run_user_query",
        user_cb,
        "Esegue una query utente come turno agent + push canale",
        replace=True,
    )


def install_default_jobs(scheduler) -> int:
    """Insert builtin jobs into `schedule_entries` if absent.

    Idempotent: if a row with the same `name` already exists, it is left
    untouched (preserving `last_run_at`, `total_runs`, etc). Returns the
    number of NEW rows inserted (0 if all already present).
    """
    storage = scheduler.storage
    inserted = 0
    now = time.time()
    tz_name = getattr(scheduler, "tz_name", "Europe/Rome")
    for spec in _BUILTIN_JOBS:
        if storage.get_by_name(spec["name"]) is not None:
            continue
        nxt = compute_next_fire(spec["trigger"], now, tz_name)
        entry = ScheduleEntry(
            name=spec["name"],
            trigger=spec["trigger"],
            next_fire_at=nxt,
            recurring=True,
            callback_key=spec["callback_key"],
            origin="system",
            description=spec.get("description", ""),
        )
        storage.upsert(entry)
        inserted += 1
    if inserted:
        scheduler.kick()
    return inserted
