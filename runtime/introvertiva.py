#!/usr/bin/env python3
"""introvertiva — MVP delle operazioni introvertive di Metnos.

L'introvertiva opera DA DENTRO il sistema (cron / soglia / manuale), non da
query utente. Lavora sul corpus accumulato (mnests + events + turns/jsonl)
per migliorare il catalogo invece di rispondere a un nuovo turno.

Due operazioni canoniche attive:
  - DEDUPE     ritira/consolida doppioni (replay algoritmico bonifica 30/4)
  - GENERALIZE promuove pattern ricorrente di catena → executor macro

SPECIALIZE (default-arg dominante → executor esteso) RITIRATA il 2/7/2026
per la regola dei livelli (Roberto 13/6, VINCOLANTE): «impostare un default
in una variabile» è SEMPRE compito di L0 (fastpath memoizza query→piano args
inclusi) — un executor la cui unica differenza è un arg pre-impostato duplica
L0 e inquina il catalogo. Le righe storiche in proposals_state restano
leggibili (adapter change_intent invariato); il codice vive in git.

MVP 1/5/2026 sera: identificazione + ranking + audit log JSONL append-only.
NESSUNA promozione/sintesi automatica (richiede smoke replay + manual review).

Riferimenti:
  - bacino: <install_root>/workspace/.mnestoma/mnest.sqlite (mnests + events)
  - turni:  ~/.local/share/metnos/turns/<YYYY-MM-DD>.jsonl
  - audit:  ~/.local/share/metnos/introvertiva/<op>_<ts>.jsonl
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from mnestoma import Mnestoma  # noqa: E402
import config as _C  # §7.11

TURNS_DIR = _C.PATH_USER_DATA / "turns"
AUDIT_DIR = _C.PATH_USER_DATA / "introvertiva"

# Channel da escludere di default: smoke battery + test runner.
# Generano traffico massiccio non rappresentativo dell'uso reale.
SMOKE_CHANNELS = frozenset({"test_uc", "smoke", "test", "e2e-undo-test"})


def _window_after_iso() -> str | None:
    """Confine inferiore (ISO) della finestra rolling dei generatori.

    Le proposte devono riflettere l'uso CORRENTE: senza finestra, lo scan
    ricopre TUTTA la storia dei turni e il traffico di bench/debug di mesi
    prima domina i contatori per sempre. Default 60gg = 2x l'orizzonte di
    aging executor (30gg). Env `METNOS_INTROVERTIVA_WINDOW_DAYS`, 0 = off.
    I turni senza `ts_start` restano esclusi (non possono provare recenza).
    """
    try:
        days = int(os.environ.get("METNOS_INTROVERTIVA_WINDOW_DAYS", "60"))
    except ValueError:
        days = 60
    if days <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_write(op: str, records: list[dict]) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out = AUDIT_DIR / f"{op}_{ts}.jsonl"
    with out.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def _ts_epoch(v) -> float | None:
    """Normalizza `ts_start` a epoch. Nei turni reali e' un float epoch;
    nei fixture/test una stringa ISO-Z. None se assente/non parsabile."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _load_turns(after_iso: str | None = None,
                exclude_channels: frozenset[str] = SMOKE_CHANNELS) -> list[dict]:
    """Carica TUTTI i turni dai file JSONL.

    `exclude_channels`: filtra di default i turni di smoke battery e test
    runner (channel in SMOKE_CHANNELS). Passa frozenset() per disabilitare.
    `after_iso`: confine inferiore (ISO-Z); il confronto avviene su epoch
    (`_ts_epoch`) perche' i turni reali portano ts_start float. I turni
    senza ts_start valido sono esclusi quando il confine e' attivo.
    """
    if not TURNS_DIR.exists():
        return []
    turns = []
    for fpath in sorted(TURNS_DIR.glob("*.jsonl")):
        for line in fpath.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if exclude_channels and t.get("channel") in exclude_channels:
                continue
            turns.append(t)
    if after_iso:
        cutoff = _ts_epoch(after_iso)
        if cutoff is not None:
            turns = [t for t in turns
                     if (_ts_epoch(t.get("ts_start")) or 0) >= cutoff]
    return turns


def _has_consecutive_dup(chain: tuple[str, ...]) -> bool:
    """True se la catena ha almeno una coppia X→X consecutiva (ridondanza)."""
    return any(chain[i] == chain[i + 1] for i in range(len(chain) - 1))


