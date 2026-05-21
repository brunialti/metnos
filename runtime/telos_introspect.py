# SPDX-License-Identifier: AGPL-3.0-only
"""telos_introspect.py — orchestrator del telos engine.

Loop introvertivo che, per ogni telos dichiarato, applica le lenti
attive (toggle env METNOS_TELOS_LENS_<NAME>=1) e genera proposte.
Ogni proposta passa attraverso:

1. anti-paternalismo guard (regex deterministico per-lente).
2. vaglio costituzionale (block forbidden, gia' esistente).
3. expected_alignment scoring (LLM judge, future ADR telos engine).
4. persistenza in proposals.db con telemetria per-lente.

Modalita' MVP (task #12, 21/5/2026):

- Pilota: solo lente SCAMPER. Altre 8 lenti = task #13.
- Off-line runnable via CLI per esperimenti (10-20 proposte su corpus
  reale, valutazione manuale).
- Wire-in scheduler v2 daily@03:30 = task #12 fase 2 (dopo esperimento).

Telemetria:
- Path: ~/.local/share/metnos/telos_proposals.jsonl (append-only)
- Una riga per proposta: ts, telos_id, lens, operator, action, rationale,
  paternalism, accepted (post-vaglio), expected_alignment

§7.9: l'LLM gira SOLO dentro la lente (creativita' richiesta).
Tutto il resto (selezione lenti, persistenza, gating) e' deterministico.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

TELEMETRY_PATH = Path.home() / ".local" / "share" / "metnos" / "telos_proposals.jsonl"

# Tier LLM default per le lenti (creativita' moderata, costo contenuto).
_DEFAULT_TIER = "middle"


def _persist(record: dict) -> None:
    """Append-only telemetria. Best-effort."""
    try:
        TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TELEMETRY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as ex:
        _LOG.warning("telos_introspect telemetry write failed: %r", ex)


def _build_mnestoma_summary(top_n: int = 10) -> str:
    """Recupera i top-N mnest co-attivati di recente. Stringa per prompt."""
    try:
        from mnestoma import Mnestoma
        mn = Mnestoma()
        rows = mn.conn.execute(
            "SELECT src_executor, dst_executor, uses FROM mnests "
            "WHERE uses >= 2 ORDER BY uses DESC LIMIT ?", (top_n,)
        ).fetchall()
        if not rows:
            return "(nessun mnest disponibile)"
        return "\n".join(
            f"  {r['src_executor']} -> {r['dst_executor']} "
            f"(uses={r['uses']})" for r in rows
        )
    except Exception as ex:
        _LOG.warning("telos_introspect: mnestoma summary failed: %r", ex)
        return "(mnestoma non accessibile)"


def _build_user_patterns(days: int = 30, top_n: int = 8) -> str:
    """Sintesi dei verbi/oggetti piu' usati di recente dal turn_log."""
    try:
        import config as _C
        from collections import Counter
        verb_counts: Counter = Counter()
        n_turns = 0
        turn_dir = _C.PATH_TURNS if hasattr(_C, "PATH_TURNS") else (
            Path.home() / ".local" / "share" / "metnos" / "turns"
        )
        now = time.time()
        cutoff = now - days * 86400
        for jsonl in sorted(turn_dir.glob("*.jsonl"), reverse=True):
            if jsonl.stat().st_mtime < cutoff:
                break
            for line in jsonl.read_text().splitlines():
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("ts_start", 0) < cutoff:
                    continue
                n_turns += 1
                for s in d.get("steps", []):
                    t = (s.get("chosen_tool") or "").split("_", 1)[0]
                    if t:
                        verb_counts[t] += 1
        if not verb_counts:
            return f"(nessun turn negli ultimi {days} giorni)"
        top = verb_counts.most_common(top_n)
        total = sum(verb_counts.values())
        lines = [f"  ({n_turns} turn totali, {total} step)"]
        for verb, cnt in top:
            pct = int(100 * cnt / total)
            lines.append(f"  {verb}: {cnt} ({pct}%)")
        return "\n".join(lines)
    except Exception as ex:
        _LOG.warning("telos_introspect: user patterns failed: %r", ex)
        return "(turn_log non accessibile)"


def _build_executors_sample(catalog, max_n: int = 8) -> list[dict]:
    """Campione di executor dal catalog per il prompt. Prende i piu' usati."""
    try:
        from mnestoma import Mnestoma
        mn = Mnestoma()
        used = {}
        for r in mn.conn.execute(
            "SELECT name, uses FROM executors "
            "WHERE uses > 0 ORDER BY uses DESC LIMIT ?", (max_n,)
        ).fetchall():
            used[r["name"]] = r["uses"]
    except Exception:
        used = {}
    out = []
    if hasattr(catalog, "executors"):
        executors = list(catalog.executors.values())
    else:
        executors = list(catalog or [])
    executors.sort(key=lambda e: -used.get(e.name, 0))
    for e in executors[:max_n]:
        desc = getattr(e, "description", "") or ""
        out.append({
            "name": e.name,
            "description": desc.split("\n", 1)[0][:140],
        })
    return out


