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
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

import config as _C  # §7.11

TELEMETRY_PATH = _C.PATH_USER_DATA / "telos_proposals.jsonl"

# LLM default per le lenti: modello locale (Qwen) via llama-server :8080.
# Bypass LLMRouter (che secondo `~/.config/metnos/llm_tiers.toml` instrada
# middle a Sonnet frontier). Il telos engine deve girare a costo zero,
# in background, su modello locale — questo e' un vincolo del progetto
# (vedi docs/it/architecture/telos.html §3 "Vive in BACKGROUND").
_LOCAL_MODEL = "local"
_LOCAL_ENDPOINT = "http://127.0.0.1:8080"


def _stored_target_lens_pairs() -> set:
    """Coppie (executor_target, lens) gia' nello store raw.

    Scan O(N) per run di lens (file ~1k righe, ms): accettabile per un
    batch notturno. Include i record appena scritti nello stesso run
    (append-only → il file E' lo stato corrente)."""
    pairs: set = set()
    try:
        with TELEMETRY_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tgt = rec.get("executor_target") or ""
                if tgt:
                    pairs.add((tgt, rec.get("lens") or ""))
    except FileNotFoundError:
        pass
    except OSError as ex:
        _LOG.warning("telos_introspect: stored pairs scan failed: %r", ex)
    return pairs


def _persist(record: dict) -> bool:
    """Append-only telemetria. Best-effort. Ritorna True se persistito.

    Anti-resurrezione (C.5, 22/5/2026): se il `executor_target` della proposta
    e' nei `rejected_targets()` (LWW), skippa silenziosamente. Coerente con
    la regola utente: "se cancello una proposta non deve riapparire la sera
    dopo". Implementazione conservativa "per target" (collassa anche varianti
    parametriche): per ora preferiamo over-filter a under-filter.

    Dedup generativo (mandato 12/6/2026, qualita'>quantita'): se la coppia
    (executor_target, lens) e' GIA' nello store, skip. La ripetizione
    intra-lente sullo stesso target non aggiunge evidenza (la convergenza
    si misura su lenti DISTINTE, vedi telos_proposals_store.cluster_score);
    sui dati reali la regola avrebbe ridotto 1017 → 75 righe (−93%).
    Una lente NUOVA sullo stesso target persiste comunque (evidenza vera).
    """
    target = record.get("executor_target") or ""
    try:
        from telos_proposals_store import rejected_targets
        rej_targets = rejected_targets()
        if target and target in rej_targets:
            _LOG.info("telos_introspect: skip persist (rejected target): %s", target)
            return False
    except Exception as ex:
        _LOG.warning("telos_introspect: rejected_targets check failed: %r", ex)
    if target and (target, record.get("lens") or "") in _stored_target_lens_pairs():
        _LOG.info("telos_introspect: skip persist (dup target+lens): %s/%s",
                  target, record.get("lens"))
        return False
    try:
        TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TELEMETRY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as ex:
        _LOG.warning("telos_introspect telemetry write failed: %r", ex)
        return False


