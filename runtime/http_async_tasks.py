"""Attività periodiche HTTP: scadenza dialoghi e identità dei modelli."""
from __future__ import annotations

import asyncio
import os

from http_app_state import DIALOG_SWEEPER_TASK, MODEL_IDENTITY_TASK, app_get
from logging_setup import get_logger

log = get_logger(__name__)

_DIALOG_SWEEP_INTERVAL_S = float(os.environ.get("METNOS_DIALOG_SWEEP_INTERVAL_S", "60"))
_MODEL_IDENTITY_INTERVAL_S = float(os.environ.get("METNOS_MODEL_IDENTITY_INTERVAL_S", "60"))
_MODEL_IDENTITY_TIMEOUT_S = float(os.environ.get("METNOS_MODEL_IDENTITY_TIMEOUT_S", "1.5"))


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


# --- Identità osservata dei modelli ---------------------------------------

async def model_identity_task(app) -> None:
    """Refresh provider-advertised model identities outside HTTP rendering."""

    log.info("model_identity started (interval=%.0fs)",
             _MODEL_IDENTITY_INTERVAL_S)
    while True:
        try:
            from model_identity import refresh_configured

            result = await asyncio.to_thread(
                refresh_configured, timeout_s=_MODEL_IDENTITY_TIMEOUT_S)
            log.debug("model_identity refresh: %s", result)
            await asyncio.sleep(_MODEL_IDENTITY_INTERVAL_S)
        except asyncio.CancelledError:
            log.info("model_identity cancelled")
            return
        except Exception:
            log.exception("model_identity tick error")
            await asyncio.sleep(_MODEL_IDENTITY_INTERVAL_S)


# --- registrazione lifecycle ------------------------------------------------

def register_async_tasks(app) -> None:
    """Aggancia i task on_startup. Cancellati on_shutdown."""
    async def _start_tasks(app):
        app[DIALOG_SWEEPER_TASK] = asyncio.create_task(
            dialog_sweeper_task(app)
        )
        app[MODEL_IDENTITY_TASK] = asyncio.create_task(
            model_identity_task(app)
        )

    async def _stop_tasks(app):
        for key in (DIALOG_SWEEPER_TASK, MODEL_IDENTITY_TASK):
            t = app_get(app, key)
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    app.on_startup.append(_start_tasks)
    app.on_shutdown.append(_stop_tasks)
