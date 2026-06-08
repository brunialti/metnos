#!/usr/bin/env python3
"""introvertiva — MVP delle operazioni introvertive di Metnos.

L'introvertiva opera DA DENTRO il sistema (cron / soglia / manuale), non da
query utente. Lavora sul corpus accumulato (mnests + events + turns/jsonl)
per migliorare il catalogo invece di rispondere a un nuovo turno.

Tre operazioni canoniche (Roberto 30/4/2026):
  - DEDUPE     ritira/consolida doppioni (replay algoritmico bonifica 30/4)
  - GENERALIZE promuove pattern ricorrente di catena → executor macro
  - SPECIALIZE estrae varianti mirate da analisi args ricorrenti

MVP 1/5/2026 sera: identificazione + ranking + audit log JSONL append-only.
NESSUNA promozione/sintesi automatica (richiede smoke replay + manual review).

Riferimenti:
  - bacino: <install_root>/workspace/.mnestoma/mnest.sqlite (mnests + events)
  - turni:  ~/.local/share/metnos/turns/<YYYY-MM-DD>.jsonl
  - audit:  ~/.local/share/metnos/introvertiva/<op>_<ts>.jsonl
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
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


def _audit_write(op: str, records: list[dict]) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out = AUDIT_DIR / f"{op}_{ts}.jsonl"
    with out.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def _load_turns(after_iso: str | None = None,
                exclude_channels: frozenset[str] = SMOKE_CHANNELS) -> list[dict]:
    """Carica TUTTI i turni dai file JSONL.

    `exclude_channels`: filtra di default i turni di smoke battery e test
    runner (channel in SMOKE_CHANNELS). Passa frozenset() per disabilitare.
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
        turns = [t for t in turns if t.get("ts_start", "") >= after_iso]
    return turns


def _manifest_defaults(catalog) -> dict[str, dict]:
    """{executor_name: {arg_name: default_value}} per skip-if-default in
    specialize. Estrae da args_schema.properties.<name>.default."""
    out = {}
    for ex in catalog:
        props = (ex.args_schema or {}).get("properties") or {}
        defaults = {k: v.get("default") for k, v in props.items()
                    if isinstance(v, dict) and "default" in v}
        if defaults:
            out[ex.name] = defaults
    return out


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
    turns = _load_turns()
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


def _is_template_value(val_str: str) -> bool:
    """True se il valore e' un placeholder runtime (`{{stepN.field}}`,
    `{{var}}`) — non specializzabile come costante."""
    if not isinstance(val_str, str):
        return False
    s = val_str.strip().strip('"\'')
    return "{{" in s and "}}" in s


def _is_valid_proposed_name(name: str) -> bool:
    """Validator deterministico del proposed_name di una specialize.

    Regola the design guide §2.2: `azione_oggetto[_qualifier]`. Vocabolario CHIUSO
    (vocab.ACTIONS x vocab.OBJECTS). 4/5/2026 ADR 0077.

    Rifiuta:
      - nomi senza underscore o con doppio/triplo underscore
      - verbo non in ACTIONS
      - oggetto non in OBJECTS
      - qualifier che inizia con cifra o e' una stringa booleana (`True`/`False`)
      - qualifier con caratteri non identifier-friendly
    """
    if not name or "__" in name or name.startswith("_") or name.endswith("_"):
        return False
    parts = name.split("_")
    if len(parts) < 2:
        return False
    try:
        from vocab import ACTIONS, OBJECTS
    except Exception:
        return True  # fallback permissivo se vocab non importabile
    verb, obj = parts[0], parts[1]
    if verb not in ACTIONS:
        return False
    if obj not in OBJECTS:
        return False
    qualifier = "_".join(parts[2:]) if len(parts) > 2 else ""
    if qualifier:
        if qualifier[0].isdigit():
            return False
        if qualifier in ("True", "False", "true", "false"):
            return False
        if not all(c.isalnum() or c == "_" for c in qualifier):
            return False
        if qualifier != qualifier.lower():
            return False
    return True