def _chain_from_turn(turn: dict) -> tuple[str, ...]:
    """Estrai la sequenza dei chosen_tool di un turno (catena strutturale).
    Filtra step senza tool (final_answer marker)."""
    steps = turn.get("steps") or []
    chain = []
    for s in steps:
        # Robust to dict (jsonl) o dataclass-like
        tool = (s.get("chosen_tool") if isinstance(s, dict) else
                getattr(s, "chosen_tool", "")) or ""
        if tool:
            chain.append(tool)
    return tuple(chain)


def _intent_key(turn: dict) -> str:
    """Chiave grezza dell'intent del turno: usa user_query lower-cased.
    Future: hash semantico via embedding o intent.verb+object dal log."""
    q = (turn.get("user_query") or "").lower().strip()
    return q[:80]  # trim per evitare key giganti


# --- GENERALIZE ------------------------------------------------------------

def candidates_generalize(
    *,
    min_chain_len: int = 3,
    min_uses: int = 3,
    min_distinct_intents: int = 2,
    min_avg_weight: float = 0.5,
    skip_redundant_patterns: bool = True,
    limit: int = 20,
) -> list[dict]:
    """Identifica catene candidate alla promozione a executor macro.

    Algoritmo:
      1. Carica tutti i turni (ts_start ASC), estrae catena = tuple(chosen_tool).
      2. Filtra catene con len >= min_chain_len.
      3. Counter su catene → frequenza.
      4. Per ogni catena candidata: misura intent diversity + avg_weight (mnest).
      5. Filtra: uses >= min_uses AND distinct_intents >= min_distinct_intents
                AND avg_weight >= min_avg_weight.
      6. Ranking by score = uses * avg_weight, top `limit`.

    Output: list[dict] con campi:
      - pattern: tuple[str] — sequenza executor
      - uses: int — quante volte la catena e' apparsa
      - distinct_intents: int — quante query semanticamente diverse
      - avg_weight: float — media weight dei mnest della catena
      - score: float — uses * avg_weight (per ranking)
      - sample_intents: list[str] — fino a 3 query rappresentative
    """
    turns = _load_turns(after_iso=_window_after_iso())
    if not turns:
        return []

    # 4/5/2026 ADR 0077: filtro deterministico contro pattern che riferiscono
    # executor non piu' nel catalog (es. fetch_urls rimosso 3/5). Senza
    # questo, catene legacy continuano a generare proposte morte.
    from loader import load_catalog
    cat_names = {e.name for e in load_catalog()}
    # Universal helpers + builtin verb-unique sono "tool del runtime"
    # (non file in <install_root>/executors/) ma sono validi nei pattern.
    cat_names.update({
        "filter_entries", "sort_entries", "compute_entries", "undo_last_turn",
        "describe_entries", "classify_entries",
        "admin", "sudoer", "request_new_executor",
    })

    # 1-2. Estrai catene + filtra per len + (opt) skip ridondanti X→X.
    # Le catene con duplicati consecutivi (sort_entries→sort_entries) sono
    # tipicamente bug del PLANNER mascherati da pattern, non candidati a
    # promozione. Vengono separate in `redundant_patterns` (output diagnostic)
    # invece di essere proposte come macro.
    chain_to_intents: dict[tuple, list[str]] = defaultdict(list)
    redundant_chains: dict[tuple, int] = defaultdict(int)
    skipped_obsolete = 0
    for t in turns:
        chain = _chain_from_turn(t)
        if len(chain) < min_chain_len:
            continue
        # Skip catene che includono executor non piu' presenti nel catalog.
        if any(tool not in cat_names for tool in chain):
            skipped_obsolete += 1
            continue
        if skip_redundant_patterns and _has_consecutive_dup(chain):
            redundant_chains[chain] += 1
            continue
        chain_to_intents[chain].append(_intent_key(t))

    # 3. Counter implicito (len di chain_to_intents[c])
    # 4. Calcola weight medio: serve mnest weight per ogni transizione
    #    (executor[i], executor[i+1]).
    mn = Mnestoma()
    cands = []
    for chain, intents in chain_to_intents.items():
        uses = len(intents)
        if uses < min_uses:
            continue
        distinct = len(set(intents))
        if distinct < min_distinct_intents:
            continue
        # Avg weight: itera transizioni della catena
        weights = []
        for i in range(len(chain) - 1):
            src, dst = chain[i], chain[i + 1]
            row = mn.conn.execute(
                """SELECT weight FROM mnests
                   WHERE src_executor = ? AND dst_executor = ? AND state = 'active'
                   ORDER BY weight DESC LIMIT 1""",
                (src, dst),
            ).fetchone()
            if row is not None:
                weights.append(row["weight"])
        if not weights:
            continue
        avg_w = sum(weights) / len(weights)
        if avg_w < min_avg_weight:
            continue
        cands.append({
            "pattern": list(chain),
            "uses": uses,
            "distinct_intents": distinct,
            "avg_weight": round(avg_w, 3),
            "score": round(uses * avg_w, 3),
            "sample_intents": list(set(intents))[:3],
        })

    cands.sort(key=lambda c: -c["score"])
    out = cands[:limit]
    # Annota redundant patterns separatamente — non promozioni ma signal
    # diagnostico per fix prompt PLANNER.
    if redundant_chains:
        out.append({
            "_kind": "diagnostic",
            "note": "redundant patterns (X→X consecutive) skipped: tipicamente bug del PLANNER, non candidati a macro",
            "redundant_patterns": [
                {"pattern": list(p), "uses": n}
                for p, n in sorted(redundant_chains.items(), key=lambda kv: -kv[1])[:10]
            ],
        })
    return out


