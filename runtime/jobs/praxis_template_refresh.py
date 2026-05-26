"""praxis_template_refresh.py — batch refresh skill con template_issue=1.

Scheduler v2 daily@04:00 (vedi `runtime/scheduler_v2/builtin_callbacks.py`).
Safety net per format_fail rilevati ma non rigenerati immediatamente
(es. retry inline ha fallito o e' stato disabilitato).

Pipeline:
  1. SELECT skills WHERE template_issue = 1 AND status='active'
  2. Per ciascuna: Mētis re-propose framework escludendo fw_hash corrente
  3. UPDATE skill con nuovo framework_json + framework_hash + template_issue=0
  4. Log skill_versions evento 'refresh_template' reason='nightly'

Determinismo §7.9: deterministico ovunque tranne Mētis re-propose (LLM wise).
Idempotente: ri-eseguibile, salta skill gia' template_issue=0.

ADR 0161 ext, 26/5/2026.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time

log = logging.getLogger(__name__)


def run(*, store=None, **kwargs) -> dict:
    """Entry point scheduler v2.

    Returns:
      dict {scanned, refreshed, failed, elapsed_ms}.
    """
    t0 = time.monotonic()
    try:
        if store is None:
            from praxis import get_store
            store = get_store()
        conn = store.conn
        cur = conn.execute(
            "SELECT id, cluster_id, intent_sig, framework_hash, framework_json "
            "FROM skills WHERE template_issue = 1 AND status = 'active'"
        )
        rows = cur.fetchall()
    except Exception as ex:
        log.warning("praxis_template_refresh: load skills: %r", ex)
        return {"scanned": 0, "refreshed": 0, "failed": 1, "error": str(ex)}

    refreshed = 0
    failed = 0
    for skill_id, cluster_id, sig, old_fw_hash, _old_fw_json in rows:
        try:
            ok = _refresh_skill(store, skill_id, sig, old_fw_hash)
            if ok:
                refreshed += 1
            else:
                failed += 1
        except Exception as ex:
            log.warning("praxis_template_refresh: skill=%s: %r", skill_id, ex)
            failed += 1

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log.info("praxis_template_refresh: scanned=%d refreshed=%d failed=%d "
              "elapsed=%dms", len(rows), refreshed, failed, elapsed_ms)
    return {"scanned": len(rows), "refreshed": refreshed,
            "failed": failed, "elapsed_ms": elapsed_ms}


def _refresh_skill(store, skill_id: str, sig: str,
                     old_fw_hash: str) -> bool:
    """Mētis re-propose + UPDATE skill. True se riuscito."""
    # Recupera ultima observation di questa skill per query proxy.
    try:
        cur = store.conn.execute(
            "SELECT turn_id FROM observations "
            "WHERE promoted_to = ? OR (intent_sig = ? AND verdict = 'ok') "
            "ORDER BY id DESC LIMIT 1", (skill_id, sig))
        row = cur.fetchone()
        turn_id = row[0] if row else None
        query = ""
        if turn_id:
            try:
                from turn_feedback import _load_turn
                turn = _load_turn(turn_id) or {}
                query = turn.get("user_query") or turn.get("query") or ""
            except Exception:
                pass
        if not query:
            # Fallback: usa intent_sig come proxy
            query = sig.replace("|", " ").replace("_", " ").strip()
    except Exception as ex:
        log.warning("_refresh_skill: load context: %r", ex)
        return False

    try:
        verb, obj, kw = sig.split("|", 2)
    except ValueError:
        verb, obj, kw = sig, "", ""
    keywords = [k for k in kw.split("_") if k]

    # Mētis re-propose.
    try:
        import praxis_propose
        from llm_router import LLMRouter
        router = LLMRouter()

        def _wise(system, user, **kw_):
            res = router.chat(system, user, tier="wise", **kw_)
            return (res.text or "") if res else ""

        intent = {"verb": verb, "object": obj, "keywords": keywords}
        tools = []
        try:
            from loader import load_catalog
            tools = [e.name for e in load_catalog()]
        except Exception:
            pass
        new_fw = praxis_propose.propose_framework(
            query=query, intent=intent, available_tools=tools,
            excluded_frameworks={old_fw_hash},
            llm_call=_wise,
        )
        if not new_fw:
            return False
    except Exception as ex:
        log.warning("_refresh_skill: Mētis: %r", ex)
        return False

    # Save.
    try:
        from praxis import compute_framework_hash, _utcnow
        new_fw_hash = compute_framework_hash(new_fw)
        store.conn.execute(
            "UPDATE skills SET framework_json = ?, framework_hash = ?, "
            "template_issue = 0, ts_last_used = ? WHERE id = ?",
            (json.dumps(new_fw, ensure_ascii=False),
             new_fw_hash, _utcnow(), skill_id))
        store.conn.commit()
        try:
            import praxis_cluster
            praxis_cluster.log_skill_version(
                store.conn, skill_id, "refresh_template",
                old_fw_hash=old_fw_hash, new_fw_hash=new_fw_hash,
                reason="nightly_batch")
        except Exception:
            pass
        log.info("praxis_template_refresh: skill=%s refreshed %s→%s",
                  skill_id, old_fw_hash, new_fw_hash)
        return True
    except Exception as ex:
        log.warning("_refresh_skill: save: %r", ex)
        return False
