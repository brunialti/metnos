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
    {
        "name": "skill_sandbox_watchdog",
        "trigger": "daily@06:35",
        "callback_key": "skill_sandbox_watchdog",
        "description": (
            "Mini-version Fase C (ADR 0140): controlla soglia trigger "
            "per sandbox per-skill enforcement (>= 5 skill third-party "
            "OR >= 1 guest paired). Se triggered, notifica admin via "
            "Telegram per attivare Fase C full."
        ),
    },
    {
        "name": "github_watcher",
        "trigger": "every_30m",
        "callback_key": "github_watcher",
        "description": (
            "Fase D GitHub provider: scansiona ogni 30 min i repo "
            "monitorati (~/.config/metnos/github_watched_repos.json), "
            "rileva nuovi issue/PR/commenti, applica dedup semantic "
            "BGE-M3 e o auto-risponde (4-AND safety) o apre dialog "
            "Stage 2 al host. Default config vuota = no-op."
        ),
    },
    {
        "name": "multi_tool_maintenance",
        "trigger": "daily@04:30",
        "callback_key": "multi_tool_maintenance",
        "description": (
            "Housekeeping fast-path L2 multi-tool (ADR 0150): expire "
            "stale entries (TTL N giorni di attivita' effettiva, default "
            "30) + promote pipelines mature (uses>=K_synth, default 50) "
            "a proto-mnest in mnestoma per synth_request. Idempotente."
        ),
    },
    {
        "name": "change_intent_materialize",
        "trigger": "daily@01:00",
        "callback_key": "change_intent_materialize",
        "description": (
            "Materializer unificato (ADR 0158): proietta 6 storage legacy "
            "(telos jsonl, introvertiva sqlite, synt jsonl, multi_tool, "
            "canonical_query_log, turn_feedback) in change_intents.sqlite. "
            "Idempotente via fingerprint (dedup cross-source + bump "
            "convergence). Eseguito daily@01:00 prima delle altre task "
            "notturne, cosi' le UI vedono dati freschi al risveglio."
        ),
    },
    {
        "name": "change_applier",
        "trigger": "every_10m",
        "callback_key": "change_applier",
        "description": (
            "Applier daemon (ADR 0158): legge change_intents in stato "
            "ACCEPTED, applica fisicamente per kind (create_executor → "
            "synth, extend_executor → manifest patch, dedupe → alias, "
            "materialize_pipeline → multi_tool active, cache_pattern → "
            "canonical_query_log active, reject_pattern → blocklist "
            "jsonl). Cap 20 intent per fire."
        ),
    },
    {
        "name": "praxis_template_refresh",
        "trigger": "daily@04:00",
        "callback_key": "praxis_template_refresh",
        "description": (
            "Refresh skill con template_issue=1 (ADR 0161 ext): Mētis "
            "re-propose framework escludendo fw_hash corrente, salva, "
            "marca template_issue=0. Safety net per format_fail rilevati "
            "ma non rigenerati immediatamente (retry inline disabilitato "
            "o fallito). Idempotente: salta skill template_issue=0."
        ),
    },
    {
        "name": "praxis_cluster_merge",
        "trigger": "daily@03:30",
        "callback_key": "praxis_cluster_merge",
        "description": (
            "Consolidation cluster piccoli (ADR 0162): rivisita cluster "
            "singleton/piccoli, find_neighbors centroide, LLM judge merge "
            "se cosine 0.75-0.90 + same intent. Cap 20 pair/fire. "
            "Risolve frammentazione iniziale Praxis."
        ),
    },
    {
        "name": "change_observer",
        "trigger": "daily@03:15",
        "callback_key": "change_observer",
        "description": (
            "Observer daemon (ADR 0158): verifica APPLIED + OBSERVED. "
            "Per kind, calcola metrics (executor_stats / cache state / "
            "feedback storico) e transiziona a FINALIZED (oltre grace 7gg "
            "OK) o ROLLED_BACK (fail_rate alto / cache demoted / nuovi "
            "feedback negativi). Cap 200 intent per fire."
        ),
    },
    {
        "name": "telos_introspect_nightly",
        "trigger": "daily@02:30",
        "callback_key": "telos_introspect_nightly",
        "description": (
            "Telos engine: 10 lenti laterali (scamper/oulipo/inverse_rl/"
            "endgame_book/analogy_transfer/boden_transformational/"
            "pattern_language/generative_design/counterfactual/"
            "constitutional) su tutti i telos dichiarati. Opt-in via "
            "env METNOS_TELOS_NIGHTLY=1 (default OFF). Output: "
            "~/.local/share/metnos/telos_proposals.jsonl (ADR 0156)."
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
    import os as _os
    import sys as _sys
    from pathlib import Path as _P
    base = _P.home() / ".local/share/metnos/Immagini"
    if not base.exists():
        return {"ok": True, "skipped": True, "reason": f"absent: {base}"}
    # runtime/ già su sys.path (builtin_callbacks VIVE in runtime/scheduler_v2/).
    # Per importare create_images_indices.py serve il path del suo dir executor.
    _rt = _os.environ.get("METNOS_RUNTIME") or next(
        str(p / "runtime") for p in _P(__file__).resolve().parents
        if (p / "runtime" / "config.py").is_file())
    _exec_dir = str(_P(_rt).parent / "executors" / "create_images_indices")
    if _exec_dir not in _sys.path:
        _sys.path.insert(0, _exec_dir)
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
    import time as _time
    # runtime/ già su sys.path (builtin_callbacks VIVE in runtime/scheduler_v2/).
    from proposals_eta_index import aggregate_from_jsonls
    since = _time.time() - 7 * 86400
    rep = aggregate_from_jsonls(since_ts=since)
    return {"ok": True, **rep}


def _wrap_zero_arg(fn: Callable[[], Any]) -> Callable[[dict | None], Any]:
    """Adapt a v1 zero-arg `task_*()` to the v2 `cb(payload)` signature."""

    def _adapter(payload: dict | None = None) -> Any:
        return fn()

    return _adapter


def task_temp_threshold_alert(payload: dict | None = None) -> dict:
    """Deterministic threshold alert §7.9 — niente LLM.

    Legge le temperature hardware via `host_health.collect_thermal()` (CPU
    GPU NVMe) e invia notifica al canale dichiarato se ALMENO una supera
    `threshold_c`. Sotto soglia: noop silenzioso. Risolve il bug live
    24/5/2026 (PLANNER LLM Gemma 4 26B inviava alarm anche con
    temperature < threshold per pattern condizionale ambiguo).

    Payload schema:
        {
          "threshold_c": int|float,        # default 80
          "channel":     str,              # "telegram" | "email" (default "telegram")
          "chat_id":     str,              # Telegram chat_id destinatario
          "to":          str,              # email destinatario (se channel="email")
          "label":       str,              # opzionale, prefisso messaggio
          "min_pause_s": int,              # opzionale, anti-flap (default 1800 = 30min)
        }

    Anti-flap: se l'ultima notifica per la STESSA combinazione (chat_id,
    threshold) e' stata inviata < min_pause_s secondi fa, skip. State in
    `<PATH_USER_STATE>/temp_threshold_alert.json` (last_sent_ts per key).

    Idempotente. Cross-tier (telegram/email). Estendibile a altre metriche
    via subclass payload (RAM, disk) — pattern §7.3 generale.
    """
    import json
    import time as _time
    from pathlib import Path as _P

    payload = payload or {}
    threshold_c = float(payload.get("threshold_c") or 80)
    channel = (payload.get("channel") or "telegram").lower()
    chat_id = payload.get("chat_id") or ""
    to_addr = payload.get("to") or ""
    label = payload.get("label") or "Allerta Temperatura Hardware"
    min_pause_s = int(payload.get("min_pause_s") or 1800)

    if channel == "telegram" and not chat_id:
        return {"ok": False, "error": "channel=telegram richiede chat_id"}
    if channel == "email" and not to_addr:
        return {"ok": False, "error": "channel=email richiede to"}

    # Anti-flap state
    import config as _C
    state_path = _C.PATH_USER_STATE / "temp_threshold_alert.json"
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            state = {}
    state_key = f"{channel}:{chat_id or to_addr}:{int(threshold_c)}"
    last_ts = float(state.get(state_key) or 0)
    now = _time.time()

    # Collect thermal — single source of truth from host_health (no
    # duplicate sensor parsing logic, ADR 0098+0108 pattern).
    from host_health import collect_thermal
    thermal = collect_thermal()
    if not thermal.get("available"):
        return {"ok": True, "skipped": "no_thermal_sensors",
                "thermal": thermal}

    # Componenti misurati e relativi label canonical.
    components = [
        ("cpu_c", "CPU"),
        ("gpu_c", "GPU"),
        ("nvme_c", "NVMe"),
    ]
    over_threshold = []
    snapshot = {}
    for key, comp_label in components:
        val = thermal.get(key)
        if val is None:
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        snapshot[comp_label] = v
        if v >= threshold_c:
            over_threshold.append((comp_label, v))

    if not over_threshold:
        return {"ok": True, "skipped": "below_threshold",
                "threshold_c": threshold_c, "snapshot": snapshot}

    # Anti-flap: sotto soglia minima → skip.
    if last_ts > 0 and (now - last_ts) < min_pause_s:
        return {"ok": True, "skipped": "anti_flap",
                "last_sent_age_s": int(now - last_ts),
                "min_pause_s": min_pause_s,
                "over_threshold": over_threshold}

    # Build message
    over_str = " · ".join(f"{c} {v:.0f}°C" for c, v in over_threshold)
    snap_str = " · ".join(f"{c} {v:.0f}°C" for c, v in snapshot.items())
    body = (f"{label}\n\n"
             f"Soglia: {int(threshold_c)}°C\n"
             f"Componente sopra: {over_str}\n"
             f"Snapshot: {snap_str}")

    # Dispatch send (deterministic, no PLANNER)
    if channel == "telegram":
        from backends.messages import telegram_bot
        send_res = telegram_bot.send({
            "messages": [{"recipient_id": chat_id, "body": body}],
        })
    else:  # email
        from backends.messages import email_metnos
        send_res = email_metnos.send({
            "messages": [{"to": to_addr,
                          "subject": label,
                          "body": body}],
            "account": "metnos_system",
        })

    # Update state if send ok
    if send_res.get("ok"):
        state[state_key] = now
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state))
        except OSError:
            pass

    return {"ok": send_res.get("ok", False),
            "threshold_c": threshold_c,
            "over_threshold": over_threshold,
            "snapshot": snapshot,
            "send_result": send_res}


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
    cb.register(
        "temp_threshold_alert",
        task_temp_threshold_alert,
        "Alert deterministic se temperatura HW supera soglia (24/5/2026)",
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

    # Change intent materializer (ADR 0158): proietta 6 storage legacy in
    # change_intents.sqlite. Idempotente via fingerprint cross-source dedup.
    from jobs.change_intent_materialize import task_change_intent_materialize
    cb.register(
        "change_intent_materialize",
        task_change_intent_materialize,
        "Materializer unificato change_intents (ADR 0158, daily@01:00)",
        replace=True,
    )

    # Change applier (ADR 0158): legge ACCEPTED, applica fisicamente.
    from change_applier import task_change_applier
    cb.register(
        "change_applier",
        task_change_applier,
        "Applier change_intents accepted → applied (ADR 0158, every_10m)",
        replace=True,
    )

    # Change observer (ADR 0158, Fase 3): monitora APPLIED → finalize/rollback.
    from change_observer import task_change_observer
    cb.register(
        "change_observer",
        task_change_observer,
        "Observer change_intents applied → finalized|rolled_back (ADR 0158, daily@03:15)",
        replace=True,
    )

    # Telos synth consumer (C.8 fase 2, 24/5/2026): processa i marker
    # synt_pending creati da `proposal_actions.on_accept`, chiama
    # `synth_request.handle_synth_request` per ognuno fino al cap giornaliero
    # (`telos.synth_daily_cap`, default 3). Schedula daily@03:30.
    from telos_synth_consumer import task_telos_synth_consume
    cb.register(
        "telos_synth_consume",
        task_telos_synth_consume,
        "Consumer marker synt_pending → handle_synth_request (C.8 fase 2, daily@03:30)",
        replace=True,
    )

    # Multi-tool fast-path promotion L2 → L3 (ADR 0150 19/5/2026 v4):
    # daily@04:30 scan multi_tool_paths uses>=K_synth (default 50) e crea
    # proto-mnest in mnestoma. Firma nativa v2.
    # Multi-tool fast-path housekeeping unificato (ADR 0150 v6).
    # Un singolo job daily che fa cleanup + promote in sequenza sullo stesso
    # sqlite. Order: expire stale PRIMA, poi promote — cosi' non promuoviamo
    # entries che stiamo per buttare.
    def _task_multi_tool_maintenance(payload=None):
        from multi_tool_paths import expire_stale_paths
        from jobs.multi_tool_promote import task_multi_tool_promote
        expired = expire_stale_paths()
        promoted = task_multi_tool_promote(payload or {})
        return {"ok": True, "expired": expired,
                "promoted": promoted.get("promoted", 0),
                "skipped": promoted.get("skipped", 0),
                "errors": promoted.get("errors", [])}
    cb.register(
        "multi_tool_maintenance",
        _task_multi_tool_maintenance,
        "Housekeeping unificato L2: expire stale + promote a proto-mnest (ADR 0150)",
        replace=True,
    )

    # Praxis template refresh nightly (ADR 0161 ext, 26/5/2026).
    # daily@04:00 safety net per skill con template_issue=1.
    def _task_praxis_template_refresh():
        from jobs.praxis_template_refresh import run as _run
        return _run()
    cb.register(
        "praxis_template_refresh",
        _task_praxis_template_refresh,
        "Refresh template skill con template_issue=1 (ADR 0161 ext)",
        replace=True,
    )

    # Praxis cluster merge nightly (ADR 0162, 26/5/2026).
    # daily@03:30 consolidation cluster singleton via LLM judge.
    def _task_praxis_cluster_merge():
        from jobs.praxis_cluster_merge import run as _run
        return _run()
    cb.register(
        "praxis_cluster_merge",
        _task_praxis_cluster_merge,
        "Merge cluster piccoli via LLM judge (ADR 0162)",
        replace=True,
    )

    # Sandbox watchdog soglia (mini-version Fase C, ADR 0140).
    # daily@06:35 controlla #skill third-party + #guest paired,
    # notifica admin se trigger superato. Firma zero-arg.
    from jobs.skill_sandbox_watchdog import task_skill_sandbox_watchdog
    cb.register(
        "skill_sandbox_watchdog",
        _wrap_zero_arg(task_skill_sandbox_watchdog),
        "Watchdog soglia per attivare Fase C sandbox per-skill (ADR 0140)",
        replace=True,
    )

    # GitHub watcher Fase D: every_30m scan dei repo monitorati con
    # dedup semantic BGE-M3. Firma nativa v2 (cb(payload)).
    from jobs.github_watcher import task_github_watcher
    cb.register(
        "github_watcher",
        task_github_watcher,
        "Watcher GitHub repo monitorati con dedup semantic (Fase D)",
        replace=True,
    )

    # Telos engine nightly introspection (ADR 0156, 21/5/2026 v8).
    # Esegue le 10 lenti laterali su tutti i telos dichiarati, produce
    # proposte in `~/.local/share/metnos/telos_proposals.jsonl`.
    # Opt-in via env METNOS_TELOS_NIGHTLY=1 (default OFF) per evitare
    # auto-run prima che la review utente sia wired (next session).
    def _task_telos_introspect_nightly(payload=None):
        import os
        if os.environ.get("METNOS_TELOS_NIGHTLY", "0") != "1":
            return {"ok": True, "skipped": True,
                    "reason": "METNOS_TELOS_NIGHTLY=0 (opt-in)"}
        from telos_introspect import run_all_telos
        from telos_lenses import LENSES
        # Forza tutte le 10 lenti attive: in modalita' nightly ignoriamo
        # i toggle per-lens individuali.
        summary = run_all_telos(lenses=list(LENSES.keys()), persist=True)
        return {"ok": True, **summary}
    cb.register(
        "telos_introspect_nightly",
        _task_telos_introspect_nightly,
        "Telos engine: 10 lenti laterali su tutti i telos (ADR 0156)",
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