def candidates_specialize(
    *,
    min_uses: int = 10,
    min_arg_dominance: float = 0.6,
    limit: int = 20,
    only_active_catalog: bool = True,
    skip_if_matches_default: bool = True,
) -> list[dict]:
    """Identifica args ricorrenti negli step di un executor → candidato a
    variante specializzata.

    Algoritmo:
      1. Per ogni executor, raccogli tutti i raw_args usati nei turni (degli
         step con quel chosen_tool).
      2. Per ogni arg name (es. 'pattern', 'op', 'qualifier'), conta i valori.
      3. Se UN valore copre >= min_arg_dominance del totale (es. 70% delle
         chiamate find_files hanno pattern='*.py'), proponi specialize.
      4. Filtra: total_uses >= min_uses.

    Output: list[dict]:
      - executor: str
      - arg_name: str
      - dominant_value: str (json-serialized)
      - dominance: float (0-1)
      - total_uses: int
      - proposed_name: str (suggerimento naming)
    """
    turns = _load_turns()
    if not turns:
        return []
    # Catalog filter: scarta executor che non esistono piu' (legacy come
    # fs_write, web_fetch rimasti in turns/jsonl di aprile).
    # Default-aware filter: scarta arg=valore se valore == default del
    # manifest (es. get_now timezone=Europe/Rome quando Europe/Rome e'
    # gia' default — non e' specialize utile, e' "tutti usano il default").
    catalog_names: set[str] = set()
    manifest_defaults: dict[str, dict] = {}
    if only_active_catalog or skip_if_matches_default:
        from loader import load_catalog
        cat = load_catalog()
        catalog_names = {e.name for e in cat}
        if skip_if_matches_default:
            manifest_defaults = _manifest_defaults(cat)
    # tool → arg_name → Counter(value)
    tool_args: dict[str, dict[str, Counter]] = defaultdict(
        lambda: defaultdict(Counter))
    for t in turns:
        for s in (t.get("steps") or []):
            tool = (s.get("chosen_tool") if isinstance(s, dict) else
                    getattr(s, "chosen_tool", "")) or ""
            if not tool:
                continue
            if only_active_catalog and tool not in catalog_names:
                continue
            raw = (s.get("raw_args") if isinstance(s, dict) else
                   getattr(s, "raw_args", {})) or {}
            for k, v in raw.items():
                # Skip flow args (entries, from_step, results) — sono slot di
                # pipeline, non costanti utente specializzabili.
                if k in _FLOW_ARGS:
                    continue
                # Serializza valore (skip scelte ovviamente troppo varianti)
                if isinstance(v, (str, int, float, bool)):
                    val = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, list) and len(v) == 1 and isinstance(v[0], (str, int, float)):
                    val = json.dumps(v, ensure_ascii=False)
                else:
                    continue  # dict/list-multi non ammessi al MVP
                # Skip placeholder template `{{stepN.field}}` (runtime value).
                if _is_template_value(val):
                    continue
                tool_args[tool][k][val] += 1

    cands = []
    for tool, args_dict in tool_args.items():
        tool_defaults = manifest_defaults.get(tool, {})
        for arg_name, val_counter in args_dict.items():
            total = sum(val_counter.values())
            if total < min_uses:
                continue
            top_val, top_count = val_counter.most_common(1)[0]
            dominance = top_count / total
            if dominance < min_arg_dominance:
                continue
            # Skip-if-default: se il valore dominante coincide con il default
            # del manifest, non e' specialize candidate ma "tutti usano il
            # default" — informazione gia' codificata nel manifest stesso.
            try:
                v_obj = json.loads(top_val)
            except (json.JSONDecodeError, ValueError):
                v_obj = top_val
            if skip_if_matches_default and arg_name in tool_defaults:
                if v_obj == tool_defaults[arg_name]:
                    continue
            # Skip booleani: tipicamente val == default (gia' filtrato sopra)
            # oppure il proposed_name finisce in `_True`/`_False` che viola
            # il vocabolario (the design guide §2.2). Niente informazione utile.
            if isinstance(v_obj, bool):
                continue
            slug = str(v_obj).strip("[]\"' ").replace("*", "").replace(".", "_")
            slug = "".join(c if (c.isalnum() or c == "_") else "_"
                           for c in slug)[:20]
            # Collassa multipli underscore di seguito a uno (slug puliti)
            while "__" in slug:
                slug = slug.replace("__", "_")
            slug = slug.strip("_")
            proposed = f"{tool}_{slug}" if slug else tool
            # Validator deterministico: rifiuta proposed_name che violano
            # naming convention (vocab chiuso + qualifier ben formato).
            if not _is_valid_proposed_name(proposed):
                continue
            cands.append({
                "executor": tool,
                "arg_name": arg_name,
                "dominant_value": top_val,
                "dominance": round(dominance, 3),
                "total_uses": total,
                "proposed_name": proposed,
            })

    cands.sort(key=lambda c: -c["dominance"] * c["total_uses"])
    return cands[:limit]


# --- DEDUPE (placeholder per replay algoritmico bonifica 30/4) ------------

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

def run_all(*, audit: bool = True) -> dict:
    """Esegue tutte e 3 le ops, ritorna summary + scrive audit JSONL per ognuna."""
    out = {
        "ts": int(time.time()),
        "dedupe": candidates_dedupe(),
        "generalize": candidates_generalize(),
        "specialize": candidates_specialize(),
    }
    if audit:
        for op in ("dedupe", "generalize", "specialize"):
            if out[op]:
                p = _audit_write(f"candidates_{op}", out[op])
                out[f"{op}_audit"] = str(p)
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("op", choices=["dedupe", "generalize", "specialize", "all", "diff"])
    p.add_argument("--no-audit", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--diff-op", choices=["dedupe", "generalize", "specialize"],
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
              "generalize": candidates_generalize,
              "specialize": candidates_specialize}[args.op]
        r = fn() if args.op == "dedupe" else fn(limit=args.limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if not args.no_audit and r:
            ap = _audit_write(f"candidates_{args.op}", r)
            print(f"\naudit: {ap}", file=sys.stderr)
