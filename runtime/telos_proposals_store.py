# SPDX-License-Identifier: AGPL-3.0-only
"""telos_proposals_store.py — Store proposte telos engine + decisioni admin.

Read-only sui proposals JSONL (cui scrivono lenti + AlignmentEngine),
append-only sulle decisioni admin in `telos_decisions.jsonl`.

Determinismo §7.9: nessun LLM, nessuna logica fuzzy. Filtri sono predicati
puri. ID proposta = `ts` (timestamp float, granularita' microsecondi:
collisione virtualmente impossibile per le sole proposte introspettive
notturne; cluster batch backfill via scrittura sequenziale).

API pubblica:
    load_all(*, min_alignment=0.0, lens=None, telos_id=None,
             max_rows=500) -> list[dict]
    apply_decision(prop_id, action, by="admin") -> dict
    decisions_index() -> dict[str, dict]
    proposals_count() -> int

Sorgenti file (path canonical, dataclass-free per leggerezza):
- INPUT: `~/.local/share/metnos/telos_proposals.rescored.recomposed.jsonl`
  (output di `alignment_engine --recompose`). Se manca, fallback al
  file `.rescored.jsonl` (pre-v1.3) o all'originale `telos_proposals.jsonl`
  (proposte con `expected_alignment=0`).
- OUTPUT: `~/.local/share/metnos/telos_decisions.jsonl`
  Schema: {"prop_id": "<ts>", "action": "accept|reject|stage", "ts": float, "by": str}
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

_DATA_DIR = Path.home() / ".local" / "share" / "metnos"
_PROPOSALS_CANDIDATES = (
    _DATA_DIR / "telos_proposals.rescored.recomposed.jsonl",
    _DATA_DIR / "telos_proposals.rescored.jsonl",
    _DATA_DIR / "telos_proposals.jsonl",
)
DECISIONS_PATH = _DATA_DIR / "telos_decisions.jsonl"

_VALID_ACTIONS = frozenset({"accept", "reject", "stage"})


def _resolve_proposals_path() -> Optional[Path]:
    for cand in _PROPOSALS_CANDIDATES:
        if cand.is_file():
            return cand
    return None


def proposals_count() -> int:
    """Conteggio totale proposte nel file piu' aggiornato (no decision filter)."""
    p = _resolve_proposals_path()
    if p is None:
        return 0
    with p.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _format_prop_id(ts: float) -> str:
    """ts float → stringa stabile per URL/decision key. 6 decimali = us."""
    return f"{ts:.6f}"


def load_all(
    *,
    min_alignment: float = 0.0,
    lens: Optional[str] = None,
    telos_id: Optional[str] = None,
    max_rows: int = 500,
    include_decided: bool = True,
    enrich_rows: bool = False,
) -> list[dict]:
    """Carica proposte con filtri, ordina per expected_alignment desc.

    Ogni record include:
    - `prop_id` (string da `ts`, usato come PK per decisioni)
    - `decision` (dict | None) se decisa, da `telos_decisions.jsonl`
    - tutti gli altri campi dal JSONL sorgente

    `include_decided=False` filtra le proposte gia' accept/reject (mostra
    solo pending + stage).
    """
    p = _resolve_proposals_path()
    if p is None:
        return []
    decisions = decisions_index()
    out: list[dict] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            ea = rec.get("expected_alignment", 0.0)
            try:
                ea = float(ea)
            except (TypeError, ValueError):
                ea = 0.0
            if ea < min_alignment:
                continue
            if lens and rec.get("lens") != lens:
                continue
            if telos_id and rec.get("telos_id") != telos_id:
                continue
            prop_id = _format_prop_id(float(ts))
            dec = decisions.get(prop_id)
            if not include_decided and dec and dec.get("action") in ("accept", "reject"):
                continue
            rec["prop_id"] = prop_id
            rec["decision"] = dec
            rec["expected_alignment"] = ea
            out.append(rec)
    out.sort(key=lambda r: -r.get("expected_alignment", 0.0))
    out = out[:max_rows]
    if enrich_rows and out:
        turns = _load_turns()
        for rec in out:
            enrich(rec, turns)
    return out


def decisions_index() -> dict[str, dict]:
    """Ultima decisione per prop_id (LWW: last-write-wins).

    Append-only file → l'ultimo record per chiave vince. Stage non e' uno
    stato terminale: una proposta stage puo' essere riaccettata/rifiutata.
    """
    if not DECISIONS_PATH.is_file():
        return {}
    idx: dict[str, dict] = {}
    with DECISIONS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = d.get("prop_id")
            if pid:
                idx[pid] = d  # LWW
    return idx


