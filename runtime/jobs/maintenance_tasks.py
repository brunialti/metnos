"""maintenance_tasks.py — task notturni builtin dello scheduler v2.

RICOSTRUITO 2026-05-28. Queste 7 funzioni vivevano in `_v1_tasks.pyc`
(bytecode «frozen» da un `runtime/scheduler.py` mai versionato), caricato via
`SourcelessFileLoader`. Il commit `078796a` (migrazione `_legacy`) ha cancellato
il `.pyc`; sorgente non recuperabile (mai in git, fd processo chiuso). Le 7 task
sono qui ricostruite come SORGENTE VERA (no piu' bytecode, §7.1/§7.10), wrapper
zero-arg attorno ai moduli che contengono la logica reale — intatti.

Contratto: ogni `task_*()` e' zero-arg e ritorna un dict-report; lo scheduler le
adatta alla firma `cb(payload)` via `_wrap_zero_arg`.

5 task ricostruite ESATTE (la logica vive nei moduli avvolti):
  apply_executor_ager, apply_ager, introvertiva_propose, proposals_cleanup,
  lifecycle_summary.
2 task NON recuperabili verbatim (nessuna `def` mai esistita in git) → stub
onesto loggato, in attesa di decisione/ricostruzione da parte di Roberto:
  synt_suggest, introvertiva_apply.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


# --- 5 task ricostruite esatte ------------------------------------------------

def task_apply_executor_ager() -> dict:
    """Deprecate/archive executor inattivi (ADR executor_lifecycle).

    Avvolge `executor_aging.apply_executor_ager()` con i default di soglia.
    """
    from executor_aging import apply_executor_ager
    return apply_executor_ager()


def task_apply_ager() -> dict:
    """Decay/demote degli archi mnestoma deboli (ADR 0074: «mnest»).

    Apre una connessione Mnestoma, applica l'ager e la CHIUDE sempre
    (evita il leak di fd visto nel path turno).
    """
    from mnestoma import Mnestoma
    mn = Mnestoma()
    try:
        return mn.apply_ager()
    finally:
        mn.close()


def task_introvertiva_propose() -> dict:
    """Genera candidati introvertiva (dedupe/generalize/specialize) SENZA
    applicarli. `run_all` scrive solo audit JSONL, non muta il catalog.
    """
    from introvertiva import run_all
    return run_all(audit=True)


def task_proposals_cleanup() -> dict:
    """Manutenzione lifecycle backlog proposte (ADR 0096): archive aged,
    keep-latest-N, dedupe, decay orfani. Sempre move, mai delete.
    """
    from proposals_cleanup import run_cleanup
    return run_cleanup()


def task_lifecycle_summary() -> dict:
    """Aggregatore READ-ONLY audit ultime 24h (ADR 0097)."""
    from lifecycle_summary import run_summary
    return run_summary(window_hours=24)


# --- Nightly aging consolidato (L2 consolidamento timer, 30/5/2026) -----------
# I 2 stub task_synt_suggest + task_introvertiva_apply sono stati RITIRATI
# (erano no-op, logica persa con _v1_tasks.pyc, superati da introvertiva_propose
# + change_applier/promoter). L1 consolidamento timer.

def task_nightly_aging() -> dict:
    """Decay notturno UNIFICATO: executor ager + mnest ager in un solo job.

    Sostituisce i 2 timer gemelli `apply_executor_ager`@03:30 e `apply_ager`@04:00
    (stesso dominio decay, sequenziali). Esegue in ordine e ritorna i due report.
    """
    return {
        "ok": True,
        "executor_ager": task_apply_executor_ager(),
        "mnest_ager": task_apply_ager(),
    }


# --- Catalog completo per i job fastpath (reaper + promotion) -----------------

def _full_catalog_names():
    """Set COMPLETO dei tool invocabili (executor caricati + builtin
    in-process di agent_runtime) — contratto condiviso di fastpath.prune
    (morte C1/C2) e fastpath_promote.run_nightly (dedupe vs catalog).
    Non ricostruibile per intero → None: i consumer degradano a
    solo-aging/nessuna-emissione (meglio di falsi kill/duplicati, §2.8).
    """
    import sys as _sys
    try:
        from loader import load_catalog
        names = set(load_catalog().all_names())
        _ar = _sys.modules.get("agent_runtime")
        if _ar is None:
            import agent_runtime as _ar
        names |= set(getattr(_ar, "_BUILTIN_TOOL_HANDLERS", {}) or {})
        return names
    except Exception as ex:
        log.warning("catalog completo non ricostruibile (%r)", ex)
        return None


# --- Promozione fastpath L0 → executor synt (mandato 11/6/2026) ----------------

def task_fastpath_promotion() -> dict:
    """Detection notturna dei cluster di fastpath ricorrenti candidati a
    executor di prima classe (engine/fastpath_promote). Tier 1: proposta
    human-gated nel backlog introvertiva. Tier 2: auto-synt dietro flag
    METNOS_FASTPATH_AUTOPROMOTE (OFF default), cap 1/notte. Gating
    conservativo cluster-based; catalog incompleto → nessuna emissione.
    """
    from engine import fastpath_promote
    return fastpath_promote.run_nightly(catalog_names=_full_catalog_names())


# --- Reaper unificato dello stato persistente (29/5/2026) ---------------------

def task_state_reaper() -> dict:
    """Reaper unico dello stato persistente che cresceva senza pulizia.

    Wire dei reaper ESISTENTI ma mai schedulati (stesso pattern del bug
    dialog_pending: funzione scritta, mai chiamata in produzione) + retention
    inline per gli store privi di funzione (turns/, _history blob). Ogni passo
    e' isolato in try/except: il fallimento di uno non blocca gli altri.
    Idempotente, cancella SOLO oltre-retention. Ritorna un report per-reaper.
    Tutte le retention via env (§7.11/§11), default conservativi.
    """
    import os
    import time
    import shutil
    from pathlib import Path
    import config as _C

    report: dict = {}

    def _run(name: str, fn):
        try:
            report[name] = fn()
        except Exception as ex:  # un reaper rotto non deve fermare gli altri
            report[name] = {"error": repr(ex)}
            log.warning("state_reaper[%s] fallito: %r", name, ex)

    undo_days = int(os.environ.get("METNOS_UNDO_RETENTION_DAYS", "30"))
    turn_days = int(os.environ.get("METNOS_TURN_LOG_RETENTION_DAYS", "90"))
    skill_days = int(os.environ.get("METNOS_SKILL_CACHE_RETENTION_DAYS", "30"))
    now = time.time()

    def _undo():
        from undo import UndoLog
        return {"purged": UndoLog().purge_older_than(days=undo_days)}
    _run("undo", _undo)

    def _history_blobs():
        # Backup blob di reversibilita' (undo): una dir per turno. Rimuovi le
        # dir oltre la retention undo (i blob servono solo finche' l'undo del
        # turno e' possibile). Era un leak da ~GB (mai ripulito).
        hist = Path(os.environ.get("METNOS_HISTORY_DIR")
                    or (_C.PATH_USER_DATA / "_history"))
        if not hist.exists():
            return {"removed_dirs": 0, "note": "no _history dir"}
        cutoff = now - undo_days * 86400
        removed = 0
        for d in hist.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
        return {"removed_dirs": removed, "retention_days": undo_days}
    _run("history_blobs", _history_blobs)

    def _http_cache():
        from http_cache import cleanup_weekly
        return {"removed": cleanup_weekly()}
    _run("http_cache", _http_cache)

    def _location():
        from location_request import sweep_expired
        return {"swept": sweep_expired()}
    _run("location_pending", _location)

    def _skill_fetch():
        from skill_fetch import cleanup_older_than
        return {"removed": cleanup_older_than(skill_days * 86400)}
    _run("skill_fetch", _skill_fetch)

    def _install_resume():
        from install_resume_state import cleanup_expired
        return {"removed": cleanup_expired()}
    _run("install_resume", _install_resume)

    def _approval():
        from approval_registry import cleanup_expired
        return {"removed": cleanup_expired()}
    _run("approval_registry", _approval)

    def _autopath():
        from engine import autopath
        return autopath.prune()
    _run("autopath", _autopath)

    def _fastpath():
        # Aging L0 (11/6/2026): mai-riusato oltre grazia, stale, cap LRU.
        # + MORTE: tool mancante dal catalog (C1), provenienza promozione
        # (C2 esatta) o executor che implementa direttamente l'intent del
        # fastpath (C2 name-based). Costo-zero: un fastpath potato per
        # errore si ricrea da solo alla prossima ripetizione riuscita
        # (auto-produzione in dispatch). catalog_names incompleto → None →
        # solo aging, nessuna morte stanotte (contratto _full_catalog_names).
        from engine import fastpath
        return fastpath.prune(catalog_names=_full_catalog_names())
    _run("fastpath", _fastpath)

    def _turn_logs():
        tdir = _C.PATH_USER_DATA / "turns"
        if not tdir.exists():
            return {"removed_files": 0}
        cutoff = now - turn_days * 86400
        removed = 0
        for f in tdir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return {"removed_files": removed, "retention_days": turn_days}
    _run("turn_logs", _turn_logs)

    log.info("state_reaper report: %s", report)
    return {"ok": True, "report": report}