def _llm_invoke_middle(prompt: str) -> str:
    """Adapter LLM tier=middle, riusa llm_router."""
    try:
        from llm_router import call_tier
        r = call_tier(
            tier=_DEFAULT_TIER, system="",
            user=prompt, max_tokens=2048, temperature=0.7,
        )
        return r.text if hasattr(r, "text") else str(r)
    except Exception as ex:
        _LOG.error("telos_introspect: LLM call failed: %r", ex)
        raise


def run_for_telos(
    telos,
    *,
    catalog=None,
    llm_invoke: Optional[Callable[[str], str]] = None,
    lenses: Optional[list[str]] = None,
    operators: Optional[tuple] = None,
    persist: bool = True,
) -> list[dict]:
    """Genera proposte per un singolo telos, applicando le lenti attive.

    Args:
      telos: oggetto Telos
      catalog: oggetto Catalog Metnos (None -> load_catalog())
      llm_invoke: callable(prompt) -> str. None -> default middle tier.
      lenses: lista nomi lenti da applicare. None -> tutte attive da env.
      operators: per SCAMPER, subset operatori. None -> tutti e 7.
      persist: True -> scrive telemetria a TELEMETRY_PATH.

    Returns:
      list[dict] di proposte serializzate (post-paternalismo filter).
    """
    if catalog is None:
        try:
            from loader import load_catalog
            catalog = load_catalog()
        except Exception as ex:
            _LOG.error("telos_introspect: catalog load failed: %r", ex)
            return []
    llm = llm_invoke or _llm_invoke_middle
    from telos_lenses import is_lens_enabled
    active = []
    if lenses is None:
        # Auto-detect dalle env flag
        for name in ("scamper",):  # MVP: solo scamper. Altre da task #13.
            if is_lens_enabled(name):
                active.append(name)
    else:
        active = list(lenses)
    if not active:
        _LOG.info("telos_introspect: nessuna lente attiva per %s", telos.id)
        return []

    mnestoma_summary = _build_mnestoma_summary()
    user_patterns = _build_user_patterns()
    executors_sample = _build_executors_sample(catalog)

    results: list[dict] = []
    for lens_name in active:
        if lens_name == "scamper":
            from telos_lenses import scamper_generate
            proposals = scamper_generate(
                telos, executors_sample,
                mnestoma_summary, user_patterns,
                llm_invoke=llm, operators=operators,
            )
            for p in proposals:
                rec = {
                    "ts": time.time(),
                    "telos_id": telos.id,
                    "telos_phrase": telos.phrase,
                    "lens": lens_name,
                    "operator": p.operator,
                    "executor_target": p.executor_target,
                    "proposed_action": p.proposed_action,
                    "rationale": p.rationale,
                    "paternalism_flag": p.paternalism_flag,
                    "expected_alignment": p.expected_alignment,
                }
                if persist:
                    _persist(rec)
                results.append(rec)
        else:
            _LOG.warning("telos_introspect: lens %r non implementata", lens_name)
    return results


def run_all_telos(
    *,
    catalog=None,
    llm_invoke: Optional[Callable[[str], str]] = None,
    lenses: Optional[list[str]] = None,
    persist: bool = True,
) -> dict:
    """Esegue il loop per TUTTI i telos correnti. Ritorna summary."""
    from telos_loader import current
    out = {"telos_count": 0, "proposals_total": 0, "by_telos": {}}
    for t in current():
        out["telos_count"] += 1
        props = run_for_telos(
            t, catalog=catalog, llm_invoke=llm_invoke,
            lenses=lenses, persist=persist,
        )
        out["by_telos"][t.id] = len(props)
        out["proposals_total"] += len(props)
    return out


# CLI offline per esperimento manuale (MVP fase 1):
#   python -m runtime.telos_introspect --telos t.tempo --operators S,C,A
if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, "/opt/metnos/runtime")
    p = argparse.ArgumentParser(description="Telos engine offline runner")
    p.add_argument("--telos", help="ID telos (default: tutti)")
    p.add_argument("--lenses", default="scamper",
                   help="Lenti (comma): scamper")
    p.add_argument("--operators", default="S,C,A,M,P,E,R",
                   help="Operatori SCAMPER (comma)")
    p.add_argument("--persist", action="store_true",
                   help="Persisti in telemetria")
    p.add_argument("--mock-llm", action="store_true",
                   help="Usa LLM fake per dry-run (no costo)")
    args = p.parse_args()

    if args.mock_llm:
        def mock(prompt):
            return ('[{"executor_target": "find_urls", '
                    '"proposed_action": "Mock proposal", '
                    '"rationale": "Mock rationale"}]')
        llm = mock
    else:
        llm = None
    lenses = [s.strip() for s in args.lenses.split(",") if s.strip()]
    operators = tuple(s.strip() for s in args.operators.split(",") if s.strip())
    from telos_loader import by_id, current
    if args.telos:
        t = by_id(args.telos)
        if not t:
            print(f"ERROR: telos {args.telos!r} non trovato")
            sys.exit(1)
        results = run_for_telos(
            t, llm_invoke=llm, lenses=lenses,
            operators=operators, persist=args.persist,
        )
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        # NB: chiamare run_all_telos passa kwarg lenses+persist ma non
        # operators (specifico SCAMPER). Per ora i 7 op sono il default.
        summary = run_all_telos(
            llm_invoke=llm, lenses=lenses, persist=args.persist,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