def apply_decision(prop_id: str, action: str, by: str = "admin") -> dict:
    """Appende una decisione al file. Ritorna il record persistito.

    Raises ValueError per action invalida. Non valida prop_id contro il
    set delle proposte (le decisioni sono append-only, l'orphan check
    e' compito dell'UI di visualizzazione).
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be in {sorted(_VALID_ACTIONS)}, got {action!r}")
    rec = {
        "prop_id": prop_id,
        "action": action,
        "ts": time.time(),
        "by": by,
    }
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DECISIONS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# --- Enrichment (turn log + rationale parsing) --------------------------------
#
# Per ogni proposta arricchiamo con i campi che il design richiede:
# - `example_query` (str|None): una user_query reale dal turn log che ha
#   invocato `executor_target` nella catena tools.
# - `current_path` (list[str]): la sequenza `chosen_tool` del turno trovato.
# - `current_latency_ms` (int|None): wall-time del turno (LLM+exec+intent
#   sommati per step).
# - `new_path_estimated` (list[str]): stima del path se la proposta venisse
#   adottata. Default minimo: `[executor_target]` (singolo step pipelined).
# - `latency_saved_ms_est` (int|None): differenza stimata. Assume mediana
#   per-step ~5500ms (LLM 4-5s + exec 0-1s + intent 1.6s, dalla telemetria).
# - `n_observed` (int|None): conteggio osservazioni dal rationale (regex
#   "N volte" / "N occorrenze").
#
# Determinismo §7.9: parsing regex + lookup file. No LLM, no fuzzy match.

_TURN_LOG_DIR = _DATA_DIR / "turns"
_TURN_LOG_CACHE: dict[Path, list[dict]] = {}
_TURN_LOG_CACHE_MTIME: dict[Path, float] = {}
_TURN_LOG_MAX_FILES = 14  # ~2 settimane di storico, evita scan illimitato

# Mediana per-step osservata in produzione (telemetria 22/5/2026 ~9s LLM
# + 0-1s exec + 1.6s intent_extractor). Usato per stima saving.
_PER_STEP_LATENCY_MS_MEDIAN = 5500

_RATIONALE_NUMBER_RE = re.compile(
    r"(\d+)\s*(?:volte|occorrenze|times|co-?attiv)", re.IGNORECASE
)

# Tool names sono `verbo_oggetto` o `verbo_oggetto_qualifier` (§2.2). Pattern
# safe: 2-4 token snake_case di lunghezza ragionevole.
_TOOL_NAME_RE = re.compile(r"\b([a-z][a-z0-9]+(?:_[a-z][a-z0-9]+){1,3})\b")
# Falsi positivi comuni da escludere (parole composte non-tool del prompt IT).
_TOOL_NAME_BLACKLIST = frozenset({
    "dei_documenti", "del_sistema", "lista_di", "lista_paths",
    "lista_della", "una_pipeline", "uno_step", "valore_aggiunto",
    "tutti_i", "non_e", "che_non", "dal_telos", "telos_id",
    "telos_phrase", "alignment_per", "expected_alignment",
    "paternalism_flag", "proposed_action", "executor_target",
    "is_a", "ti_aiuta", "to_calendar", "to_action",
})


def _extract_tool_mentions(*texts: str) -> list[str]:
    """Estrae i nomi-tool menzionati nei testi (dedup, ordine di apparizione).

    NON valida contro il catalog runtime (overhead): la blacklist filtra i
    falsi positivi comuni; eventuali nomi orphan finiscono nella lista ma
    non causano errori (lookup nel turn log restituisce zero match).
    """
    seen = set()
    out = []
    for txt in texts:
        if not txt:
            continue
        for m in _TOOL_NAME_RE.finditer(txt):
            name = m.group(1)
            if name in _TOOL_NAME_BLACKLIST:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def _load_turns(max_files: int = _TURN_LOG_MAX_FILES) -> list[dict]:
    """Carica tutti i turni dai file *.jsonl piu' recenti. Cache mtime-based.

    Restituisce flat list (ordine = piu' recente prima, file per file).
    """
    if not _TURN_LOG_DIR.is_dir():
        return []
    files = sorted(_TURN_LOG_DIR.glob("*.jsonl"), reverse=True)[:max_files]
    out: list[dict] = []
    for fp in files:
        mtime = fp.stat().st_mtime
        if _TURN_LOG_CACHE.get(fp) and _TURN_LOG_CACHE_MTIME.get(fp) == mtime:
            out.extend(_TURN_LOG_CACHE[fp])
            continue
        records = []
        try:
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
        _TURN_LOG_CACHE[fp] = records
        _TURN_LOG_CACHE_MTIME[fp] = mtime
        out.extend(records)
    return out


def _step_latency_ms(step: dict) -> int:
    """Wall-time stimato di uno step: LLM + exec + intent + prefilter."""
    fields = ("llm_latency_ms", "exec_ms", "intent_ms",
              "prefilter_ms", "vaglio_ms", "rerank_ms")
    total = 0
    for f in fields:
        v = step.get(f)
        if isinstance(v, (int, float)):
            total += int(v)
    return total


def _find_example_turn(
    target: str,
    related_tools: list[str],
    turns: list[dict],
):
    """Trova il turno piu' rilevante che illustra la proposta.

    Ritorna: (turn|None, pipeline_observed: bool).
    Priorita': (1) turno che contiene TUTTI i related_tools (pipeline
    osservata, pipeline_observed=True); (2) turno con piu' alto overlap;
    (3) primo turno con il target (pipeline_observed=False).
    """
    if not turns:
        return None, False
    candidates: list[tuple[int, dict]] = []  # (overlap, turn)
    for t in turns:
        steps = t.get("steps") or []
        chosen = {s.get("chosen_tool") for s in steps if s.get("chosen_tool")}
        if not chosen:
            continue
        overlap = sum(1 for tool in related_tools if tool in chosen)
        if related_tools and overlap == len(related_tools):
            return t, True  # match perfetto: pipeline_observed
        if target and target in chosen:
            candidates.append((overlap, t))
    if not candidates:
        return None, False
    candidates.sort(key=lambda kv: -kv[0])
    return candidates[0][1], False


def _path_from_turn(turn: dict) -> list[str]:
    """Catena di `chosen_tool` di un turno, in ordine."""
    return [s.get("chosen_tool") for s in (turn.get("steps") or [])
            if s.get("chosen_tool")]


def _turn_total_latency_ms(turn: dict) -> int:
    """Somma wall-time per-step dell'intero turno."""
    return sum(_step_latency_ms(s) for s in (turn.get("steps") or []))