# --- SPECIALIZE ------------------------------------------------------------

_FLOW_ARGS = frozenset({
    # Args di pipeline: ricevono valori al runtime tramite from_step o
    # placeholder template. Non hanno senso come "valori costanti utente"
    # da specializzare (4/5/2026, ADR 0077).
    "entries", "from_step", "results",
})


def candidates_dedupe(*, min_uses: int = 1) -> list[dict]:
    """Identifica mnest candidati a dedupe (rename / merge / cleanup).

    MVP: solo segnalazione, no execute. Tre famiglie:
      - mnest legacy con executor rinominati (require manifest superseded_by)
      - mnest deprecated piu' giovani del TTL
      - proto orfani (state='proto' AND uses<=1 AND age>30d)

    NB: replay algoritmico completo della bonifica 30/4 non implementato qui:
    richiede mapping legacy→corrente che oggi e' empirico (web_fetch ↔
    get_urls, list_dir ↔ list_dirs, find_file ↔ find_files), non
    derivabile univocamente dal manifest. Da estendere quando manifest
    superseded_by sara' pervasivo.
    """
    mn = Mnestoma()
    cands = []
    # Famiglia 1: mnest con src/dst non in catalog (legacy)
    from loader import load_catalog
    cat = load_catalog()
    catalog_names = {e.name for e in cat}
    for r in mn.conn.execute(
        "SELECT id, src_executor, dst_executor, uses, weight, state FROM mnests "
        "WHERE state = 'active'"
    ):
        src_orphan = r["src_executor"] not in catalog_names
        dst_orphan = r["dst_executor"] not in catalog_names
        if src_orphan or dst_orphan:
            cands.append({
                "kind": "legacy_orphan",
                "mnest_id": r["id"],
                "src_executor": r["src_executor"],
                "dst_executor": r["dst_executor"],
                "uses": r["uses"],
                "weight": r["weight"],
                "src_in_catalog": not src_orphan,
                "dst_in_catalog": not dst_orphan,
            })
    return cands


# --- Diff fra audit log (signal long-period) -----------------------------

def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _candidate_key(rec: dict, op: str) -> str:
    """Chiave stabile per matchare candidati fra run distinti."""
    if op == "generalize":
        return "→".join(rec.get("pattern", []))
    if op == "specialize":
        return f"{rec.get('executor')}::{rec.get('arg_name')}::{rec.get('dominant_value')}"
    if op == "dedupe":
        return f"{rec.get('mnest_id')}::{rec.get('kind')}"
    return json.dumps(rec, sort_keys=True)


