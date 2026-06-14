# SPDX-License-Identifier: AGPL-3.0-only
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
    digest dopo promoter; refresh indice immagini (GPU-heavy) per primo e da solo.
  - GPU-SAFE per costruzione: esecuzione sequenziale (un task alla volta) → mai
    due task GPU-heavy in parallelo (supera lo staggering a orari fissi, ADR 0167).
  - ERROR-ISOLATION (§2.8): un task che fallisce NON aborta gli altri; ogni esito
    e' catturato; ritorna un sommario {task: ok|error|missing}.
  - async-aware: invoca callback sync e async (CallbackInfo.is_async).

NON include i ricorrenti a cadenza propria (dialog_pending_sweep 1m, change_applier
10m, i18n 6h) ne' i GPU-heavy a 72h (telos_introspect, intent_retrain) ne' i task
UTENTE (i `user_*`): restano entry separate. (github_watcher RITIRATO → executor.)
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("metnos.nightly_orchestrator")

# Sequenza ordinata dei task housekeeping notturni (per callback_key).
# L'ordine codifica le dipendenze + mette il GPU-heavy per primo e isolato.
NIGHTLY_SEQUENCE: tuple[str, ...] = (
    "images_index_refresh",      # GPU-heavy: per primo, da solo (sequenziale)
    "change_intent_materialize",
    "change_observer",           # dopo materialize
    "nightly_aging",
    "state_reaper",              # dopo aging (reaper unico stato persistente)
    "telos_synth_consume",
    "proposals_eta_aggregate",
    "introvertiva_propose",
    "promoter",
    "promoter_digest",           # dopo promoter
    "proposals_cleanup",
    "lifecycle_summary",
    "skill_sandbox_watchdog",
)


async def run_nightly(callbacks, payload: dict | None = None) -> dict:
    """Esegue la sequenza notturna invocando i callback registrati per chiave.

    `callbacks`: il CallbackRegistry del daemon (ha `.get(key) -> CallbackInfo`).
    Ritorna `{ok, ran: {key: "ok"|"missing"|"error: ..."}, ok_count, fail_count}`.
    """
    ran: dict[str, str] = {}
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
                await info.fn(None)
            else:
                await loop.run_in_executor(None, info.fn, None)
            ran[key] = "ok"
            log.info("nightly_maintenance: %s ok", key)
        except Exception as e:  # §2.8 error-isolation: un fallimento non aborta
            ran[key] = f"error: {type(e).__name__}: {e}"
            log.warning("nightly_maintenance: %s FALLITO: %r", key, e)
    ok_count = sum(1 for v in ran.values() if v == "ok")
    fail_count = sum(1 for v in ran.values() if v.startswith("error"))
    return {"ok": True, "ran": ran, "ok_count": ok_count,
            "fail_count": fail_count, "total": len(NIGHTLY_SEQUENCE)}
