# SPDX-License-Identifier: AGPL-3.0-only
"""speculation.py — preemption euristica per warming del cache HTTP.

L'idea (cluster C, 20/5/2026): mentre il PLANNER LLM ragiona (3-8s), il
runtime esegue speculativamente il producer piu' probabile in un thread
parallelo. Quando il PLANNER ritorna con tool_call=<producer>, l'executor
invoca il producer reale ma trova la risposta nel http_cache (ADR 0105)
sotto millisecondi.

Risparmio atteso: 2-5s wall-clock per turno per query web (≈30% del
traffico). Costo del miss: 1 chiamata producer sprecata (3-5s CPU + 1
request a SearXNG / IMAP).

Disciplina:
- Speculazione SOLO su producer side-effect-free: find_urls (HTTP GET via
  SearXNG, cached); read_urls_html (HTTP GET, cached). MAI su action_out.
- Gating heuristic: intent.object → producer atteso. Confidenza alta
  (≥ 0.7) o object 1:1 al producer.
- Thread daemon: muore alla fine del turno; cache popolata sopravvive.
- Telemetria opzionale via env METNOS_SPECULATION_TELEMETRY=1.

Opt-in di default OFF per fase di tuning. Env METNOS_SPECULATION=1.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

import config as _C  # §7.11

# Telemetria persistente per A/B bench (task #16).
# Append-only JSONL, una riga per warm tentativo. Strutture:
#   {ts, turn_id?, tool, args, dt_ms, ok, cache_hit, error?}
# Letto da admin/script per misurare hit-rate + saving.
_TELEMETRY_PATH = _C.PATH_USER_DATA / "speculation_telemetry.jsonl"


def _persist_telemetry(record: dict) -> None:
    """Append-only telemetria. Best-effort: errori loggati, non bloccanti."""
    try:
        _TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _TELEMETRY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as ex:
        _LOG.warning("speculation telemetry write failed: %r", ex)

# Mapping euristico intent.object -> producer tool. Solo producer pesanti
# (latenza >500ms) che justifierebbero la speculazione. Producer leggeri
# (get_now <100ms, get_persons local sqlite) non valgono il rischio.
_OBJECT_TO_SPECULATIVE_PRODUCER = {
    "urls": "find_urls",        # ~2-5s, SearXNG + BFS
    # NB: read_messages NON aggiunto per ora: il cache IMAP non e' pulito
    # quanto il http_cache; rischio di stale results su mail nuova.
}


def is_enabled() -> bool:
    """Speculazione attiva solo se METNOS_SPECULATION=1."""
    return os.environ.get("METNOS_SPECULATION", "0") == "1"


def _telemetry_enabled() -> bool:
    return os.environ.get("METNOS_SPECULATION_TELEMETRY", "0") == "1"


def choose_speculative_tool(intent: dict | None) -> Optional[str]:
    """Decide se vale la pena speculare e quale tool. None se no.

    Heuristica conservativa: object 1:1 mapping a producer pesante.
    Estendibile guardando intent.confidence o query patterns specifici.
    """
    if not isinstance(intent, dict):
        return None
    obj = intent.get("object")
    if not obj:
        return None
    return _OBJECT_TO_SPECULATIVE_PRODUCER.get(obj)


def build_speculative_args(tool: str, query: str) -> dict:
    """Args di default per la speculazione. Conservativi: stesso default
    che il planner sceglierebbe quasi sicuramente."""
    if tool == "find_urls":
        return {"search_query": query} if query else {}
    return {}


def kick_off(
    intent: dict | None,
    query: str,
    *,
    invoke: Callable[[str, dict], dict],
) -> Optional[threading.Thread]:
    """Lancia la speculazione in un thread daemon. Ritorna il thread o None
    se gating non passa.

    `invoke(tool_name, args) -> obs`: il caller fornisce la callable che
    dispatcha verso invoke_executor (gia' inizializzato col contesto turn).
    Errori interni della speculazione vengono assorbiti (best-effort warm).
    """
    if not is_enabled():
        return None
    tool = choose_speculative_tool(intent)
    if not tool:
        return None
    args = build_speculative_args(tool, query or "")
    if not args:
        return None  # niente query, niente speculation

    def _run():
        t0 = time.perf_counter()
        rec: dict = {
            "ts": time.time(), "tool": tool, "args_keys": sorted(args.keys()),
        }
        try:
            obs = invoke(tool, args)
            dt_ms = int((time.perf_counter() - t0) * 1000)
            rec["dt_ms"] = dt_ms
            rec["ok"] = bool(obs.get("ok")) if isinstance(obs, dict) else False
            # cache_hit euristico: se il warm e' tornato in <300ms su tool
            # cache-able (find_urls/read_urls_html) e ok=True, e' cache hit.
            rec["cache_hit"] = (
                rec["ok"] and dt_ms < 300
                and tool in {"find_urls", "read_urls_html"}
            )
            if _telemetry_enabled():
                _LOG.info("speculation: %s %dms ok=%s",
                          tool, dt_ms, rec["ok"])
        except Exception as ex:
            rec["dt_ms"] = int((time.perf_counter() - t0) * 1000)
            rec["ok"] = False
            rec["error"] = f"{type(ex).__name__}: {ex}"[:200]
            if _telemetry_enabled():
                _LOG.warning("speculation: %s failed: %r", tool, ex)
        # Persisti sempre (anche se telemetria env e' OFF) per bench.
        _persist_telemetry(rec)
        # finally: thread daemon termina; cache populated rimane.

    th = threading.Thread(target=_run, daemon=True, name=f"spec_{tool}")
    th.start()
    return th
