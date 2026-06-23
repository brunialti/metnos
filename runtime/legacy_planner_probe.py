"""Sonda di osservabilità per l'ingresso del PLANNER legacy (ADR 0177 M1).

Scopo TEMPORANEO (scadenza 2026-06-30): confermare che il blocco legacy
(~3300 LOC in agent_runtime.run_turn) sia EFFETTIVAMENTE morto in produzione —
0 ingressi su traffico reale — prima di rimuoverlo fisicamente. Entrambi i path
che lo tenevano vivo (foto-upload, dialog-resume) sono ora sull'engine v3
(«semina di turno»); l'ingresso legacy resta solo come fallback gated
(engine→None) o via `METNOS_PLANNER_LEGACY=1` esplicito.

Registra OGNI ingresso: marker greppabile nei log + riga JSONL persistente
(sopravvive ai restart). Quando il contatore resta 0 su una battery reale +
storico, il blocco è «gated» e si rimuove (questa sonda inclusa).

NB: rimuovere INTERAMENTE questo modulo + la callsite in agent_runtime quando
il legacy viene eliminato (è tutto codice a tempo).
"""
from __future__ import annotations

import json
import os
import time

from logging_setup import get_logger

log = get_logger(__name__)

# Marker univoco greppabile (journalctl -u metnos-http | grep LEGACY_PLANNER_ENTRY).
MARKER = "LEGACY_PLANNER_ENTRY"


def _probe_path():
    """File JSONL persistente sotto PATH_USER_STATE (override env per test)."""
    p = os.environ.get("METNOS_LEGACY_PROBE_PATH")
    if p:
        return p
    from config import PATH_USER_STATE
    return str(PATH_USER_STATE / "legacy_planner_entries.jsonl")


def record_legacy_entry(*, turn_id: str, trigger: str, query: str,
                        ts: float | None = None) -> None:
    """Registra un ingresso nel blocco legacy. `trigger` ∈
    {upload_fallthrough, resume_fallthrough, legacy_flag, unknown}: la CAUSA
    per cui il controllo è caduto nel legacy (engine→None su upload/resume, o
    METNOS_PLANNER_LEGACY=1 esplicito). Best-effort: non deve mai far fallire
    il turno (§2.8 — l'osservabilità non altera l'esito)."""
    # query NON in chiaro nel persistente (privacy §7.5): solo lunghezza + hash
    # corto per distinguere query diverse senza esporne il contenuto.
    import hashlib
    qn = (query or "").strip()
    qh = hashlib.sha256(qn.encode("utf-8")).hexdigest()[:12] if qn else ""
    # Marker nei log applicativi (visibile subito, conta via grep).
    log.warning("%s trigger=%s turn_id=%s qlen=%d qhash=%s",
                MARKER, trigger, turn_id, len(qn), qh)
    # Riga persistente (conteggio robusto ai restart).
    try:
        rec = {"ts": ts if ts is not None else time.time(),
               "turn_id": turn_id, "trigger": trigger,
               "qlen": len(qn), "qhash": qh}
        path = _probe_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:  # noqa: BLE001 — best-effort, mai bloccante
        log.debug("legacy_planner_probe: persist noop: %r", ex)


def count_entries() -> dict:
    """Riepilogo per il monitoraggio: totale + per-trigger. {} se nessun file."""
    try:
        path = _probe_path()
        if not os.path.exists(path):
            return {"total": 0, "by_trigger": {}}
        total = 0
        by_trigger: dict = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                total += 1
                t = rec.get("trigger", "unknown")
                by_trigger[t] = by_trigger.get(t, 0) + 1
        return {"total": total, "by_trigger": by_trigger}
    except Exception as ex:  # noqa: BLE001
        log.debug("legacy_planner_probe: count noop: %r", ex)
        return {"total": 0, "by_trigger": {}}