def diff_audit(op: str) -> dict:
    """Confronta gli ULTIMI DUE audit log dell'op (cronologia introvertiva
    long-period). Output: {added, removed, persisted, grew, shrunk}.

    `added`: candidati comparsi solo nell'ultimo run.
    `removed`: candidati nell'avant-ultimo, scomparsi nell'ultimo.
    `persisted`: candidati in entrambi (= pattern stabili nel tempo).
    `grew/shrunk`: persisted con metric (uses o dominance) diversa.
    """
    if not AUDIT_DIR.exists():
        return {"error": f"audit dir non esiste: {AUDIT_DIR}"}
    files = sorted(AUDIT_DIR.glob(f"candidates_{op}_*.jsonl"))
    if len(files) < 2:
        return {
            "error": f"servono almeno 2 audit log per '{op}', trovati {len(files)}",
            "files": [str(f.name) for f in files],
        }
    prev_recs = _read_audit(files[-2])
    curr_recs = _read_audit(files[-1])
    prev_map = {_candidate_key(r, op): r for r in prev_recs
                if "_kind" not in r}
    curr_map = {_candidate_key(r, op): r for r in curr_recs
                if "_kind" not in r}
    added = [curr_map[k] for k in (curr_map.keys() - prev_map.keys())]
    removed = [prev_map[k] for k in (prev_map.keys() - curr_map.keys())]
    persisted_keys = curr_map.keys() & prev_map.keys()
    grew, shrunk, stable = [], [], []
    metric = "uses" if op == "generalize" else "total_uses"
    for k in persisted_keys:
        p, c = prev_map[k], curr_map[k]
        pm, cm = p.get(metric, 0), c.get(metric, 0)
        if cm > pm:
            grew.append({"key": k, "prev": pm, "curr": cm, "delta": cm - pm})
        elif cm < pm:
            shrunk.append({"key": k, "prev": pm, "curr": cm, "delta": cm - pm})
        else:
            stable.append({"key": k, "uses": cm})
    return {
        "op": op,
        "prev_run": files[-2].name,
        "curr_run": files[-1].name,
        "n_added": len(added), "added": added[:10],
        "n_removed": len(removed), "removed": removed[:10],
        "n_persisted": len(persisted_keys),
        "n_grew": len(grew), "grew": sorted(grew, key=lambda x: -x["delta"])[:10],
        "n_shrunk": len(shrunk), "shrunk": sorted(shrunk, key=lambda x: x["delta"])[:5],
        "n_stable": len(stable),
    }


# --- Orchestrator ----------------------------------------------------------

def _sig_key_for(op: str, cand: dict):
    """Chiave canonica di un candidato per proposals_state.

    Le shape sono il CONTRATTO con lo storico del DB e con l'adapter
    change_intent (`change_intent_adapters/introvertiva.py`):
      dedupe     → ["dedupe", reason, src, dst]
      generalize → ["generalize", [chain]]
    (specialize → ["specialize", executor, arg, valore] esiste SOLO come
    shape storica nel DB: il generatore è ritirato, l'adapter la legge.)
    Ritorna None per record non candidabili (diagnostici, campi mancanti).
    """
    if op == "dedupe":
        a, b = cand.get("src_executor"), cand.get("dst_executor")
        if not a or not b:
            return None
        return ["dedupe", cand.get("kind", ""), a, b]
    if op == "generalize":
        pattern = cand.get("pattern") or []
        if not pattern:
            return None
        return ["generalize", list(pattern)]
    return None


def sync_proposals_state(out: dict) -> dict:
    """Proietta i candidati di un run in `proposals_state` (touch_or_insert).

    E' il passo che tiene VIVO il lifecycle pending→dormant→riemersione e la
    vista /admin/changes: senza, i generatori scrivono solo audit JSONL che
    nessuno consuma. Chiamato dal task notturno `introvertiva_propose`.
    Ritorna i conteggi per operazione.
    """
    import proposals_state as ps
    counts: dict[str, int] = {}
    for op in ("dedupe", "generalize"):
        n = 0
        for cand in out.get(op) or []:
            if not isinstance(cand, dict) or cand.get("_kind"):
                continue  # record diagnostici, non candidati
            key = _sig_key_for(op, cand)
            if key is None:
                continue
            uses = int(cand.get("uses") or cand.get("total_uses") or 0)
            ps.touch_or_insert(key, op, uses)
            n += 1
        counts[op] = n
    return counts


def run_all(*, audit: bool = True) -> dict:
    """Esegue le ops attive (dedupe, generalize), summary + audit JSONL."""
    out = {
        "ts": int(time.time()),
        "dedupe": candidates_dedupe(),
        "generalize": candidates_generalize(),
    }
    if audit:
        for op in ("dedupe", "generalize"):
            if out[op]:
                p = _audit_write(f"candidates_{op}", out[op])
                out[f"{op}_audit"] = str(p)
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("op", choices=["dedupe", "generalize", "all", "diff"])
    p.add_argument("--no-audit", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--diff-op", choices=["dedupe", "generalize"],
                   help="Per `op=diff`: quale operazione confrontare.")
    args = p.parse_args()
    if args.op == "diff":
        if not args.diff_op:
            print("--diff-op richiesto per `op=diff`", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(diff_audit(args.diff_op), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.op == "all":
        r = run_all(audit=not args.no_audit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        fn = {"dedupe": candidates_dedupe,
              "generalize": candidates_generalize}[args.op]
        r = fn() if args.op == "dedupe" else fn(limit=args.limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if not args.no_audit and r:
            ap = _audit_write(f"candidates_{args.op}", r)
            print(f"\naudit: {ap}", file=sys.stderr)