def _build_mnestoma_summary(
    top_n: int = 10,
    live_names: Optional[set] = None,
) -> str:
    """Recupera i top-N mnest co-attivati di recente. Stringa per prompt.

    Filtra le coppie a entrambi gli endpoint vivi nel catalog corrente
    (`live_names`): evita che il LLM proponga su executor obsoleti che
    sono nel mnestoma per ragioni storiche (rinomi, GC, demote)."""
    try:
        from mnestoma import Mnestoma
        mn = Mnestoma()
        rows = mn.conn.execute(
            "SELECT src_executor, dst_executor, uses FROM mnests "
            "WHERE uses >= 2 ORDER BY uses DESC LIMIT ?", (top_n * 3,)
        ).fetchall()
        if not rows:
            return "(nessun mnest disponibile)"
        if live_names:
            rows = [r for r in rows
                    if r["src_executor"] in live_names
                    and r["dst_executor"] in live_names]
        rows = rows[:top_n]
        if not rows:
            return "(nessun mnest vivente nel catalog corrente)"
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
        from collections import Counter
        verb_counts: Counter = Counter()
        n_turns = 0
        turn_dir = _C.PATH_TURNS if hasattr(_C, "PATH_TURNS") else (
            _C.PATH_USER_DATA / "turns"
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


def _llm_invoke_local(prompt: str, *, grammar: str | None = None) -> str:
    """Adapter LLM locale via LlamaCppProvider diretto a :8080.

    BYPASSA LLMRouter perche' `~/.config/metnos/llm_tiers.toml` puo'
    instradare tier=middle a un provider frontier (Sonnet/Opus): per il
    telos engine vogliamo SEMPRE il modello locale (background, zero
    cost, vincolo §3 telos.html "Vive in BACKGROUND").

    Thinking budget configurabile via env:
      METNOS_TELOS_THINK=0|1   abilita reasoning (default 0)
      METNOS_TELOS_REASONING_BUDGET=N  (default 1024, ignorato se THINK=0)

    `grammar`: GBNF opzionale. Se passata, vincola l'output (Naming
    Authority, ADR 0133).
    """
    think = os.environ.get("METNOS_TELOS_THINK", "0") == "1"
    rb = int(os.environ.get("METNOS_TELOS_REASONING_BUDGET", "1024"))
    try:
        from llm_provider import LlamaCppProvider
        prov = LlamaCppProvider(
            model=_LOCAL_MODEL,
            endpoint=_LOCAL_ENDPOINT,
        )
        r = prov.chat(
            "", prompt,
            max_tokens=2048, temperature=0.7,
            think=think,
            reasoning_budget=rb,
            grammar=grammar,
        )
        return r.text if hasattr(r, "text") else str(r)
    except Exception as ex:
        _LOG.error("telos_introspect: local LLM call failed: %r", ex)
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
    llm = llm_invoke or _llm_invoke_local
    from telos_lenses import LENSES, LENSES_NO_GRAMMAR, is_lens_enabled, run_lens, LensCtx

    if lenses is None:
        active = [n for n in LENSES if is_lens_enabled(n)]
    else:
        active = [n for n in lenses if n in LENSES]
    if not active:
        _LOG.info("telos_introspect: nessuna lente attiva per %s", telos.id)
        return []

    if hasattr(catalog, "executors"):
        live_names = set(catalog.executors.keys())
    else:
        live_names = {getattr(e, "name", "") for e in (catalog or [])}
    mnestoma_summary = _build_mnestoma_summary(live_names=live_names)
    user_patterns = _build_user_patterns()
    executors_sample = _build_executors_sample(catalog)

    # GBNF opt-in via env: vincola executor_target a catalog vivo e
    # new_op_name a vocab §2.2 + descriptor kebab-case (Naming Authority).
    grammar = None
    if os.environ.get("METNOS_TELOS_GRAMMAR", "0") == "1":
        try:
            from naming_grammar import naming_grammar_fragment, scamper_json_grammar
            grammar = scamper_json_grammar(naming_grammar_fragment(
                live_executors=sorted(live_names),
            ))
        except Exception as ex:
            _LOG.warning("telos_introspect: grammar build failed: %r", ex)

    ctx = LensCtx(
        telos=telos,
        executors_sample=executors_sample,
        mnestoma_summary=mnestoma_summary,
        user_patterns_summary=user_patterns,
        live_executor_names=live_names,
    )

    # Fase 2 (22/5/2026): pre-carica TUTTI i telos per il Giudice teleologico.
    # La proposta e' generata per UN telos (telos.id) ma il fit va misurato su
    # TUTTI: una proposta puo' aiutare quello ma danneggiarne altri.
    try:
        from telos_loader import current as _telos_current
        all_telos = _telos_current()
    except Exception as ex:
        _LOG.warning("telos_introspect: telos_loader.current() failed: %r", ex)
        all_telos = []

    results: list[dict] = []
    for lens_name in active:
        lens_mod = LENSES[lens_name]
        # operators override (oggi solo SCAMPER usa subset)
        ops = operators if (operators and lens_name == "scamper") else lens_mod.OPERATORS
        # Per lenti che propongono concetti (telos, super-verbo, vincolo)
        # disabilita la grammar canonical (new_op_name e' sempre null).
        lens_grammar = None if lens_name in LENSES_NO_GRAMMAR else grammar
        proposals = run_lens(
            lens_name=lens_name,
            operators=ops,
            build_prompt=lens_mod.build_prompt,
            ctx=ctx,
            llm_invoke=llm,
            grammar=lens_grammar,
        )
        # Cap selettivita' (F.2, 22/5/2026): max N proposte per lens per
        # run. Rationale utente: 481 proposte accumulate sono ingestibili.
        # Conservare solo le top-N per affidarsi all'AlignmentEngine come
        # filtro post-hoc. Cap conservativo (10): le lenti emettono
        # tipicamente 2-30 proposte per run, taglio elimina la coda lunga.
        _MAX_PROPOSALS_PER_LENS = int(os.environ.get(
            "METNOS_TELOS_MAX_PER_LENS", "10"))
        if len(proposals) > _MAX_PROPOSALS_PER_LENS:
            _LOG.info("telos %s: %d → %d proposte (cap)",
                      lens_name, len(proposals), _MAX_PROPOSALS_PER_LENS)
            proposals = proposals[:_MAX_PROPOSALS_PER_LENS]
        for p in proposals:
            # Giudice teleologico fase 2 (22/5/2026): stima fit per telos +
            # compone expected_alignment. Bother_cost=0 in MVP (l'engine
            # non sa ancora quante proposte sono gia' state pubblicate).
            ea = 0.0
            per_telos_audit: list[dict] = []
            if all_telos:
                try:
                    from alignment_engine import estimate_fit
                    res = estimate_fit(
                        {"lens": lens_name,
                         "proposed_action": p.proposed_action,
                         "rationale": p.rationale},
                        all_telos,
                        llm_invoke=llm,
                    )
                    ea = res.expected_alignment
                    p.expected_alignment = ea
                    per_telos_audit = [
                        {"telos_id": f.telos_id, "fit": f.fit, "why": f.why}
                        for f in res.per_telos
                    ]
                except Exception as ex:
                    _LOG.warning("telos_introspect: alignment_engine failed: %r", ex)

            rec = {
                "ts": time.time(),
                "telos_id": telos.id,
                "telos_phrase": telos.phrase,
                "lens": lens_name,
                "operator": p.operator,
                "executor_target": p.executor_target,
                "new_op_name": p.new_op_name,
                "proposed_action": p.proposed_action,
                "rationale": p.rationale,
                "paternalism_flag": p.paternalism_flag,
                "expected_alignment": p.expected_alignment,
            }
            if per_telos_audit:
                rec["alignment_per_telos"] = per_telos_audit
            if persist:
                # §2.8: il chiamante vede quante proposte sono state
                # REALMENTE persistite (dedup/anti-resurrezione possono
                # skippare). False quando persist e' disattivato a monte.
                rec["persisted"] = _persist(rec)
            results.append(rec)
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
    out = {"telos_count": 0, "proposals_total": 0,
           "persisted_total": 0, "by_telos": {}}
    for t in current():
        out["telos_count"] += 1
        props = run_for_telos(
            t, catalog=catalog, llm_invoke=llm_invoke,
            lenses=lenses, persist=persist,
        )
        out["by_telos"][t.id] = len(props)
        out["proposals_total"] += len(props)
        out["persisted_total"] += sum(1 for p in props if p.get("persisted"))
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
    from telos_loader import by_id
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