def _parse_n_observed(rationale: str) -> Optional[int]:
    if not rationale:
        return None
    m = _RATIONALE_NUMBER_RE.search(rationale)
    return int(m.group(1)) if m else None


def enrich(prop: dict, turns: Optional[list[dict]] = None) -> dict:
    """Arricchisce una proposta con campi UI. Mutates+returns `prop`.

    Lookup linear: piu' di 100 proposte da arricchire? Pre-carica `turns`
    una volta sola e passa.
    """
    if turns is None:
        turns = _load_turns()
    target = prop.get("executor_target") or ""
    rationale = prop.get("rationale") or ""
    proposed = prop.get("proposed_action") or ""

    prop["n_observed"] = _parse_n_observed(rationale)
    # I tool menzionati nella proposta = pipeline da sostituire con `target`.
    mentions = _extract_tool_mentions(proposed, rationale)
    # Target deve essere nel set per il matching ma puo' essere implicito.
    related_tools = [t for t in mentions if t != target]
    prop["pipeline_tools_mentioned"] = mentions

    if target or related_tools:
        turn, pipeline_observed = _find_example_turn(target, related_tools, turns)
    else:
        turn, pipeline_observed = None, False
    prop["pipeline_observed"] = pipeline_observed
    if turn is not None:
        cur_path = _path_from_turn(turn)
        cur_lat = _turn_total_latency_ms(turn)
        # Stima new_path: sostituisco i related_tools (e il target se gia'
        # presente) con UN singolo step `target`. Mantengo gli step non
        # correlati (es. get_now iniziale, final_answer chiusura).
        to_replace = set(related_tools)
        if target:
            to_replace.add(target)
        new_path: list[str] = []
        replaced_block = False
        for step in cur_path:
            if step in to_replace:
                if not replaced_block:
                    new_path.append(target or step)
                    replaced_block = True
                # else: skip (collassiamo il blocco)
            else:
                new_path.append(step)
                replaced_block = False  # reset: blocco interrotto
        saved_steps = max(0, len(cur_path) - len(new_path))
        prop["example_query"] = turn.get("user_query")
        prop["current_path"] = cur_path
        prop["current_latency_ms"] = cur_lat
        prop["new_path_estimated"] = new_path
        prop["latency_saved_ms_est"] = saved_steps * _PER_STEP_LATENCY_MS_MEDIAN
    else:
        prop["example_query"] = None
        prop["current_path"] = []
        prop["current_latency_ms"] = None
        prop["new_path_estimated"] = [target] if target else []
        prop["latency_saved_ms_est"] = None
    return prop


def stats() -> dict:
    """Aggregati per dashboard summary card."""
    total = proposals_count()
    decisions = decisions_index()
    by_action = {"accept": 0, "reject": 0, "stage": 0}
    for d in decisions.values():
        a = d.get("action")
        if a in by_action:
            by_action[a] += 1
    pending = max(0, total - by_action["accept"] - by_action["reject"])
    return {
        "total": total,
        "accepted": by_action["accept"],
        "rejected": by_action["reject"],
        "staged": by_action["stage"],
        "pending": pending,
    }
