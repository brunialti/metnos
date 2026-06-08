"""Test dell'orchestratore manutenzione notturna (2026-06-04, ADR 0167 ext).

Verifica: (a) i 14 task housekeeping sono consolidati sotto `nightly_maintenance`
e tolti dalle schedule entry standalone; (b) `run_nightly` invoca i callback
registrati in ORDINE; (c) error-isolation §2.8 (un fallimento non aborta gli
altri); (d) callback `missing` gestito; (e) callback sync e async entrambi
invocati (sync via executor, non bloccante).
"""
from __future__ import annotations

import asyncio

from nightly_orchestrator import NIGHTLY_SEQUENCE, run_nightly
from scheduler_v2.callbacks import CallbackRegistry
from scheduler_v2.builtin_callbacks import (
    _BUILTIN_JOBS,
    _NIGHTLY_CONSOLIDATED,
    install_default_jobs,
)
from scheduler_v2.daemon import SchedulerDaemon
from scheduler_v2.models import ScheduleEntry


def test_consolidation_invariant():
    names = {j["name"] for j in _BUILTIN_JOBS}
    # I 14 della sequenza NON sono piu' entry standalone...
    assert _NIGHTLY_CONSOLIDATED.isdisjoint(names)
    # ...e la singola entry orchestratrice c'e'.
    assert "nightly_maintenance" in names
    # elenco+ordine = single source nella sequenza.
    assert frozenset(NIGHTLY_SEQUENCE) == _NIGHTLY_CONSOLIDATED
    assert len(NIGHTLY_SEQUENCE) == 14


def test_run_nightly_order_and_isolation():
    order: list[str] = []
    cb = CallbackRegistry()

    def make_sync(name):
        def _fn(payload=None):
            order.append(name)
            return {"ok": True}
        return _fn

    # una sync solleva (isolation), una e' async (copre il ramo await),
    # una resta non registrata (missing).
    boom_key = NIGHTLY_SEQUENCE[3]
    async_key = NIGHTLY_SEQUENCE[1]
    missing_key = NIGHTLY_SEQUENCE[-1]

    async def _async_fn(payload=None):  # iscoroutinefunction → is_async=True
        order.append(async_key)
        return {"ok": True}

    def _boom(payload=None):
        raise RuntimeError("boom")

    for k in NIGHTLY_SEQUENCE:
        if k == missing_key:
            continue
        if k == boom_key:
            cb.register(k, _boom)
        elif k == async_key:
            cb.register(k, _async_fn)
        else:
            cb.register(k, make_sync(k))

    rep = asyncio.run(run_nightly(cb))

    assert rep["ok"] is True
    assert rep["total"] == len(NIGHTLY_SEQUENCE)
    assert rep["ran"][boom_key].startswith("error")
    assert rep["ran"][missing_key] == "missing"
    assert rep["ran"][async_key] == "ok"
    # ok_count = tutti tranne il boom e il missing.
    assert rep["ok_count"] == len(NIGHTLY_SEQUENCE) - 2
    assert rep["fail_count"] == 1
    # Ordine preservato (boom non appende, missing saltato).
    expected = [k for k in NIGHTLY_SEQUENCE if k not in (boom_key, missing_key)]
    assert order == expected


def test_install_jobs_cleans_consolidated_keeps_user(db_path):
    """install_default_jobs rimuove le 14 entry standalone obsolete e seed
    `nightly_maintenance`, ma preserva i task UTENTE (callback_key diverso)."""
    d = SchedulerDaemon(db_path)
    # Simula un DB pre-consolidamento: una vecchia entry standalone di sistema
    # per ognuno dei 14 + un task utente che NON deve essere toccato.
    for key in NIGHTLY_SEQUENCE:
        d.storage.upsert(ScheduleEntry(
            name=key, trigger="daily@03:00", next_fire_at=0.0, recurring=True,
            callback_key=key, origin="system", description="legacy standalone",
        ))
    d.storage.upsert(ScheduleEntry(
        name="user_reminder", trigger="daily@09:00", next_fire_at=0.0,
        recurring=True, callback_key="run_user_query", origin="user",
        description="task utente",
    ))

    install_default_jobs(d)

    names = {e.name for e in d.storage.list_all()}
    # I 14 standalone obsoleti sono spariti...
    assert _NIGHTLY_CONSOLIDATED.isdisjoint(names)
    # ...l'orchestratore c'e'...
    assert "nightly_maintenance" in names
    # ...e il task utente e' intatto.
    assert "user_reminder" in names

    # Idempotente: una seconda esecuzione non duplica ne' ri-rimuove.
    install_default_jobs(d)
    names2 = [e.name for e in d.storage.list_all()]
    assert names2.count("nightly_maintenance") == 1
    assert names2.count("user_reminder") == 1
