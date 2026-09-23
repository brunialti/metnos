# SPDX-License-Identifier: MIT
"""nightly_orchestrator.py — COMPONENTE CORE: orchestrazione manutenzione notturna.

Accorpamento (feedback 3/6): i ~14 task di housekeeping notturno erano 14 entry
separate nello scheduler (01:00–07:00), affollando la dashboard. Ora sono UNA
sola entry scheduler (`nightly_maintenance`, daily@03:00) il cui callback chiama
questo orchestratore, che li esegue IN SEQUENZA ORDINATA.

Vincoli (richiesta utente):
  - lo SCHEDULER resta com'e' (mechanism invariato): si limita a far partire UNA
    entry; tutta la logica di sequenza vive QUI, in un componente core;
  - i callback dei singoli task restano REGISTRATI (invocabili per chiave) —
    questo orchestratore non li reimplementa, li SEQUENZIA via registry.

Proprieta':
  - ORDINE rispettato (dipendenze): observer dopo materialize; reaper dopo aging;
    digest dopo promoter; consegna del refresh indice immagini per prima.
  - I callback sono sequenziali; un lavoro consegnato a LRE continua in modo
    asincrono. Le sue risorse sono regolate da LRE, non da questa sequenza.
  - ERROR-ISOLATION (§2.8): un task che fallisce NON abortisce gli altri; ogni esito
    e' catturato; ritorna un sommario {task: ok|error|missing}.
  - async-aware: invoca callback sync e async (CallbackInfo.is_async).

NON include i ricorrenti a cadenza propria (dialog_pending_sweep 1m, change_applier
10m, i18n 6h) ne' i GPU-heavy a 72h (telos_introspect, intent_retrain) ne' i task
UTENTE (i `user_*`): restano entry separate. (github_watcher RITIRATO → executor.)
"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger("metnos.nightly_orchestrator")

# Sequenza ordinata dei task housekeeping notturni (per callback_key).
# L'ordine codifica le dipendenze tra callback, non il termine dei lavori LRE.
NIGHTLY_SEQUENCE: tuple[str, ...] = (
    "images_index_refresh",      # consegna asincrona a LRE
    "change_intent_materialize",
    "change_observer",           # dopo materialize
    "nightly_aging",
    "state_reaper",              # dopo aging (reaper unico stato persistente)
    "learning_loop_review",      # W1: pota seed shadow + conteggi (ADR 0185)
    "birth_failure_reviews",     # RM-0008 F5: classifica le quarantene esatte
    "telos_synth_consume",
    "proposals_eta_aggregate",
    "introvertiva_propose",
    "promoter",
    "promoter_digest",           # dopo promoter
    "proposals_cleanup",
    "lifecycle_summary",
    "skill_sandbox_watchdog",
)


def _declared_failure(result) -> str | None:
    """Normalizza i comuni report job senza imporre una shape unica."""
    if not isinstance(result, dict):
        return None
    if result.get("workload_id") and result.get("state") in {
        "needs_attention", "failed", "completed_with_errors", "paused",
        "pause_requested", "cancel_requested", "cancelled",
    }:
        return "workload_" + result["state"]
    if result.get("ok") is False:
        return str(result.get("error_class") or result.get("error")
                   or result.get("reason") or "reported_failure")
    if result.get("partial") is True or result.get("status") == "partial":
        return str(result.get("error_class") or "partial_result")
    for field in ("fail_count", "failed", "error_count"):
        try:
            count = int(result.get(field) or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return f"{field}={count}"
    errors = result.get("errors")
    if isinstance(errors, (list, dict)) and errors:
        return f"errors={len(errors)}"
    return None


async def run_nightly(callbacks, payload: dict | None = None) -> dict:
    """Esegue la sequenza notturna invocando i callback registrati per chiave.

    `callbacks`: il CallbackRegistry del daemon (ha `.get(key) -> CallbackInfo`).
    Ritorna gli esiti dei callback e i riferimenti ai lavori LRE consegnati.
    """
    ran: dict[str, str] = {}
    workloads: dict[str, dict] = {}
    loop = asyncio.get_running_loop()
    for key in NIGHTLY_SEQUENCE:
        info = callbacks.get(key) if callbacks is not None else None
        if info is None:
            ran[key] = "missing"
            log.warning("nightly_maintenance: callback %r non registrato, skip", key)
            continue
        try:
            # Sync → offload su executor (come daemon._invoke): un callback sync
            # pesante (image refresh GPU) NON deve bloccare l'event loop per la
            # finestra notturna. Sequenziale per costruzione (un await per volta).
            if getattr(info, "is_async", False):
                result = await info.fn(None)
            else:
                result = await loop.run_in_executor(None, info.fn, None)
            if isinstance(result, dict) and result.get("workload_id"):
                # Keep only bounded operational facts, never the input,
                # prompts or full receipt. Admission is not job completion.
                workloads[key] = {name: str(result[name])[:128] for name in
                                  ("workload_id", "state") if name in result}
            reason = _declared_failure(result)
            if reason:
                ran[key] = f"error: {reason}"
                log.warning(
                    "nightly_maintenance: %s ha dichiarato fallimento: %s",
                    key, reason)
            else:
                ran[key] = ("workload: " + str(result.get("state", "unknown"))
                            if key in workloads else "ok")
                log.info("nightly_maintenance: %s %s", key, ran[key])
        except Exception as e:  # §2.8 error-isolation: un fallimento non abortisce
            ran[key] = f"error: {type(e).__name__}: {e}"
            log.warning("nightly_maintenance: %s FALLITO: %r", key, e)
    ok_count = sum(1 for v in ran.values() if v == "ok" or v.startswith("workload:"))
    fail_count = sum(1 for v in ran.values() if v.startswith("error"))
    missing_count = sum(1 for v in ran.values() if v == "missing")
    return {"ok": not (fail_count or missing_count), "ran": ran, "workloads": workloads,
            "ok_count": ok_count, "fail_count": fail_count, "missing_count": missing_count,
            "total": len(NIGHTLY_SEQUENCE)}


async def scheduled_nightly(callbacks, payload: dict | None = None):
    """Give the scheduler an explicit outcome so partial runs cannot look green."""
    from scheduler_v2.models import CallbackOutcome

    report = await run_nightly(callbacks, payload)
    return CallbackOutcome(
        status="success" if report["ok"] else "partial",
        output=json.dumps(report, ensure_ascii=True, separators=(",", ":")),
        error=None if report["ok"] else "; ".join(
            f"{key}: {value}" for key, value in report["ran"].items()
            if value == "missing" or value.startswith("error:")
        ),
    )
